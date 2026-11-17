"""One fetcher per ATS (applicant tracking system). Each turns that ATS's JSON into the same Job shape."""
import asyncio
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


@dataclass
class Job:
    id: str            # "greenhouse:gitlab:8638232002": unique across all sources
    source: str        # which ATS: greenhouse, lever, ashby, workable, ...
    company: str
    title: str
    location: str      # raw text as the company wrote it, e.g. "Remote - European Union"
    workplace: str     # remote / hybrid / onsite, or "" when the ATS doesn't say
    department: str
    url: str           # the job post on the company's own board: where "Open" takes you
    description: str   # plain text, for search and classifying later
    posted_at: str     # ISO date, "" if unknown
    salary: str        # as written, "" if not shown


async def get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with polite retries. 429 = "too many requests", 5xx = server trouble, and a dropped or slow
    connection are all temporary: wait, then try again. If the server says how long to wait
    (Retry-After header), wait that long."""
    for attempt in range(4):
        try:
            r = await client.get(url, **kwargs)
        except httpx.TransportError:  # connection dropped or timed out (big boards, many pages)
            if attempt == 3:
                raise
            await asyncio.sleep(5 * 2 ** attempt)
            continue
        if r.status_code != 429 and r.status_code < 500:
            break
        retry_after = r.headers.get("Retry-After", "")
        await asyncio.sleep(min(int(retry_after) if retry_after.isdigit() else 5 * 2 ** attempt, 60))
    r.raise_for_status()  # still failing after the retries (or a 404): let the caller decide what that means
    return r


def to_text(markup: str) -> str:
    """HTML -> plain text. Greenhouse sends HTML that is itself HTML-escaped, so unescape first."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(markup or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


