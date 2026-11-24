"""Local web page: your matches per profile, with links to the original posts, and a job tracker.

Run: uv run python -m job_radar.web    then open http://localhost:8766
Only this PC can open it (it listens on 127.0.0.1). Matches come from the last `match` run.
"""
import html
import re
import sys
import tomllib
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from job_radar import check, store, students
from job_radar.job import profile_cvs
from job_radar.match import PROFILES, hired_here, sponsor_here

PROMPT = Path("prompts/tailor-cv.md")

PORT = 8766
STATUSES = ["saved", "applied", "interview", "offer", "rejected", "hidden"]
LABELS = {"from_pakistan": {"yes": "✅ remote, Pakistan allowed", "maybe": "❔ remote, place not stated"},
          "visa": {"yes": "🛂 visa sponsorship", "no": "⛔ no visa sponsorship"},
          "relocation": {"yes": "🧳 relocation support"}}

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--text:#1f1f1f;--muted:#6b6b6b;--line:#ddd;--accent:#0b62c4;--good:#1d7a46;--bad:#a33}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--card:#202020;--text:#eee;--muted:#a0a0a0;--line:#333;
 --accent:#6aa8ff;--good:#5fc48a;--bad:#e57373}}
*{box-sizing:border-box}body{font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--text);margin:0}
main{max-width:980px;margin:auto;padding:16px}a{color:var(--accent)}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 16px}nav a{padding:6px 12px;border:1px solid var(--line);
 border-radius:999px;text-decoration:none;color:var(--text)}nav a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.meta{color:var(--muted);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:12px 14px;margin:10px 0}.title{font-weight:600;font-size:16px}.rank{color:var(--muted);margin-right:6px}
.badges{margin:6px 0;display:flex;gap:6px;flex-wrap:wrap}.b{font-size:12px;padding:2px 8px;border-radius:999px;
 border:1px solid var(--line)}.b.good{color:var(--good);border-color:var(--good)}.b.bad{color:var(--bad);border-color:var(--bad)}
