"""Daily run: fetch every company in companies.csv, save to SQLite, print a summary.

Run: uv run python -m job_radar.fetch
"""
import asyncio
import csv
import time
from collections import Counter

import httpx

from job_radar import store
from job_radar.sources import FETCHERS, Job

COMPANIES = "companies.csv"
PARALLEL = 8  # at most 8 requests at once: polite to the ATS servers, and still fast
HEADERS = {"User-Agent": "job-radar/0.1 (personal job search)"}


async def fetch_company(client: httpx.AsyncClient, limit: asyncio.Semaphore, row: dict) -> tuple[dict, list[Job] | str]:
    async with limit:
        try:
            return row, await FETCHERS[row["ats"]](client, row["slug"], row["name"])
        except Exception as e:  # one broken board must not stop the others
            return row, f"{type(e).__name__}: {e}"[:150]


async def fetch_all(companies: list[dict]) -> list[tuple[dict, list[Job] | str]]:
    limit = asyncio.Semaphore(PARALLEL)
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        return await asyncio.gather(*(fetch_company(client, limit, row) for row in companies))


def main() -> None:
    with open(COMPANIES, encoding="utf-8") as f:
        companies = list(csv.DictReader(f))

    start = time.perf_counter()
    results = asyncio.run(fetch_all(companies))
    seconds = time.perf_counter() - start

    jobs = [job for _, found in results if isinstance(found, list) for job in found]
    failed = [(row, found) for row, found in results if isinstance(found, str)]
    db = store.connect()
    new = store.save(db, jobs)

    print(f"{len(companies)} companies, {len(jobs)} open jobs, {new} new, in {seconds:.1f}s")
    print("By source:", dict(Counter(job.source for job in jobs)))
    for row, error in failed:
        print(f"  FAILED {row['ats']}/{row['slug']}: {error}")


if __name__ == "__main__":
    main()