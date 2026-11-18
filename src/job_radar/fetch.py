"""Daily run: fetch every company in companies.csv, save to SQLite, close jobs that disappeared,
delete old ones, and print a summary.

Run: uv run python -m job_radar.fetch
"""
import asyncio
import csv
import random
import time
from collections import Counter, defaultdict
from datetime import date

import httpx

from job_radar import store
from job_radar.sources import FETCHERS, Job
from job_radar.sponsors import SponsorMatcher, registers

COMPANIES = "companies.csv"
PARALLEL = 8  # at most 8 requests at once: polite to the ATS servers, and still fast
HEADERS = {"User-Agent": "job-radar/0.1 (personal job search)"}


async def fetch_company(client: httpx.AsyncClient, limit: asyncio.Semaphore, row: dict) -> tuple[dict, list[Job] | str]:
    async with limit:
        start = time.perf_counter()
        try:
            found = await FETCHERS[row["ats"]](client, row["slug"], row["name"])
        except Exception as e:  # one broken board must not stop the others
            found = f"{type(e).__name__}: {e}"[:150]
        row["seconds"] = time.perf_counter() - start  # where does the time go? measured per board
        return row, found


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
    today = date.today().isoformat()
    in_csv = {f"{row['ats']}:{row['slug']}" for row in companies}

    start = time.perf_counter()
    results = asyncio.run(fetch_all(companies))
    seconds = time.perf_counter() - start

    jobs = [job for _, found in results if isinstance(found, list) for job in found]
    failed = [(row, found) for row, found in results if isinstance(found, str)]
    db = store.connect()
    new, reopened = store.save(db, jobs, today)
    rows = company_rows(results, SponsorMatcher(registers()))
    store.save_companies(db, rows, in_csv, today)
    # A 404 is a clear answer ("this board no longer exists"), unlike a timeout: close its jobs too.
    gone = {f"{row['ats']}:{row['slug']}" for row, error in failed if "404 Not Found" in error}
    closed = store.close_missing(db, {row["board"] for row in rows} | gone, today)
    deleted = store.prune(db, in_csv, today)
    store.log_run(db, day=today, seconds=round(seconds), companies=len(companies), failed=len(failed),
                  open_jobs=len(jobs), new=new, closed=closed, reopened=reopened, deleted=deleted)

    print(f"{len(companies)} companies, {len(jobs)} open jobs in {seconds:.0f}s")
    print(f"Today: {new} new, {closed} closed, {reopened} back again, {deleted} old ones deleted")
    print("By source:", dict(Counter(job.source for job in jobs)))
    print("Companies by size:", dict(Counter(row["size"] for row in rows)))
    print("On a sponsor list:", dict(Counter(row["sponsor_in"] for row in rows if row["sponsor_in"])))
    busy = defaultdict(float)  # seconds spent per ATS (summed over boards; they run 8 at a time)
    for row, _ in results:
        busy[row["ats"]] += row["seconds"]
    print("Board-seconds per ATS:", {ats: round(s) for ats, s in sorted(busy.items(), key=lambda x: -x[1])})
    print("Slowest boards:", ", ".join(f"{row['ats']}/{row['slug']} {row['seconds']:.0f}s"
                                       for row, _ in sorted(results, key=lambda r: -r[0]["seconds"])[:5]))
    if gone:
        print(f"{len(gone)} boards no longer exist (404), their jobs are closed: {', '.join(sorted(gone))}")
    for row, error in failed:
        if "404 Not Found" not in error:
            print(f"  FAILED {row['ats']}/{row['slug']}: {error}")


if __name__ == "__main__":
    main()
