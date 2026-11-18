"""One fetcher per ATS (applicant tracking system). Each turns that ATS's JSON into the same Job shape."""
import asyncio
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

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


# --- Company career sites without a known ATS: schema.org JobPosting ---------------------------------------
# Sites that want their jobs in Google search put a JSON block like {"@type": "JobPosting", "title": ...}
# in each job page (inside <script type="application/ld+json">). It's a public standard, so one reader
# works for many different career sites.

JOB_LINK = re.compile(r"(?i)job|career|position|vacanc|opening|role|stelle|offre|vagas?|empleo")
MAX_JOB_PAGES = 100  # per company per run: enough for a startup, and polite to a small company's website


def job_postings(page: str) -> list[dict]:
    """Every JobPosting object in a page's JSON-LD blocks (they can be nested, in lists, or in an @graph)."""
    found = []

    def walk(node) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                found.append(node)
            else:
                for value in node.values():
                    walk(value)

    decoder = json.JSONDecoder(strict=False)  # strict=False: allow raw newlines inside strings
    for block in re.findall(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page):
        try:
            walk(decoder.raw_decode(block.strip())[0])  # raw_decode: ignore junk after the JSON, e.g. a stray "};"
        except ValueError:
            continue  # one broken block must not hide the others
    return found


def site_of(host: str | None) -> str:
    """'careers.adyen.com' -> 'adyen.com', 'jobs.acme.co.uk' -> 'acme.co.uk': subdomains are the same company."""
    labels = (host or "").lower().split(".")
    keep = 3 if len(labels) > 2 and labels[-2] in ("co", "com", "org", "net", "ac") and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


def job_links(page: str, page_url: str) -> list[str]:
    """Links on the same site that look like job pages: /careers/backend-engineer, /jobs/123, ..."""
    site = site_of(urlparse(page_url).hostname)
    links = []
    for href in re.findall(r"""href=["']([^"'#]+)""", page):
        url = urljoin(page_url, html.unescape(href))
        parts = urlparse(url)
        if parts.scheme in ("http", "https") and site_of(parts.hostname) == site and JOB_LINK.search(parts.path) \
                and url.rstrip("/") != page_url.rstrip("/") \
                and url not in links and not re.search(r"\.(pdf|jpg|png|svg|css|js)$", parts.path, re.I):
            links.append(url)
    return links[:MAX_JOB_PAGES]


def first(value) -> dict:
    """JSON-LD fields can be one object or a list of them."""
    return (value[0] if value else {}) if isinstance(value, list) else (value or {})


def place_name(place: dict) -> str:
    address = first(place.get("address")) if isinstance(place, dict) else {}
    if isinstance(address, str):
        return address
    country = address.get("addressCountry")
    country = country.get("name", "") if isinstance(country, dict) else country or ""
    return ", ".join(p for p in (address.get("addressLocality"), address.get("addressRegion"), country) if p)


def posting_to_job(p: dict, careers_url: str, page_url: str, name: str) -> Job | None:
    if (p.get("validThrough") or "9999")[:10] < datetime.now(timezone.utc).date().isoformat():
        return None  # the site says this job has closed
    places = [place_name(x) for x in (p.get("jobLocation") if isinstance(p.get("jobLocation"), list)
                                      else [p.get("jobLocation") or {}])]
    remote = "TELECOMMUTE" in str(p.get("jobLocationType", "")).upper()
    allowed = [x.get("name", "") if isinstance(x, dict) else str(x)
               for x in (p.get("applicantLocationRequirements") or [])] \
        if isinstance(p.get("applicantLocationRequirements"), list) \
        else [first(p.get("applicantLocationRequirements")).get("name", "")]
    location = " / ".join(x for x in places if x)
    if remote and any(allowed):
        location = f"{location} / Remote: {', '.join(a for a in allowed if a)}".strip(" /")
    pay = first(p.get("baseSalary"))
    amount = first(pay.get("value")) if isinstance(pay.get("value"), (dict, list)) else {"value": pay.get("value")}
    low, high = amount.get("minValue") or amount.get("value"), amount.get("maxValue")
    url = p.get("url") or page_url
    return Job(
        id=f"jsonld:{careers_url}:{hashlib.md5(url.encode()).hexdigest()[:12]}", source="jsonld",
        company=first(p.get("hiringOrganization")).get("name") or name,
        title=to_text(p.get("title", "")), location=location, workplace="remote" if remote else "",
        department="", url=url, description=to_text(p.get("description", "")),
        posted_at=str(p.get("datePosted") or "")[:10],
        salary=f"{low}-{high} {pay.get('currency', '')} {amount.get('unitText', '')}".strip() if high
               else f"{low} {pay.get('currency', '')} {amount.get('unitText', '')}".strip() if low else "",
    )


async def jsonld(client: httpx.AsyncClient, careers_url: str, name: str) -> list[Job]:
    """For a career site with no supported ATS: read JobPosting data from the careers page itself, then
    from the job pages it links to. Here the "slug" is the careers page URL."""
    r = await get(client, careers_url)
    found = [(p, str(r.url)) for p in job_postings(r.text)]
    if not found:
        limit = asyncio.Semaphore(4)  # a company's own website is small: few requests at once

        async def read(url: str) -> list[tuple[dict, str]]:
            async with limit:
                try:
                    return [(p, url) for p in job_postings((await get(client, url)).text)]
                except httpx.HTTPError:
                    return []  # one broken job page must not lose the others

        for postings in await asyncio.gather(*(read(url) for url in job_links(r.text, str(r.url)))):
            found += postings
    jobs = {}
    for posting, page_url in found:
        job = posting_to_job(posting, careers_url, page_url, name)
        if job and job.title:
            jobs[job.id] = job  # the same job can be on the list page and on its own page
    return list(jobs.values())


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "lever_eu": lever_eu, "ashby": ashby,
            "workable": workable, "smartrecruiters": smartrecruiters, "recruitee": recruitee, "personio": personio,
            "jsonld": jsonld}