async def greenhouse(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    r = await get(client, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    return [Job(
        id=f"greenhouse:{slug}:{j['id']}", source="greenhouse", company=name,
        title=j["title"], location=(j.get("location") or {}).get("name", ""), workplace="",
        department=", ".join(d["name"] for d in j.get("departments", [])),
        url=j["absolute_url"], description=to_text(j.get("content", "")),
        posted_at=(j.get("first_published") or j.get("updated_at") or "")[:10], salary="",
    ) for j in r.json()["jobs"]]


async def lever(client: httpx.AsyncClient, slug: str, name: str,
                api: str = "api.lever.co", source: str = "lever") -> list[Job]:
    r = await get(client, f"https://{api}/v0/postings/{slug}", params={"mode": "json"})
    jobs = []
    for j in r.json():
        cats = j.get("categories", {})
        created = j.get("createdAt")  # milliseconds since 1970
        pay = j.get("salaryRange")
        jobs.append(Job(
            id=f"{source}:{slug}:{j['id']}", source=source, company=name,
            title=j["text"], location=" / ".join(cats.get("allLocations") or [cats.get("location", "")]),
            workplace="" if j.get("workplaceType") in (None, "unspecified") else j["workplaceType"],
            department=cats.get("department") or cats.get("team") or "",
            url=j["hostedUrl"],
            description=f"{j.get('descriptionPlain', '')} {j.get('additionalPlain', '')}".strip(),
            posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat() if created else "",
            salary=f"{pay['min']}-{pay['max']} {pay.get('currency', '')}".strip() if pay else "",
        ))
    return jobs


async def ashby(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    r = await get(client, f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                  params={"includeCompensation": "true"})
    jobs = []
    for j in r.json()["jobs"]:
        if not j.get("isListed", True):
            continue
        places = [j.get("location", "")] + [s["location"] for s in j.get("secondaryLocations", [])]
        jobs.append(Job(
            id=f"ashby:{slug}:{j['id']}", source="ashby", company=name,
            title=j["title"], location=" / ".join(p for p in places if p),
            workplace=(j.get("workplaceType") or "").lower(),
            department=j.get("department") or j.get("team") or "",
            url=j["jobUrl"], description=j.get("descriptionPlain", ""),
            posted_at=(j.get("publishedAt") or "")[:10],
            salary=(j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or "",
        ))
    return jobs


async def lever_eu(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    """Lever keeps EU companies' data on a separate server; same JSON shape."""
    return await lever(client, slug, name, api="api.eu.lever.co", source="lever_eu")


async def workable(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    r = await get(client, f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
    jobs: dict[str, Job] = {}
    for j in r.json()["jobs"]:
        places = [", ".join(p for p in (place.get("city"), place.get("country")) if p)
                  for place in j.get("locations") or [j]]  # older boards only have city/country on the job itself
        location = " / ".join(p for p in places if p)
        if j["shortcode"] in jobs:  # Workable lists one job once per country: merge, don't keep only the last
            same = jobs[j["shortcode"]]
            if location and location not in same.location:
                same.location = f"{same.location} / {location}" if same.location else location
            continue
        jobs[j["shortcode"]] = Job(
            id=f"workable:{slug}:{j['shortcode']}", source="workable", company=name,
            title=j["title"], location=location,
            workplace="remote" if j.get("telecommuting") else "",
            department=j.get("department") or "", url=j["url"], description=to_text(j.get("description", "")),
            posted_at=(j.get("published_on") or "")[:10], salary="",
        )
    return list(jobs.values())


async def smartrecruiters(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    """100 jobs per page. The list has no description (that needs one extra call per job), so we leave it
    empty here and fetch descriptions later only for jobs that pass the filters."""
    jobs, offset = [], 0
    while True:
        r = await get(client, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                      params={"limit": 100, "offset": offset})
        page = r.json()
        for j in page["content"]:
            place = j.get("location") or {}
            jobs.append(Job(
                id=f"smartrecruiters:{slug}:{j['id']}", source="smartrecruiters", company=name,
                title=j["name"], location=place.get("fullLocation") or place.get("city") or "",
                workplace="remote" if place.get("remote") else "hybrid" if place.get("hybrid") else "",
                department=(j.get("department") or {}).get("label", ""),
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", description="",
                posted_at=(j.get("releasedDate") or "")[:10], salary="",
            ))
        offset += 100
        if offset >= page["totalFound"] or not page["content"]:
            return jobs


async def recruitee(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    r = await get(client, f"https://{slug}.recruitee.com/api/offers/")
    jobs = []
    for j in r.json()["offers"]:
        pay = j.get("salary") or {}
        jobs.append(Job(
            id=f"recruitee:{slug}:{j['id']}", source="recruitee", company=name,
            title=j["title"], location=j.get("location") or "",
            workplace="remote" if j.get("remote") else "hybrid" if j.get("hybrid") else "onsite" if j.get("on_site") else "",
            department=j.get("department") or "", url=j["careers_url"],
            description=to_text(f"{j.get('description', '')} {j.get('requirements', '')}"),
            posted_at=(j.get("published_at") or "")[:10],
            salary=f"{pay['min']}-{pay['max']} {pay.get('currency') or ''} {pay.get('period') or ''}".strip()
                   if pay.get("min") or pay.get("max") else "",
        ))
    return jobs


async def personio(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    """Personio publishes XML, not JSON. Parse the raw bytes: some boards contain broken characters."""
    r = await get(client, f"https://{slug}.jobs.personio.de/xml")
    jobs = []
    for j in ET.fromstring(r.content).findall("position"):
        text = " ".join(part.findtext("value") or "" for part in j.iter("jobDescription"))
        jobs.append(Job(
            id=f"personio:{slug}:{j.findtext('id')}", source="personio", company=name,
            title=j.findtext("name") or "", location=j.findtext("office") or "", workplace="",
            department=j.findtext("department") or "",
            url=f"https://{slug}.jobs.personio.de/job/{j.findtext('id')}", description=to_text(text),
            posted_at=(j.findtext("createdAt") or "")[:10], salary="",
        ))
    return jobs


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "lever_eu": lever_eu, "ashby": ashby,
            "workable": workable, "smartrecruiters": smartrecruiters, "recruitee": recruitee, "personio": personio}