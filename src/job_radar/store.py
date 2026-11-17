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
    source TEXT, company TEXT, title TEXT, location TEXT, workplace TEXT,
    department TEXT, url TEXT, description TEXT, posted_at TEXT, salary TEXT,
    first_seen TEXT NOT NULL,   -- the day we first found it
    last_seen TEXT NOT NULL     -- the last day it was still on the board; older than today = closed
)"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(SCHEMA)
    return db


def save(db: sqlite3.Connection, jobs: list[Job]) -> int:
    """Add new jobs, refresh the ones we already have. Returns how many were new.
    Running it twice in a day changes nothing the second time (idempotent)."""
    today = date.today().isoformat()
    before = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    db.executemany("""
        INSERT INTO jobs VALUES (:id, :source, :company, :title, :location, :workplace, :department,
                                 :url, :description, :posted_at, :salary, :today, :today)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title, location = excluded.location, workplace = excluded.workplace,
            department = excluded.department, url = excluded.url, description = excluded.description,
            salary = excluded.salary, last_seen = excluded.last_seen""",
        [asdict(job) | {"today": today} for job in jobs])
    db.commit()
    return db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] - before