.ev{font-size:13px;color:var(--muted);font-style:italic}form.inline{display:inline}button,select,input{font:inherit;
 padding:4px 10px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button:hover,a.btn:hover{border-color:var(--accent)}a.btn{padding:4px 10px;border-radius:6px;border:1px solid var(--line);
 text-decoration:none;color:var(--text);background:var(--card)}textarea{background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px}.actions{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
input[name=note]{min-width:220px;flex:1}.new{color:var(--good);font-weight:600;font-size:12px}
h2{margin:24px 0 4px;font-size:17px;text-transform:capitalize}.closed{color:var(--bad);font-size:12px}
@media (max-width:600px){input[name=note]{min-width:0;width:100%}}
"""


def esc(value) -> str:
    return html.escape(str(value or ""))


def page(title: str, body: str, profiles: list[str], current: str) -> str:
    tabs = "".join(f'<a href="/?{urlencode({"p": p})}" class="{"on" if p == current else ""}">{esc(p)}</a>'
                   for p in profiles)
    if students.config()["enabled"]:
        tabs += f'<a href="/students" class="{"on" if current == "students" else ""}">Working student</a>'
    tabs += f'<a href="/tracker" class="{"on" if current == "tracker" else ""}">Tracker</a>'
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{esc(title)} · Job Radar</title><style>{CSS}</style><main><h1>Job Radar</h1><nav>{tabs}</nav>{body}</main>
<script>
// "Copy" buttons copy the text of the element named in data-copy. If the browser blocks the clipboard,
// the text is selected instead, ready for Ctrl+C.
document.addEventListener("click", async (e) => {{
  const b = e.target.closest("button[data-copy]"); if (!b) return;
  const box = document.getElementById(b.dataset.copy), label = b.textContent;
  try {{ await navigator.clipboard.writeText(box.value); b.textContent = "✓ Copied"; }}
  catch {{ box.focus(); box.select(); b.textContent = "Selected: press Ctrl+C"; }}
  setTimeout(() => b.textContent = label, 2500);
}});
</script></html>"""


def status_buttons(job_id: str, current: str, back: str, note: str = "", with_note: bool = False,
                   with_tailor: bool = True) -> str:
    hidden = f'<input type=hidden name=job_id value="{esc(job_id)}"><input type=hidden name=back value="{esc(back)}">'
    if with_note:  # tracker page: pick any status + a note
        options = "".join(f'<option {"selected" if s == current else ""}>{s}</option>' for s in STATUSES)
        return (f'<form method=post action=/status class=actions>{hidden}<select name=status>{options}</select>'
                f'<input name=note value="{esc(note)}" placeholder="note: who you talked to, next step..."><button>Save</button></form>')
    buttons = "".join(f'<button name=status value={s}>{label}</button>'
                      for s, label in (("saved", "☆ Save"), ("applied", "✉ Applied"), ("hidden", "✕ Not for me")))
    # Ways to apply: directly, or tailor a CV first (prompt for any AI chat, or Claude Code)
    tailor = f'<a class=btn href="/tailor?{urlencode({"job": job_id})}">📝 Tailor CV</a>' if with_tailor else ""
    return f'<form method=post action=/status class=actions>{hidden}{buttons}{tailor}</form>'


def tailor_page(db, job_id: str, profiles: list[str]) -> str:
    """One job: apply directly, or tailor a CV with the prompt in prompts/tailor-cv.md (any AI chat or Claude Code)."""
    row = db.execute("SELECT title, company, location, url, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    row = row or db.execute("SELECT title, company, location, url, description FROM student_jobs WHERE id = ?",
                            (job_id,)).fetchone()
    if not row:
        return page("Not found", "<p>This job is no longer in the database.</p>", profiles, "")
    title, company, location, url, description = row
    config = tomllib.loads(PROFILES.read_text(encoding="utf-8")) if PROFILES.exists() else {}
    skip = ", ".join(config.get("skip_red_flags", [])) or "(nothing)"
    prompt = PROMPT.read_text(encoding="utf-8").format(
        title=title, company=company, location=location or "not given", url=url, skip_red_flags=skip,
        description=description or "(no description stored: open the link and paste the job text here)")
    match = db.execute("SELECT profile FROM matches WHERE job_id = ? ORDER BY rank LIMIT 1", (job_id,)).fetchone()
    cvs = profile_cvs(match[0] if match else None)
    cv_hint = (f"Your CV for this kind of job: <code>{esc(' or '.join(cvs))}</code>" if cvs
               else "Set <code>cv</code> in profiles.toml to see which CV fits this kind of job.")
    command = f"/tailor-cv {job_id}"
    body = f"""<h2 style="text-transform:none">{esc(title)}</h2><p class=meta>{esc(company)} · {esc(location)}</p>
<p class=meta>{cv_hint}</p>
<div class=card><div class=title>1. Apply directly</div>
<p>Use any CV you like. Open the post and apply on the company's site, then mark it ✉ Applied.</p>
<div class=actions><a class=btn href="{esc(url)}" target=_blank rel="noopener noreferrer">Open the job post ↗</a>
{status_buttons(job_id, "", f"/tailor?{urlencode({'job': job_id})}", with_tailor=False)}</div></div>
<div class=card><div class=title>2. Tailor your CV with any AI chat (Claude, ChatGPT, …)</div>
<p>Copy this prompt (the job is already filled in), paste it into the chat, and <b>attach your CV</b>.
It scores your CV against this job, rewrites it with the XYZ formula, and checks it like an ATS. It must only use facts
from your CV. Read the result before sending it.</p>
<textarea id=prompt readonly rows=12 style="width:100%;font:13px/1.4 ui-monospace,monospace">{esc(prompt)}</textarea>
<div class=actions><button type=button data-copy=prompt>📋 Copy prompt</button></div></div>
<div class=card><div class=title>3. Or let Claude Code make the PDF (optional)</div>
<p>If you use Claude Code: open it in the job-radar folder and run this command. It runs the same 3 steps using
<code>cvs/experience.md</code> (all your true facts) and saves a one-page PDF and a report in <code>applications/</code>.</p>
<input id=cmd readonly value="{esc(command)}" style="width:100%">
<div class=actions><button type=button data-copy=cmd>📋 Copy command</button></div></div>"""
    return page(f"Tailor CV · {title}", body, profiles, "")


WORLDWIDE = re.compile(r"anywhere|worldwide|world ?wide|global|any country|any location", re.I)


def badges(answer: dict, sponsor: str, how: str = "", evidence: str = "") -> str:
    out = []
    # "Remote from anywhere" only when the post says so (location or description, or the AI quoted it);
    # a post that lists allowed countries including Pakistan is not "anywhere"
    worldwide = "anywhere" in how or bool(WORLDWIDE.search(evidence or ""))
    for field, labels in LABELS.items():
        if field == "from_pakistan" and answer[field] == "yes" and worldwide:
            out.append('<span class="b good">🌍 remote from anywhere</span>')
            continue
        if label := labels.get(answer[field]):
            out.append(f'<span class="b {"bad" if answer[field] == "no" else "good"}">{label}</span>')
    if sponsor:
        out.append(f'<span class="b good">🏛 on the {esc(sponsor)} visa sponsor list</span>')
    return f'<div class=badges>{"".join(out)}</div>'


SORTS = {"match": "best match", "date": "newest first"}


def matches_page(db, profile: str, profiles: list[str], sort: str = "match") -> str:
    rows = db.execute("""
        SELECT m.rank, m.job_id, m.matched_on, j.title, j.company, j.location, j.url, j.first_seen, j.closed_on,
               j.posted_at, COALESCE(c.sponsor_in, ''), COALESCE(c.size, ''), t.status, m.similarity, m.score,
               COALESCE(e.how, '')
        FROM matches m JOIN jobs j ON j.id = m.job_id LEFT JOIN companies c ON c.board = j.board
        LEFT JOIN eligibility e ON e.job_id = m.job_id
        LEFT JOIN tracker t ON t.job_id = m.job_id
        WHERE m.profile = ? ORDER BY m.rank""", (profile,)).fetchall()
    rows = [r for r in rows if r[12] in (None, "saved")]  # applied/hidden ones are on the tracker page
    if sort == "date":  # newest post first; no posted date -> the day we first saw it. Ties keep the match order.
        rows.sort(key=lambda r: r[9] or r[7] or "", reverse=True)
    else:
        sort = "match"
    answers = check.final_answers(db, [r[1] for r in rows])
    last_fetch, first_fetch = db.execute("SELECT MAX(day), MIN(day) FROM runs").fetchone()
    cards = []
    for i, (rank, job_id, matched_on, title, company, location, url, first_seen, closed, posted, sponsor, size, status,
            similarity, score, how) in enumerate(rows):
        # after a button, come back to the same sort, scrolled to the card that was below this one
        below = f"#r{rows[i + 1][0]}" if i + 1 < len(rows) else ""
        back = f"/?{urlencode({'p': profile, 'sort': sort})}{below}"
        here = sponsor_here(location, sponsor)
        answer = answers[job_id]
        evidence = f'<div class=ev>“{esc(answer["llm"])}”</div>' if answer.get("llm") else ""
        new = '<span class=new>NEW </span>' if first_seen == last_fetch != first_fetch else ""  # on the first run everything is "new"
        saved = " ★ saved" if status == "saved" else ""
        cards.append(f"""<div class=card id="r{rank}"><div class=title><span class=rank>{rank}.</span>{new}
<a href="{esc(url)}" target=_blank rel="noopener noreferrer">{esc(title)}</a></div>
<div class=meta>{esc(company)}{f" · {esc(size)}" if size else ""} · {esc(location) or "location not given"}
{f" · posted {esc(posted)}" if posted else ""}{" · <span class=closed>closed</span>" if closed else ""}{saved}
<span title="How close this job is to your looking_for text (cosine similarity), plus small boosts for badges. Used for ranking only, not a percentage."> · match {score:.2f}</span></div>
{badges(answer, here, how, answer.get('llm') or '')}{f'<div class=badges><span class=b title="Official data: this company hired foreign workers in this country recently. A bonus signal, not a promise.">🧭 hired foreign workers in {esc(hired)} recently</span></div>' if (hired := hired_here(location, sponsor)) else ''}{evidence}{status_buttons(job_id, status or "", back)}</div>""")
    matched_on = rows[0][2] if rows else "never"
    body = (f'<p class=meta>Top matches from the last <code>match</code> run ({esc(matched_on)}). Jobs fetched on '
            f'{esc(last_fetch)}. “Applied” and “Not for me” move a job to the tracker; the next match run fills its place.</p>'
            + '<nav>Sort: ' + "".join(f'<a href="/?{urlencode({"p": profile, "sort": key})}" class="{"on" if key == sort else ""}">'
                                      f'{label}</a>' for key, label in SORTS.items()) + '</nav>'
            + ("".join(cards) or "<p>No matches yet: run <code>uv run python -m job_radar.match</code>.</p>"))
    return page(profile, body, profiles, profile)


LANGS = {"en": ('good', "🇬🇧 English OK"), "de-required": ('bad', "🇩🇪 German required"),
         "de-ad": ('', "🇩🇪 ad in German"), "": ('', "❔ language not known")}
SOURCES = {"arbeitsagentur": "Bundesagentur für Arbeit", "arbeitnow": "Arbeitnow"}  # others: the company's own page
# Boards without a free API: a ready-made search, opened in your browser
SEARCHES = [("LinkedIn", "https://www.linkedin.com/jobs/search/?" + urlencode(
                {"keywords": 'Werkstudent OR "working student" OR Praktikum OR internship', "location": "Germany",
                 "f_TPR": "r432000"})),  # posted in the last 5 days (in seconds)
            ("Indeed", "https://de.indeed.com/jobs?" + urlencode(
                {"q": 'Werkstudent OR "working student" OR Praktikum', "l": "Deutschland", "fromage": 7, "sort": "date"})),
            ("StepStone", "https://www.stepstone.de/jobs/werkstudent?" + urlencode({"sort": 2})),
            ("englishjobs.de", "https://englishjobs.de/jobs/student")]


WORKPLACES = {"remote": ('good', "🏠 remote"), "hybrid": ('good', "🔀 hybrid (some home office)"),
              "onsite": ('', "🏢 on-site"), "": ('', "❔ remote not stated")}


def students_page(db, profiles: list[str], lang: str = "", where: str = "") -> str:
    settings = students.config()
    students.ensure_table(db)
    rows = db.execute("""SELECT s.id, s.source, s.company, s.title, s.location, s.url, s.posted_at, s.lang, s.seen_on,
                                t.status, s.remote FROM student_jobs s LEFT JOIN tracker t ON t.job_id = s.id
                         ORDER BY s.posted_at DESC, s.title""").fetchall()
    avoid = [w.lower() for w in settings["avoid"]]
    rows = [r for r in rows if r[9] in (None, "saved") and not any(w in r[3].lower() for w in avoid)]
    # Two filters that combine: language (all / English OK) and place (all / remote or hybrid / remote only)
    by_lang = {"": rows, "en": [r for r in rows if r[7] == "en"]}
    lang = lang if lang in by_lang else ""
    places = {"": lambda r: True, "home": lambda r: r[10] in ("remote", "hybrid"), "remote": lambda r: r[10] == "remote"}
    where = where if where in places else ""
    rows = [r for r in by_lang[lang] if places[where](r)]
    shown = rows[:settings["top"]]
    cards = []
    for i, (job_id, source, company, title, location, url, posted, code, seen, status, remote) in enumerate(shown):
        below = f"#s{i + 1}" if i + 1 < len(shown) else ""
        kind, label = LANGS.get(code, LANGS[""])
        place_kind, place_label = WORKPLACES.get(remote or "", WORKPLACES[""])
        via = SOURCES.get(source, "company site")
        cards.append(f"""<div class=card id="s{i}"><div class=title><a href="{esc(url)}" target=_blank
rel="noopener noreferrer">{esc(students.clean_title(title))}</a></div>
<div class=meta>{esc(company)} · {esc(location) or "Germany"} · posted {esc(posted)} · via {esc(via)}{" · ★ saved" if status else ""}</div>
<div class=badges><span class="b {kind}">{label}</span><span class="b {place_kind}">{place_label}</span></div>
{status_buttons(job_id, status or "", f"/students?{urlencode({'lang': lang, 'where': where})}{below}")}</div>""")

    def link(new_lang: str, new_where: str, label: str, on: bool) -> str:
        return (f'<a href="/students?{urlencode({"lang": new_lang, "where": new_where})}" class="{"on" if on else ""}">'
                f'{label}</a>')
    in_lang = by_lang[lang]
    filters = ("".join(link(key, where, f"{label} ({sum(places[where](r) for r in by_lang[key])})", key == lang)
                       for key, label in (("", "All languages"), ("en", "🇬🇧 English OK only")))
               + "</nav><nav>"
               + "".join(link(lang, key, f"{label} ({sum(places[key](r) for r in in_lang)})", key == where)
                         for key, label in (("", "Anywhere"), ("home", "🏠🔀 Remote or hybrid"), ("remote", "🏠 Remote only"))))
    links = " ".join(f'<a class=btn href="{esc(url)}" target=_blank rel="noopener noreferrer">{name} ↗</a>'
                     for name, url in SEARCHES)
    seen = rows[0][8] if rows else "never"
    body = (f'<p class=meta>Working-student and internship jobs in Germany, any field, posted in the last '
            f'{settings["max_age_days"]} days, newest first (the newest {settings["top"]} are shown). '
            f'Last fetched {esc(seen)}. Language is read from the ad: “ad in German” usually means German is needed.</p>'
            f'<div class=actions>Search also on: {links}</div><nav>{filters}</nav>'
            + ("".join(cards) or "<p>No jobs yet: run <code>uv run python -m job_radar.students</code>.</p>"))
    return page("Working student", body, profiles, "students")


def tracker_page(db, profiles: list[str]) -> str:
    rows = db.execute("""SELECT t.job_id, t.status, t.note, t.title, t.company, t.url, t.updated_on, j.closed_on, COALESCE(j.id, s.id)
                         FROM tracker t LEFT JOIN jobs j ON j.id = t.job_id
                         LEFT JOIN student_jobs s ON s.id = t.job_id ORDER BY t.updated_on DESC""").fetchall()
    sections = []
    for status in STATUSES:
        group = [r for r in rows if r[1] == status]
        if not group:
            continue
        cards = "".join(f"""<div class=card><div class=title><a href="{esc(url)}" target=_blank rel="noopener noreferrer">
{esc(title)}</a></div><div class=meta>{esc(company)} · updated {esc(updated)}
{" · <span class=closed>closed on the board</span>" if closed or not still_there else ""}</div>
{status_buttons(job_id, status, "/tracker", note, with_note=True)}</div>"""
                        for job_id, _, note, title, company, url, updated, closed, still_there in group)
        sections.append(f"<h2>{status} ({len(group)})</h2>{cards}")
    body = "".join(sections) or "<p>Nothing tracked yet. Use ☆ Save / ✉ Applied on a match.</p>"
    return page("Tracker", body, profiles, "tracker")


def set_status(db, job_id: str, status: str, note: str | None) -> None:
    if status not in STATUSES:
        return
    students.ensure_table(db)
    job = (db.execute("SELECT title, company, url FROM jobs WHERE id = ?", (job_id,)).fetchone()
           or db.execute("SELECT title, company, url FROM student_jobs WHERE id = ?", (job_id,)).fetchone())
    old = db.execute("SELECT note, title, company, url FROM tracker WHERE job_id = ?", (job_id,)).fetchone()
    if not job and not old:
        return
    title, company, url = job or old[1:]
    note = note if note is not None else (old[0] if old else "")
    db.execute("INSERT OR REPLACE INTO tracker VALUES (?, ?, ?, ?, ?, ?, ?)",
               (job_id, status, note, title, company, url, date.today().isoformat()))
    db.commit()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        db = store.connect()
        db.executescript(check.SCHEMA + ";")
        students.ensure_table(db)
        url = urlparse(self.path)
        profiles = [p for p, in db.execute("SELECT DISTINCT profile FROM matches ORDER BY profile")]
        if url.path == "/tracker":
            return self.reply(tracker_page(db, profiles))
        if url.path == "/students":
            query = parse_qs(url.query)
            return self.reply(students_page(db, profiles, query.get("lang", [""])[0], query.get("where", [""])[0]))
        if url.path == "/tailor":
            return self.reply(tailor_page(db, parse_qs(url.query).get("job", [""])[0], profiles))
        if url.path != "/":
            return self.send_error(404)
        profile = parse_qs(url.query).get("p", [profiles[0] if profiles else ""])[0]
        self.reply(matches_page(db, profile, profiles, parse_qs(url.query).get("sort", ["match"])[0]))

    def do_POST(self):
        # Only this page may change your tracker: another website can't post here from your browser
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
            return self.send_error(403)
        if urlparse(self.path).path != "/status":
            return self.send_error(404)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode(),
                                              keep_blank_values=True).items()}
        set_status(store.connect(), form.get("job_id", ""), form.get("status", ""), form.get("note"))
        back = form.get("back", "/")
        self.send_response(303)
        self.send_header("Location", back if back.startswith("/") else "/")  # never redirect off this site
        self.end_headers()

    def reply(self, body: str):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main() -> None:
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else PORT
    print(f"Job Radar: http://localhost:{port}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
