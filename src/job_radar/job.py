"""Print one job in full: by its id (shown on the web page) or by its URL.

  uv run python -m job_radar.job <job id or URL>                  title, company, answers, profile + CV, full post
  uv run python -m job_radar.job <job id or URL> --cv <path>      remember the CV you sent (tracker note, status "saved")

Used by the CV-tailoring step: it reads the job from here instead of a copy-pasted description.
"""
import sys
import tomllib
from datetime import date

from job_radar import check, store
from job_radar.match import PROFILES, sponsor_here


def find(db, key: str):
    row = db.execute("SELECT id FROM jobs WHERE id = ?", (key,)).fetchone()
    if not row and key.startswith("http"):  # a URL: exact first, then without the query string
        row = (db.execute("SELECT id FROM jobs WHERE url = ?", (key,)).fetchone()
               or db.execute("SELECT id FROM jobs WHERE url LIKE ? LIMIT 1", (key.split("?")[0] + "%",)).fetchone())
    return row[0] if row else None


def profile_cvs(profile_name: str | None) -> list[str]:
    if not profile_name or not PROFILES.exists():
        return []
    for profile in tomllib.loads(PROFILES.read_text(encoding="utf-8")).get("profile", []):
        if profile["name"] == profile_name:
            cv = profile.get("cv", [])
            return [cv] if isinstance(cv, str) else list(cv)
    return []


def show(db, job_id: str) -> None:
    title, company, location, workplace, url, description, posted, salary, closed, sponsor = db.execute(
        """SELECT j.title, j.company, j.location, j.workplace, j.url, j.description, j.posted_at, j.salary,
                  j.closed_on, COALESCE(c.sponsor_in, '')
           FROM jobs j LEFT JOIN companies c ON c.board = j.board WHERE j.id = ?""", (job_id,)).fetchone()
    db.executescript(check.SCHEMA + ";")
    answer = check.final_answers(db, [job_id])[job_id]
    match = db.execute("SELECT profile, rank FROM matches WHERE job_id = ? ORDER BY rank LIMIT 1", (job_id,)).fetchone()
    tracked = db.execute("SELECT status, note FROM tracker WHERE job_id = ?", (job_id,)).fetchone()
    print(f"JOB ID:     {job_id}")
    print(f"TITLE:      {title}")
    print(f"COMPANY:    {company}")
    print(f"LOCATION:   {location or '-'}  ({workplace or 'workplace not given'})")
    print(f"URL:        {url}")
    print(f"POSTED:     {posted or '-'}   SALARY: {salary or '-'}" + ("   ** CLOSED **" if closed else ""))
    print(f"ANSWERS:    from Pakistan {answer['from_pakistan']}, visa {answer['visa']}, "
          f"relocation {answer['relocation']}" + (f", sponsor list {here}" if (here := sponsor_here(location, sponsor)) else ""))
    if answer.get("llm"):
        print(f"EVIDENCE:   \"{answer['llm']}\"")
    if match:
        print(f"PROFILE:    {match[0]} (rank {match[1]})")
        print(f"BASE CV:    {', '.join(profile_cvs(match[0])) or '(no cv set in profiles.toml)'}")
    if tracked:
        print(f"TRACKER:    {tracked[0]}" + (f" | {tracked[1]}" if tracked[1] else ""))
    print("\n----- FULL POST -----\n")
    print(description or "(no description stored: open the URL)")


def remember_cv(db, job_id: str, cv_path: str) -> None:
    title, company, url = db.execute("SELECT title, company, url FROM jobs WHERE id = ?", (job_id,)).fetchone()
    old = db.execute("SELECT status, note FROM tracker WHERE job_id = ?", (job_id,)).fetchone()
    note = f"CV: {cv_path}" + (f" | {old[1]}" if old and old[1] and "CV:" not in old[1] else "")
    db.execute("INSERT OR REPLACE INTO tracker VALUES (?, ?, ?, ?, ?, ?, ?)",
               (job_id, old[0] if old else "saved", note, title, company, url, date.today().isoformat()))
    db.commit()
    print(f"Tracker: {company} / {title} -> {note}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    if not args:
        sys.exit(__doc__)
    db = store.connect()
    job_id = find(db, args[0])
    if not job_id:
        sys.exit(f"No job found for {args[0]!r}. Use the job id from the web page, or the exact job URL.")
    if "--cv" in args:
        remember_cv(db, job_id, args[args.index("--cv") + 1])
    else:
        show(db, job_id)


if __name__ == "__main__":
    main()
