"""Rank jobs for each search profile in profiles.toml.

1. Filter (SQL, cheap): open jobs you could actually take, by route:
     remote = doable from Pakistan (yes / maybe)
     move   = offers visa or relocation, or the company is on a UK/NL visa sponsor list (and the post doesn't refuse)
   plus region and title-word filters.
2. Rank (embeddings, on this PC): how close each job is to what you're looking for, in meaning, not just words
   ("C# backend" is close to ".NET API developer"). Job vectors are cached, so only new jobs are embedded.
3. Save the top jobs per profile to the `matches` table and print them.

Your profile text never leaves this PC. Run: uv run python -m job_radar.match
"""
import re
import sys
import time
import tomllib
from datetime import date
from pathlib import Path

import numpy as np

from job_radar import check, places, store

PROFILES = Path("profiles.toml")
MODEL = "BAAI/bge-small-en-v1.5"  # small (130 MB), English, runs on CPU; 384 numbers per text
# Not a score you can read as "%": cosine similarity between texts. Small boosts move clear wins up a little.
BOOSTS = {"from Pakistan: yes": 0.04, "visa: yes": 0.03, "relocation: yes": 0.02, "sponsor list": 0.01}

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (job_id TEXT PRIMARY KEY, model TEXT, vector BLOB);
CREATE TABLE IF NOT EXISTS matches (
    profile TEXT, rank INTEGER, job_id TEXT, score REAL, similarity REAL, why TEXT, matched_on TEXT,
    PRIMARY KEY (profile, job_id)
);"""

ROUTES = {
    "remote": "e.from_pakistan IN ('yes', 'maybe')",
    "move": "e.visa != 'no' AND (e.visa = 'yes' OR e.relocation = 'yes' OR COALESCE(c.sponsor_in, '') != '')",
}


def job_text(title: str, department: str, description: str) -> str:
    """What gets embedded: the title says most; the start of the description adds the stack/domain."""
    return f"{title}. {department or ''}. {(description or '')[:600]}"


def candidates(db, profile: dict) -> list[tuple]:
    routes = " OR ".join(f"({ROUTES[r]})" for r in profile.get("routes", ROUTES))
    sql = f"""SELECT j.id, j.title, j.department, j.description, j.company, j.location, j.url,
                     e.from_pakistan, e.visa, e.relocation, e.regions, COALESCE(c.sponsor_in, ''), COALESCE(c.size, '')
              FROM jobs j JOIN eligibility e ON e.job_id = j.id LEFT JOIN companies c ON c.board = j.board
              WHERE j.closed_on IS NULL AND ({routes})
                AND j.id NOT IN (SELECT job_id FROM tracker WHERE status != 'saved')"""
    # applied / rejected / hidden jobs leave the match list (they live on the tracker page), so it refills
    rows = db.execute(sql).fetchall()
    regions = set(profile.get("regions", []))
    any_words = [w.lower() for w in profile.get("title_any", [])]
    none_words = [w.lower() for w in profile.get("title_none", [])]

    wanted = profile.get("routes", list(ROUTES))

    def keep(row) -> bool:
        title = f" {row[1].lower()} "
        if any_words and not any(word_in(w, title) for w in any_words):
            return False
        if any(word_in(w, title) for w in none_words):
            return False
        remote = "remote" in wanted and row[7] in ("yes", "maybe")
        # A UK sponsor licence helps only for a job IN the UK: Renesas is on the UK list, its Poland jobs aren't
        move = "move" in wanted and row[8] != "no" and (row[8] == "yes" or row[9] == "yes" or row[13])
        if not (remote or move):
            return False
        # remote-from-Pakistan jobs pass any region filter: where the company is doesn't matter then
        return not regions or row[7] == "yes" or bool(regions & set(filter(None, row[10].split(","))))
    return [row for row in map(with_sponsor_here, rows) if keep(row)]


SPONSOR_COUNTRY = {"UK": "United Kingdom", "NL": "Netherlands"}


def with_sponsor_here(row: tuple) -> tuple:
    """Adds row[13]: the sponsor list that covers THIS job's country ("UK"), or ""."""
    return (*row, sponsor_here(row[5], row[11]))


def sponsor_here(location: str, sponsor_in: str) -> str:
    """'London' + 'NL,UK' -> 'UK': only the list for the job's own country counts."""
    lists = [code for code in (sponsor_in or "").split(",") if code]
    here = places.countries_in(location or "") if lists else set()
    return ",".join(code for code in lists if SPONSOR_COUNTRY[code] in here)


def word_in(word: str, title: str) -> bool:
    """Whole word: "ai" must not match "maintenance"."""
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", title) is not None


def embed_missing(db, rows: list[tuple], embedder) -> dict[str, np.ndarray]:
    ids = [row[0] for row in rows]
    vectors = {}
    for start in range(0, len(ids), 900):  # SQLite allows ~999 parameters per query
        chunk = ids[start:start + 900]
        for job_id, blob in db.execute(f"SELECT job_id, vector FROM embeddings WHERE model = ? AND job_id IN "
                                       f"({','.join('?' * len(chunk))})", [MODEL, *chunk]):
            vectors[job_id] = np.frombuffer(blob, dtype=np.float32)
    todo = [row for row in rows if row[0] not in vectors]
    if todo:
        start = time.perf_counter()
        print(f"Embedding {len(todo)} new jobs on CPU (cached after this)...", flush=True)
        new = embedder.embed([job_text(r[1], r[2], r[3]) for r in todo], batch_size=64)
        batch = []
        for row, vector in zip(todo, new):
            vector = np.asarray(vector, dtype=np.float32)
            vectors[row[0]] = vector
            batch.append((row[0], MODEL, vector.tobytes()))
            if len(batch) == 2000:
                db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)", batch)
                db.commit()
                batch = []
        db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)", batch)
        db.commit()
        print(f"  done in {time.perf_counter() - start:.0f}s ({len(todo) / (time.perf_counter() - start):.0f} jobs/s)")
    return vectors


