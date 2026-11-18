"""Add a company by its careers page URL, for companies Common Crawl didn't find.

Run: uv run python -m job_radar.careers https://acme.com/careers [more URLs...]
     add --dry-run to only see what would be added

Most career pages are a thin layer over an ATS: the page links to (or embeds) jobs.lever.co/acme or loads
a Greenhouse widget. So first look for a known ATS board in the page; if there is one, the company joins
the normal fast fetch. If not, read the schema.org JobPosting data the site publishes for Google.
"""
import asyncio
import csv
import re
import sys
from collections import Counter

import httpx

from job_radar.discover import NOT_COMPANIES, board_name
from job_radar.fetch import COMPANIES, HEADERS
from job_radar.sources import FETCHERS, job_links, job_postings

# Where each supported ATS shows up in a page: links, iframes, script URLs, or API calls.
# The first group is the company's slug.
ATS_PATTERNS = {
    "greenhouse": [r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)",
                   r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"],
    "lever": [r"jobs\.lever\.co/([\w.-]+)", r"(?<!eu\.)api\.lever\.co/v0/postings/([\w.-]+)"],
    "lever_eu": [r"jobs\.eu\.lever\.co/([\w.-]+)", r"api\.eu\.lever\.co/v0/postings/([\w.-]+)"],
    "ashby": [r"jobs\.ashbyhq\.com/([\w.-]+)", r"api\.ashbyhq\.com/posting-api/job-board/([\w.-]+)"],
    "workable": [r"apply\.workable\.com/(?:api/v\d/widget/accounts/)?([\w-]+)"],
    "smartrecruiters": [r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"],
    "recruitee": [r"([\w-]+)\.recruitee\.com"],
    "personio": [r"([\w-]+)\.jobs\.personio\.(?:de|com)"],
}
# ATS we recognise but can't read yet. Counting them tells us which one is worth adding next.
UNSUPPORTED = {
    "workday": r"\.myworkdayjobs\.com", "bamboohr": r"[\w-]+\.bamboohr\.com", "teamtailor": r"\.teamtailor\.com",
    "breezy": r"[\w-]+\.breezy\.hr", "jazzhr": r"\.applytojob\.com", "join": r"join\.com/companies/",
    "jobvite": r"jobs\.jobvite\.com", "icims": r"\.icims\.com", "taleo": r"\.taleo\.net",
    "successfactors": r"successfactors\.(?:com|eu)", "pinpoint": r"\.pinpointhq\.com", "rippling": r"ats\.rippling\.com",
}
NOT_SLUGS = NOT_COMPANIES | {"app", "careers", "jobs", "widget", "static", "cdn"}


def ats_boards(page: str, url: str) -> list[tuple[str, str]]:
    """Every (ats, slug) the page points to, most mentioned first."""
    text = f"{url} {page}".replace("\\/", "/")  # JSON inside scripts writes / as \/
    seen = Counter()
    for ats, patterns in ATS_PATTERNS.items():
        for pattern in patterns:
            for slug in re.findall(pattern, text, re.I):
                slug = slug if ats == "smartrecruiters" else slug.lower()
                if slug.lower() not in NOT_SLUGS:
                    seen[(ats, slug)] += 1
    return [board for board, _ in seen.most_common()]


LISTING = re.compile(r"(?i)jobs|openings|positions|vacanc|open-roles|opportunit|join")
DEEPER_PAGES = 10  # how many linked pages to open when the careers page itself shows no board


async def live_board(client: httpx.AsyncClient, boards: list[tuple[str, str]]) -> tuple[dict | None, str]:
    """The first board that really has jobs: pages also link old, empty, or other companies' boards."""
    for ats, slug in boards:
        try:
            jobs = await FETCHERS[ats](client, slug, slug)
        except Exception:
            continue
        if jobs:
            name = await board_name(client, ats, slug)
            return {"ats": ats, "slug": slug, "name": name or slug}, f"{ats} board '{slug}' with {len(jobs)} jobs"
    return None, ""


async def find_board(client: httpx.AsyncClient, url: str) -> tuple[dict | None, str]:
    """Returns (row for companies.csv or None, what we found)."""
    try:
        r = await client.get(url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return None, f"page not readable: {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}"
    page, final = r.text, str(r.url)

    # 1. A supported ATS board linked from the careers page.
    row, what = await live_board(client, ats_boards(page, final))
    if row:
        return row, what

    # 2. Often the board is one click deeper ("See open positions"). Open the likeliest links.
    links = sorted(job_links(page, final), key=lambda link: not LISTING.search(link))[:DEEPER_PAGES]
    deeper = {}
    for link in links:
        try:
            deeper[link] = (await client.get(link)).text
        except httpx.HTTPError:
            continue
        row, what = await live_board(client, ats_boards(deeper[link], link))
        if row:
            return row, f"{what} (found on {link})"

    # 3. No ATS: JobPosting data, read from the careers page or from a jobs list one click deeper.
    #    Keep the page that gives the most jobs: a careers page often shows only a few "featured" ones.
    best, best_jobs = final, []
    for start in [final] + [link for link in deeper if LISTING.search(link)][:3]:
        jobs = await FETCHERS["jsonld"](client, start, "")
        if len(jobs) > len(best_jobs):
            best, best_jobs = start, jobs
    if best_jobs:
        return {"ats": "jsonld", "slug": best, "name": best_jobs[0].company}, \
            f"JobPosting data: {len(best_jobs)} jobs" + ("" if best == final else f" (from {best})")

    # 4. Nothing we can read. Say why, so we know what to build next.
    everything = page + "".join(deeper.values())
    other = [name for name, pattern in UNSUPPORTED.items() if re.search(pattern, everything, re.I)]
    if other:
        return None, f"uses {', '.join(other)} (not supported yet)"
    if re.search(r"(?i)itemtype=\"https?://schema\.org/JobPosting", everything):
        return None, "JobPosting as microdata (HTML attributes), not supported yet"
    if not links:
        return None, "no job links in the HTML (jobs probably load with JavaScript)"
    return None, "job links found, but no ATS and no JobPosting data"


async def find_all(urls: list[str]) -> list[tuple[dict | None, str]]:
    browser_like = HEADERS | {"Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(headers=browser_like, timeout=30, follow_redirects=True) as client:
        return await asyncio.gather(*(find_board(client, url) for url in urls))


def main() -> None:
    urls = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if not urls:
        sys.exit("Usage: uv run python -m job_radar.careers https://acme.com/careers [--dry-run]")

    with open(COMPANIES, encoding="utf-8") as f:
        known = list(csv.DictReader(f))
    have = {(row["ats"], row["slug"]) for row in known}

    new = []
    for url, (row, what) in zip(urls, asyncio.run(find_all(urls))):
        status = "" if row is None else " (already in companies.csv)" if (row["ats"], row["slug"]) in have \
            else " -> new" if dry_run else " -> added"
        print(f"{url}\n    {what}{status}")
        if row and (row["ats"], row["slug"]) not in have:
            new.append(row)
            have.add((row["ats"], row["slug"]))

    if new and not dry_run:
        with open(COMPANIES, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=["ats", "slug", "name"]).writerows(new)
    print(f"\n{len(new)} new {'(dry run, nothing saved)' if dry_run else 'added to companies.csv'}")


if __name__ == "__main__":
    main()
