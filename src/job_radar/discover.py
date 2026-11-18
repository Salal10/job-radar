"""Find companies' job boards in Common Crawl (a free public copy of billions of web pages), check each one
is live, and add the new ones to companies.csv.

Run: uv run python -m job_radar.discover        (takes a while; run it weekly, not daily)
"""
import asyncio
import csv
import html
import json
import random
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from job_radar.fetch import COMPANIES, HEADERS, PARALLEL
from job_radar.sources import FETCHERS

CRAWLS = 2  # how many of the latest crawls to search. Tested: older crawls add very few new boards
INDEX = "https://index.commoncrawl.org"
CRAWL_CACHE = Path("data/crawl")

# Common Crawl search -> which ATS fetcher reads it. Most ATS put the company in the path
# (jobs.lever.co/acme), some in the subdomain (acme.recruitee.com).
SEARCHES = {
    "jobs.ashbyhq.com/*": "ashby",
    "jobs.lever.co/*": "lever",
    "jobs.eu.lever.co/*": "lever_eu",
    "boards.greenhouse.io/*": "greenhouse",
    "job-boards.greenhouse.io/*": "greenhouse",
    "boards.eu.greenhouse.io/*": "greenhouse",      # EU boards are still read through the normal Greenhouse API
    "job-boards.eu.greenhouse.io/*": "greenhouse",
    "apply.workable.com/*": "workable",
    "jobs.smartrecruiters.com/*": "smartrecruiters",
    "*.recruitee.com": "recruitee",
    "*.jobs.personio.de": "personio",
}
SUBDOMAIN_BASE = {"recruitee": "recruitee.com", "personio": "jobs.personio.de"}  # company = the part before this
CASE_SENSITIVE = {"smartrecruiters"}  # SmartRecruiters company IDs: "AFRY" works, "afry" doesn't
NOT_COMPANIES = {"embed", "v1", "api", "j", "www", "static", "assets", "favicon.ico", "robots.txt", "sitemap.xml"}


def slug_from(url: str, ats: str) -> str | None:
    """'https://jobs.lever.co/acme/123' -> 'acme'. 'https://acme.recruitee.com/o/x' -> 'acme'.
    Greenhouse embeds look like '/embed/job_board?for=acme'."""
    parts = urlparse(url)
    if ats in SUBDOMAIN_BASE:
        labels = (parts.hostname or "").split(".")
        first = labels[0] if len(labels) > len(SUBDOMAIN_BASE[ats].split(".")) else ""
    else:
        first = parts.path.strip("/").split("/")[0]
        if first.lower() == "embed":
            first = parse_qs(parts.query).get("for", [""])[0]
    if ats not in CASE_SENSITIVE:
        first = first.lower()
    return first if first and first.lower() not in NOT_COMPANIES else None


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


def slugs_for(client: httpx.Client, crawl: str, search: str, ats: str) -> set[str] | None:
    """Every company slug Common Crawl saw for this search in this crawl. None if the index didn't answer."""
    api = f"{INDEX}/{crawl}-index"
    r = get_with_retries(client, api, {"url": search, "output": "json", "showNumPages": "true"})
    if r is None:
        return None
    slugs = set()
    for page in range(r.json()["pages"]):
        r = get_with_retries(client, api, {"url": search, "output": "json", "fl": "url", "page": page})
        if r is None:
            return None  # a missing page means an incomplete answer: don't save it as if it were complete
        for line in r.text.splitlines():
            try:
                slug = slug_from(json.loads(line)["url"], ats)
            except (ValueError, KeyError):  # a cut-off or broken line from the busy index: skip it
                continue
            if slug:
                slugs.add(slug)
    return slugs


def boards_in_common_crawl() -> set[tuple[str, str]]:
    """Each finished (crawl, search) is saved to data/crawl/, so a crash or rerun continues where it stopped."""
    CRAWL_CACHE.mkdir(parents=True, exist_ok=True)
    found = set()
    with httpx.Client(headers=HEADERS, timeout=120) as client:
        crawls = [c["id"] for c in client.get(f"{INDEX}/collinfo.json").json()[:CRAWLS]]
        for crawl in crawls:
            for search, ats in SEARCHES.items():
                saved = CRAWL_CACHE / f"{crawl}_{re.sub(r'[^\w.-]', '', search)}.txt"  # no * or / in file names
                if saved.exists():
                    slugs = set(saved.read_text(encoding="utf-8").split())
                else:
                    slugs = slugs_for(client, crawl, search, ats)
                    if slugs is None:
                        print(f"  {crawl} {search}: index busy, skipped (run again later to fill it in)")
                        continue
                    saved.write_text("\n".join(sorted(slugs)), encoding="utf-8")
                before = len(found)
                found |= {(ats, slug) for slug in slugs}
                print(f"  {crawl} {search}: {len(slugs)} boards, {len(found) - before} not seen before")
    return found