def rank(profile: dict, rows: list[tuple], vectors: dict, embedder) -> list[dict]:
    # bge models want this prefix on the query side (short query vs long passages)
    query = next(embedder.query_embed(profile["looking_for"]))
    query = np.asarray(query, dtype=np.float32)
    matrix = np.stack([vectors[row[0]] for row in rows])
    similarity = matrix @ query / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(query))
    results = []
    for row, sim in zip(rows, similarity):
        flags = [name for name, on in (("from Pakistan: yes", row[7] == "yes"), ("visa: yes", row[8] == "yes"),
                                       ("relocation: yes", row[9] == "yes"), ("sponsor list", bool(row[13])))
                 if on]
        results.append({"job_id": row[0], "title": row[1], "company": row[4], "location": row[5], "url": row[6],
                        "from_pakistan": row[7], "visa": row[8], "relocation": row[9], "size": row[12],
                        "sponsor_here": row[13],
                        "similarity": float(sim), "score": float(sim) + sum(BOOSTS[f] for f in flags),
                        "why": ", ".join(f"{f} {row[13]}" if f == "sponsor list" else f for f in flags)})
    results.sort(key=lambda r: -r["score"])
    # One job posted in 4 cities is one choice, not 4: keep the best, note the others
    best = {}
    for r in results:
        key = (r["company"].lower(), re.sub(r"\W+", " ", r["title"].lower()).strip())
        if key in best:
            best[key]["also"] += 1
        else:
            best[key] = r | {"also": 0}
    return list(best.values())


def second_look(db, profile: dict, ranked: list[dict]) -> list[dict]:
    """Full description for SmartRecruiters jobs + LLM answers, then drop jobs that are no longer possible."""
    ids = [r["job_id"] for r in ranked]
    check.fill_descriptions(db, ids)
    check.ask_llm(db, ids)
    final = check.final_answers(db, ids)
    wanted = profile.get("routes", list(ROUTES))
    kept, dropped = [], []
    for r in ranked:
        answer = final[r["job_id"]]
        remote = "remote" in wanted and answer["from_pakistan"] in ("yes", "maybe")
        move = "move" in wanted and answer["visa"] != "no" and (
            answer["visa"] == "yes" or answer["relocation"] == "yes" or r["sponsor_here"])
        flags = [f for f, on in (("from Pakistan: yes", answer["from_pakistan"] == "yes"),
                                 ("visa: yes", answer["visa"] == "yes"),
                                 ("relocation: yes", answer["relocation"] == "yes")) if on]
        if r["sponsor_here"]:
            flags.append(f"sponsor list {r['sponsor_here']}")
        r = r | answer | {"why": ", ".join(flags) + (f'  [checked: "{answer["llm"]}"]' if answer["llm"] else "")}
        (kept if remote or move else dropped).append(r)
    for r in dropped:
        print(f"  dropped after reading the post: {r['title'][:50]} | {r['company']} "
              f"(from Pakistan {r['from_pakistan']}, visa {r['visa']}) {r['llm'] or ''}")
    return kept


def main() -> None:
    if not PROFILES.exists():
        sys.exit("No profiles.toml: copy profiles.example.toml to profiles.toml and edit it.")
    from fastembed import TextEmbedding
    profiles = tomllib.loads(PROFILES.read_text(encoding="utf-8"))["profile"]
    db = store.connect()
    db.executescript(SCHEMA)
    embedder = TextEmbedding(MODEL, cache_dir="data/models")  # downloaded once (~130 MB), then offline
    today = date.today().isoformat()
    for profile in profiles:
        rows = candidates(db, profile)
        print(f"\n=== {profile['name']}: {len(rows)} candidate jobs after filters")
        if not rows:
            continue
        vectors = embed_missing(db, rows, embedder)
        top = profile.get("top", 25)
        # Check a few more than we show: some will drop out once the full post is read
        ranked = rank(profile, rows, vectors, embedder)[:top + top // 2]
        if "--no-check" not in sys.argv:
            ranked = second_look(db, profile, ranked)
        ranked = ranked[:top]
        db.execute("DELETE FROM matches WHERE profile = ?", (profile["name"],))
        db.executemany("INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?)",
                       [(profile["name"], i + 1, r["job_id"], r["score"], r["similarity"], r["why"], today)
                        for i, r in enumerate(ranked)])
        db.commit()
        for i, r in enumerate(ranked, 1):
            more = f" (+{r['also']} more locations)" if r["also"] else ""
            print(f"{i:3}. {r['score']:.3f}  {r['title'][:60]}  |  {r['company'][:25]}  |  {r['location'][:30]}{more}"
                  f"\n       {r['why'] or 'remote: maybe'}  {r['url']}")


if __name__ == "__main__":
    main()
