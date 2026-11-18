"""SQLite storage. Each job keeps the day we first and last saw it, and the day it closed."""
import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from job_radar.sources import Job

DB_PATH = Path("data/jobs.db")
KEEP_CLOSED_DAYS = 30  # closed jobs stay this long (to see "it closed last week"), then they're deleted

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    board TEXT,                 -- "greenhouse:gitlab": which company board it came from
    source TEXT, company TEXT, title TEXT, location TEXT, workplace TEXT,
    department TEXT, url TEXT, description TEXT, posted_at TEXT, salary TEXT,
    first_seen TEXT NOT NULL,   -- the day we first found it
    last_seen TEXT NOT NULL,    -- the last day it was on the board
    closed_on TEXT              -- the day it disappeared from a board we read fine; NULL = still open
);
CREATE INDEX IF NOT EXISTS jobs_board ON jobs(board);
CREATE TABLE IF NOT EXISTS companies (
    board TEXT PRIMARY KEY,     -- "greenhouse:deliveroo". Not the name: one company can have two boards,
    company TEXT,               -- and two different companies can share a name ("Relay")
    ats TEXT, slug TEXT,
    open_jobs INTEGER,          -- jobs open on the last good fetch: a cheap measure of company size
    size TEXT,                  -- startup / scale-up / large, from open_jobs
    sponsor_in TEXT,            -- countries whose official sponsor list has this name, e.g. "NL,UK"
    sponsor_match TEXT,         -- exact / brand / "": how sure the name match is
    last_ok TEXT                -- last day this board was fetched without an error
);
CREATE TABLE IF NOT EXISTS runs (   -- one row per daily run: is the tool healthy over time?
    day TEXT, seconds REAL, companies INTEGER, failed INTEGER,
    open_jobs INTEGER, new INTEGER, closed INTEGER, reopened INTEGER, deleted INTEGER
);"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path)
    # Databases made before M3 lack the new columns. ADD COLUMN is instant in SQLite, even on 1 GB.
    for table, column in (("jobs", "closed_on"), ("companies", "last_ok")):
        if db.execute(f"SELECT 1 FROM sqlite_master WHERE name = '{table}'").fetchone() and \
                column not in [row[1] for row in db.execute(f"PRAGMA table_info({table})")]:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    db.executescript(SCHEMA)
    return db


def save_companies(db: sqlite3.Connection, rows: list[dict], boards_in_csv: set[str], today: str) -> None:
    """Update the boards fetched today. A board that failed today keeps yesterday's row (it didn't vanish,
    we just couldn't read it). Boards removed from companies.csv are removed here too."""
    db.executemany("""
        INSERT INTO companies VALUES (:board, :company, :ats, :slug, :open_jobs, :size, :sponsor_in,
                                      :sponsor_match, :today)
        ON CONFLICT(board) DO UPDATE SET
            company = excluded.company, open_jobs = excluded.open_jobs, size = excluded.size,
            sponsor_in = excluded.sponsor_in, sponsor_match = excluded.sponsor_match, last_ok = excluded.last_ok""",
        [row | {"today": today} for row in rows])
    gone = {board for (board,) in db.execute("SELECT board FROM companies")} - boards_in_csv
    db.executemany("DELETE FROM companies WHERE board = ?", [(board,) for board in gone])
    db.commit()


def save(db: sqlite3.Connection, jobs: list[Job], today: str) -> tuple[int, int]:
    """Add new jobs, refresh the ones we already have. Returns (new, reopened).
    Running it twice in a day changes nothing the second time (idempotent)."""
    before = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    was_closed = db.execute("SELECT COUNT(*) FROM jobs WHERE closed_on IS NOT NULL").fetchone()[0]
    db.executemany("""
        INSERT INTO jobs VALUES (:id, :board, :source, :company, :title, :location, :workplace, :department,
                                 :url, :description, :posted_at, :salary, :today, :today, NULL)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title, location = excluded.location, workplace = excluded.workplace,
            department = excluded.department, url = excluded.url, description = excluded.description,
            salary = excluded.salary, last_seen = excluded.last_seen,
            closed_on = NULL""",  # back on the board: a job can be unpublished for a day and come back
        [asdict(job) | {"board": job.id.rsplit(":", 1)[0], "today": today} for job in jobs])
    db.commit()
    new = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] - before
    reopened = was_closed - db.execute("SELECT COUNT(*) FROM jobs WHERE closed_on IS NOT NULL").fetchone()[0]
    return new, reopened


def close_missing(db: sqlite3.Connection, boards_ok: set[str], today: str) -> int:
    """Jobs not seen today are closed, but ONLY on boards we read fine today. If a board failed (timeout,
    429), its jobs are unknown, not closed: a network blip must not close 1,000 jobs."""
    db.execute("CREATE TEMP TABLE IF NOT EXISTS boards_ok (board TEXT PRIMARY KEY)")
    db.execute("DELETE FROM boards_ok")
    db.executemany("INSERT INTO boards_ok VALUES (?)", [(board,) for board in boards_ok])
    closed = db.execute("""UPDATE jobs SET closed_on = ? WHERE closed_on IS NULL AND last_seen < ?
                           AND board IN (SELECT board FROM boards_ok)""", (today, today)).rowcount
    db.commit()
    return closed


def prune(db: sqlite3.Connection, boards_in_csv: set[str], today: str) -> int:
    """Delete jobs closed long ago, and jobs of boards no longer in companies.csv. Returns how many."""
    cutoff = (date.fromisoformat(today) - timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
    deleted = db.execute("DELETE FROM jobs WHERE closed_on < ?", (cutoff,)).rowcount
    known = {board for (board,) in db.execute("SELECT DISTINCT board FROM jobs")}
    for board in known - boards_in_csv:
        deleted += db.execute("DELETE FROM jobs WHERE board = ?", (board,)).rowcount
    db.commit()
    if deleted > 10_000:
        db.execute("VACUUM")  # deleting doesn't shrink the file; VACUUM rewrites it. Slow, so only after big deletes
    return deleted


def log_run(db: sqlite3.Connection, **numbers) -> None:
    db.execute("INSERT INTO runs VALUES (:day, :seconds, :companies, :failed, :open_jobs, :new, :closed, "
               ":reopened, :deleted)", numbers)
    db.commit()
