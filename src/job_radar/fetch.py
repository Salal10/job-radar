"""Daily run: fetch every company in companies.csv, save to SQLite, print a summary.

Run: uv run python -m job_radar.fetch
"""
import asyncio
import csv
import random
import time
from collections import Counter

import httpx

from job_radar import store
from job_radar.sources import FETCHERS, Job
from job_radar.sponsors import SponsorMatcher, registers

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

def size_from(open_jobs: int) -> str:
    """Open roles as a rough size signal. A guess, not headcount: a 40-person startup can hire for 10 roles."""
    if open_jobs <= 20:
        return "startup"
    if open_jobs <= 100:
        return "scale-up"
    return "large"


def company_rows(results: list[tuple[dict, list[Job] | str]], matcher: SponsorMatcher) -> list[dict]:
    rows = []
    for row, found in results:
        if not isinstance(found, list):
            continue
        countries, how = matcher.lookup(row["name"])
        rows.append({"board": f"{row['ats']}:{row['slug']}", "company": row["name"], "ats": row["ats"],
                     "slug": row["slug"], "open_jobs": len(found), "size": size_from(len(found)),
                     "sponsor_in": ",".join(countries), "sponsor_match": how})
    return rows

def main() -> None:
    with open(COMPANIES, encoding="utf-8") as f:
        companies = list(csv.DictReader(f))
    random.shuffle(companies)  # companies.csv is sorted by ATS: unshuffled, 8 requests at once all hit one server

    start = time.perf_counter()
    results = asyncio.run(fetch_all(companies))
    seconds = time.perf_counter() - start

    jobs = [job for _, found in results if isinstance(found, list) for job in found]
    failed = [(row, found) for row, found in results if isinstance(found, str)]
    db = store.connect()
    new = store.save(db, jobs)
    rows = company_rows(results, SponsorMatcher(registers()))
    store.save_companies(db, rows)

    print(f"{len(companies)} companies, {len(jobs)} open jobs, {new} new, in {seconds:.1f}s")
    print("By source:", dict(Counter(job.source for job in jobs)))
    print("Companies by size:", dict(Counter(row["size"] for row in rows)))
    print("On a sponsor list:", dict(Counter(row["sponsor_in"] for row in rows if row["sponsor_in"])))
    for row, error in failed:
        print(f"  FAILED {row['ats']}/{row['slug']}: {error}")


if __name__ == "__main__":
    main()