"""SQLite storage. Each job keeps the day we first saw it, so "new since yesterday" is a simple query."""
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path

from job_radar.sources import Job

DB_PATH = Path("data/jobs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    board TEXT,                 -- "greenhouse:gitlab": which company board it came from
    source TEXT, company TEXT, title TEXT, location TEXT, workplace TEXT,
    department TEXT, url TEXT, description TEXT, posted_at TEXT, salary TEXT,
    first_seen TEXT NOT NULL,   -- the day we first found it
    last_seen TEXT NOT NULL     -- the last day it was still on the board; older than today = closed
);
CREATE TABLE IF NOT EXISTS companies (
    board TEXT PRIMARY KEY,     -- "greenhouse:deliveroo". Not the name: one company can have two boards,
    company TEXT,               -- and two different companies can share a name ("Relay")
    ats TEXT, slug TEXT,
    open_jobs INTEGER,          -- jobs open today: a cheap measure of company size
    size TEXT,                  -- startup / scale-up / large, from open_jobs
    sponsor_in TEXT,            -- countries whose official sponsor list has this name, e.g. "NL,UK"
    sponsor_match TEXT          -- exact / brand / "": how sure the name match is
)"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def save_companies(db: sqlite3.Connection, rows: list[dict]) -> None:
    """Replace the whole table: it is rebuilt from today's fetch every run."""
    db.execute("DELETE FROM companies")
    db.executemany("INSERT INTO companies VALUES (:board, :company, :ats, :slug, :open_jobs, :size, "
                   ":sponsor_in, :sponsor_match)", rows)
    db.commit()


def save(db: sqlite3.Connection, jobs: list[Job]) -> int:
    """Add new jobs, refresh the ones we already have. Returns how many were new.
    Running it twice in a day changes nothing the second time (idempotent)."""
    today = date.today().isoformat()
    before = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    db.executemany("""
        INSERT INTO jobs VALUES (:id, :board, :source, :company, :title, :location, :workplace, :department,
                                 :url, :description, :posted_at, :salary, :today, :today)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title, location = excluded.location, workplace = excluded.workplace,
            department = excluded.department, url = excluded.url, description = excluded.description,
            salary = excluded.salary, last_seen = excluded.last_seen""",
        [asdict(job) | {"board": job.id.rsplit(":", 1)[0], "today": today} for job in jobs])
    db.commit()
    return db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] - before