async def board_name(client: httpx.AsyncClient, ats: str, slug: str) -> str:
    """The company's display name. Some APIs return it; for the others, the board page's <title> has it."""
    if ats == "greenhouse":
        return (await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")).json()["name"]
    if ats == "workable":
        return (await client.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")).json()["name"]
    if ats == "smartrecruiters":
        r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 1})
        return r.json()["content"][0]["company"]["name"]
    if ats == "recruitee":
        return (await client.get(f"https://{slug}.recruitee.com/api/offers/")).json()["offers"][0]["company_name"]
    page = {"ashby": f"https://jobs.ashbyhq.com/{slug}", "lever": f"https://jobs.lever.co/{slug}",
            "lever_eu": f"https://jobs.eu.lever.co/{slug}", "personio": f"https://{slug}.jobs.personio.de"}[ats]
    title = re.search(r"<title>([^<]*)</title>", (await client.get(page)).text)
    # "9fin Jobs", "Jobs at Acme", "Jobs bei 1KOMMA5°" (Personio, German) -> the name alone
    return re.sub(r"^Jobs (at|bei) |\s+Jobs$", "", html.unescape(title.group(1))).strip() if title else ""


async def check_board(client: httpx.AsyncClient, limit: asyncio.Semaphore, ats: str, slug: str) -> tuple[dict | None, str]:
    """Is the board live with at least one job? Uses the same fetchers as the daily run.
    Returns (row for companies.csv or None, why). "Dead" and "couldn't check" are different answers:
    a network blip must not look like 1,000 closed companies."""
    async with limit:
        try:
            jobs = await FETCHERS[ats](client, slug, slug)
            name = await board_name(client, ats, slug) if jobs else ""
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            return None, "no board (404)" if code == 404 else f"HTTP {code}"
        except (httpx.TransportError, asyncio.TimeoutError) as e:  # network trouble: not the board's fault
            return None, f"network: {type(e).__name__}"
        except Exception as e:  # not JSON, unexpected shape: probably not a real board
            return None, f"unreadable: {type(e).__name__}"
    if not jobs:
        return None, "no open jobs"
    if re.fullmatch(r"(?i)(jobs|careers|not found.*|404.*|)", name.strip()):  # no real name found
        name = slug.replace("-", " ").replace("_", " ").title()               # "9-mothers" -> "9 Mothers"
    return {"ats": ats, "slug": slug, "name": name}, "live"


async def check_all(boards: list[tuple[str, str]]) -> list[tuple[dict | None, str]]:
    limit = asyncio.Semaphore(PARALLEL)
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        return await asyncio.gather(*(check_board(client, limit, ats, slug) for ats, slug in boards))


def main() -> None:
    with open(COMPANIES, encoding="utf-8") as f:
        known = list(csv.DictReader(f))
    # Compare slugs ignoring case: SmartRecruiters needs the right case for some companies ("AFRY"), but for
    # many both "Dominos" and "dominos" work and return the SAME board: keeping both fetched it twice.
    have = {(row["ats"], row["slug"].lower()) for row in known}

    print(f"Searching the latest {CRAWLS} Common Crawl indexes...")
    candidates = []
    for ats, slug in sorted(boards_in_common_crawl()):  # sorted: "AFRY" before "afry", the right case first
        if (ats, slug.lower()) not in have:
            have.add((ats, slug.lower()))
            candidates.append((ats, slug))
    random.shuffle(candidates)  # mix the ATS platforms, so 8 parallel requests don't all hit one server
    print(f"{len(candidates)} boards we don't have yet. Checking which are live...")
    start = time.perf_counter()
    results = asyncio.run(check_all(candidates))
    live = [row for row, _ in results if row]
    print(f"{len(live)} live boards with open jobs ({time.perf_counter() - start:.0f}s)")
    print("Why the others were dropped:")
    dropped = Counter((ats, why) for (ats, _), (row, why) in zip(candidates, results) if not row)
    for (ats, why), count in dropped.most_common():
        print(f"  {ats:16} {why:28} {count}")

    with open(COMPANIES, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ats", "slug", "name"])
        writer.writeheader()
        writer.writerows(known + sorted(live, key=lambda row: (row["ats"], row["slug"])))
    print(f"companies.csv now has {len(known) + len(live)} companies")


if __name__ == "__main__":
    main()