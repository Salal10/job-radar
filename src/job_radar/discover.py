"""Find companies' job boards in Common Crawl (a free public copy of billions of web pages), check each one
is live, and add the new ones to companies.csv.

Run: uv run python -m job_radar.discover        (takes a while; run it weekly, not daily)
"""
import asyncio
import csv
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from job_radar.fetch import COMPANIES, HEADERS, PARALLEL

CRAWLS = 2  # how many of the latest crawls to search. Tested: older crawls add very few new boards
INDEX = "https://index.commoncrawl.org"
CRAWL_CACHE = Path("data/crawl")

# Board address -> which ATS fetcher reads it.
HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever_eu",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",      # EU boards are still read through the normal Greenhouse API
    "job-boards.eu.greenhouse.io": "greenhouse",
}
NOT_COMPANIES = {"embed", "v1", "api", "static", "assets", "favicon.ico", "robots.txt", "sitemap.xml"}


def slug_from(url: str) -> str | None:
    """'https://jobs.lever.co/acme/123' -> 'acme'. Greenhouse embeds look like '/embed/job_board?for=acme'."""
    parts = urlparse(url)
    first = parts.path.strip("/").split("/")[0].lower()
    if first == "embed":
        first = parse_qs(parts.query).get("for", [""])[0].lower()
    return first if first and first not in NOT_COMPANIES else None


def get_with_retries(client: httpx.Client, url: str, params: dict) -> httpx.Response | None:
    """The Common Crawl index is free and often busy (503/504), so wait and try again."""
    for attempt in range(5):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r
        except httpx.HTTPError:
            pass
        time.sleep(5 * (attempt + 1))
    return None


def slugs_for(client: httpx.Client, crawl: str, host: str) -> set[str] | None:
    """Every company slug Common Crawl saw on this host in this crawl. None if the index didn't answer."""
    api = f"{INDEX}/{crawl}-index"
    r = get_with_retries(client, api, {"url": f"{host}/*", "output": "json", "showNumPages": "true"})
    if r is None:
        return None
    slugs = set()
    for page in range(r.json()["pages"]):
        r = get_with_retries(client, api, {"url": f"{host}/*", "output": "json", "fl": "url", "page": page})
        if r is None:
            return None  # a missing page means an incomplete answer: don't save it as if it were complete
        for line in r.text.splitlines():
            try:
                slug = slug_from(json.loads(line)["url"])
            except (ValueError, KeyError):  # a cut-off or broken line from the busy index: skip it
                continue
            if slug:
                slugs.add(slug)
    return slugs


def boards_in_common_crawl() -> set[tuple[str, str]]:
    """Each finished (crawl, host) is saved to data/crawl/, so a crash or rerun continues where it stopped."""
    CRAWL_CACHE.mkdir(parents=True, exist_ok=True)
    found = set()
    with httpx.Client(headers=HEADERS, timeout=120) as client:
        crawls = [c["id"] for c in client.get(f"{INDEX}/collinfo.json").json()[:CRAWLS]]
        for crawl in crawls:
            for host, ats in HOSTS.items():
                saved = CRAWL_CACHE / f"{crawl}_{host}.txt"
                if saved.exists():
                    slugs = set(saved.read_text(encoding="utf-8").split())
                else:
                    slugs = slugs_for(client, crawl, host)
                    if slugs is None:
                        print(f"  {crawl} {host}: index busy, skipped (run again later to fill it in)")
                        continue
                    saved.write_text("\n".join(sorted(slugs)), encoding="utf-8")
                before = len(found)
                found |= {(ats, slug) for slug in slugs}
                print(f"  {crawl} {host}: {len(slugs)} boards, {len(found) - before} not seen before")
    return found


async def check_board(client: httpx.AsyncClient, limit: asyncio.Semaphore, ats: str, slug: str) -> dict | None:
    """Is the board live with at least one job? Returns its row for companies.csv, or None."""
    async with limit:
        try:
            if ats == "greenhouse":
                jobs = (await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")).json()["jobs"]
                name = (await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")).json()["name"]
            else:
                api, page = {
                    "ashby": (f"https://api.ashbyhq.com/posting-api/job-board/{slug}", f"https://jobs.ashbyhq.com/{slug}"),
                    "lever": (f"https://api.lever.co/v0/postings/{slug}?mode=json", f"https://jobs.lever.co/{slug}"),
                    "lever_eu": (f"https://api.eu.lever.co/v0/postings/{slug}?mode=json", f"https://jobs.eu.lever.co/{slug}"),
                }[ats]
                data = (await client.get(api)).json()
                jobs = data if isinstance(data, list) else data["jobs"]
                # Ashby and Lever don't return the company name, but the board page's <title> has it.
                title = re.search(r"<title>([^<]*)</title>", (await client.get(page)).text)
                name = re.sub(r"\s+Jobs$", "", html.unescape(title.group(1))).strip() if title else ""
        except Exception:  # 404, not JSON, timeout: not a live board
            return None
    if re.fullmatch(r"(?i)(jobs|careers|not found.*|404.*|)", name.strip()):  # the page title had no real name
        name = slug.replace("-", " ").replace("_", " ").title()               # "9-mothers" -> "9 Mothers"
    return {"ats": ats, "slug": slug, "name": name} if jobs else None


async def check_all(boards: list[tuple[str, str]]) -> list[dict]:
    limit = asyncio.Semaphore(PARALLEL)
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        rows = await asyncio.gather(*(check_board(client, limit, ats, slug) for ats, slug in boards))
    return [row for row in rows if row]


def main() -> None:
    with open(COMPANIES, encoding="utf-8") as f:
        known = list(csv.DictReader(f))
    have = {(row["ats"], row["slug"]) for row in known}

    print(f"Searching the latest {CRAWLS} Common Crawl indexes...")
    candidates = sorted(boards_in_common_crawl() - have)
    print(f"{len(candidates)} boards we don't have yet. Checking which are live...")
    start = time.perf_counter()
    live = asyncio.run(check_all(candidates))
    print(f"{len(live)} live boards with open jobs ({time.perf_counter() - start:.0f}s)")

    with open(COMPANIES, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ats", "slug", "name"])
        writer.writeheader()
        writer.writerows(known + sorted(live, key=lambda row: (row["ats"], row["slug"])))
    print(f"companies.csv now has {len(known) + len(live)} companies")


if __name__ == "__main__":
    main()