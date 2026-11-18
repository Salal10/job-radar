"""Second look at the top matches only: fill in missing descriptions, then ask the LLM.

Why only the top matches: SmartRecruiters needs one extra call per job for the description (180k jobs),
and the free LLM tier allows 15 calls a minute. For the ~50 jobs you'll actually read, both are cheap.

Measured in M4 (holdout set): the LLM read visa wording best (100% vs 96% for rules), and the rules +
LLM-for-"maybe" combination was best for "can I do it from Pakistan" (90% vs 84%). So for a checked job:
  from_pakistan = rules, unless the rules said "maybe" -> LLM
  visa, relocation = LLM (it also reads non-English posts, which the rules can't)
"""
import asyncio
import time
from datetime import date

import httpx

from job_radar import sources
from job_radar.eligibility import classify
from job_radar.evaluate import excerpts

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_checks (
    job_id TEXT PRIMARY KEY, model TEXT, from_pakistan TEXT, visa TEXT, relocation TEXT, evidence TEXT,
    checked_on TEXT
)"""


async def _smartrecruiters_descriptions(job_ids: list[str]) -> dict[str, str]:
    limit = asyncio.Semaphore(4)

    async def one(client, job_id):
        _, slug, posting = job_id.split(":")
        async with limit:
            try:
                r = await sources.get(client, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting}")
            except httpx.HTTPError as e:
                print(f"  could not fetch {job_id}: {e}")
                return job_id, ""
        sections = (r.json().get("jobAd") or {}).get("sections") or {}
        # The job first, the company "About us" last: ranking embeds the start of the text
        order = ["jobDescription", "qualifications", "additionalInformation", "companyDescription"]
        return job_id, sources.to_text(" ".join((sections.get(k) or {}).get("text", "") for k in order))

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        return dict(await asyncio.gather(*(one(client, j) for j in job_ids)))


def fill_descriptions(db, job_ids: list[str]) -> int:
    """Fetch missing SmartRecruiters descriptions, store them, and re-run the rules on them."""
    missing = [j for j, in db.execute(
        f"SELECT id FROM jobs WHERE id IN ({','.join('?' * len(job_ids))}) AND source = 'smartrecruiters' "
        f"AND COALESCE(description, '') = ''", job_ids)]
    if not missing:
        return 0
    print(f"Fetching {len(missing)} SmartRecruiters descriptions...")
    found = {j: text for j, text in asyncio.run(_smartrecruiters_descriptions(missing)).items() if text}
    today = date.today().isoformat()
    for job_id, text in found.items():
        db.execute("UPDATE jobs SET description = ? WHERE id = ?", (text, job_id))
        db.execute("DELETE FROM embeddings WHERE job_id = ?", (job_id,))  # the text changed: re-embed next run
        location, workplace, title = db.execute("SELECT location, workplace, title FROM jobs WHERE id = ?",
                                                (job_id,)).fetchone()
        rules = classify(location, workplace, text, title)
        db.execute("UPDATE eligibility SET from_pakistan = ?, visa = ?, relocation = ?, how = ?, evidence = ?, "
                   "checked_on = ? WHERE job_id = ?", (rules["from_pakistan"], rules["visa"], rules["relocation"],
                                                       rules["how"], rules["evidence"], today, job_id))
    db.commit()
    return len(found)


def ask_llm(db, job_ids: list[str]) -> int:
    """LLM answers for jobs not checked yet (cached forever: a post rarely changes its visa wording)."""
    from job_radar import llm_check
    db.execute(SCHEMA)
    done = {j for j, in db.execute("SELECT job_id FROM llm_checks")}
    todo = [j for j in job_ids if j not in done]
    if not todo:
        return 0
    try:
        gemini = llm_check.client()
    except SystemExit as e:
        print(f"Skipping the LLM check: {e}")
        return 0
    print(f"Asking {llm_check.MODEL} about {len(todo)} jobs (~{len(todo) * 4 // 60 + 1} min at the free-tier pace)...")
    asked, failures = 0, 0
    for job_id in todo:
        title, location, workplace, description = db.execute(
            "SELECT title, location, workplace, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
        answer, info = llm_check.ask(gemini, title, location, workplace, excerpts(description or ""))
        if info.get("daily_quota"):
            print("  Gemini's free daily quota is used up: these jobs get checked on the next run (answers are cached)")
            break
        if not answer:
            failures += 1
            print(f"  no answer for {job_id}: {info.get('error')}")
            if failures == 3:
                print("  3 failures in a row: stopping the LLM check")
                break
        else:
            failures = 0
            db.execute("INSERT OR REPLACE INTO llm_checks VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (job_id, llm_check.MODEL, answer.from_pakistan, answer.visa, answer.relocation,
                        answer.evidence, date.today().isoformat()))
            db.commit()
            asked += 1
        time.sleep(60 / llm_check.REQUESTS_PER_MINUTE)
    return asked


def combine(rules: dict, llm: dict | None) -> dict:
    """Rules + LLM for a job the LLM has read. Each choice follows a measured result (eval sets, M4/M5):
    - rules said "maybe" -> the LLM decides (the hybrid was best: 90% vs 84% on the holdout)
    - the LLM says "no" -> no, even over a rules "yes" (LLM "no" was right 100% / 98% of the time)
    - visa, relocation -> the LLM (100% on the holdout; also reads non-English posts)"""
    if not llm:
        return dict(rules)
    answer = dict(rules)
    if rules["from_pakistan"] == "maybe" or llm["from_pakistan"] == "no":
        answer["from_pakistan"] = llm["from_pakistan"]
    answer["visa"], answer["relocation"] = llm["visa"], llm["relocation"]
    return answer


def final_answers(db, job_ids: list[str]) -> dict[str, dict]:
    """Rules + LLM combined. Jobs without an LLM answer keep the rules' answers."""
    db.execute(SCHEMA)
    out = {}
    for job_id in job_ids:
        rules = dict(zip(("from_pakistan", "visa", "relocation"), db.execute(
            "SELECT from_pakistan, visa, relocation FROM eligibility WHERE job_id = ?", (job_id,)).fetchone()))
        llm = db.execute("SELECT from_pakistan, visa, relocation, evidence FROM llm_checks WHERE job_id = ?",
                         (job_id,)).fetchone()
        llm = dict(zip(("from_pakistan", "visa", "relocation", "evidence"), llm)) if llm else None
        out[job_id] = combine(rules, llm) | {"llm": llm["evidence"] if llm else None}
    return out
