"""One fetcher per ATS (applicant tracking system). Each turns that ATS's JSON into the same Job shape."""
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


@dataclass
class Job:
    id: str            # "greenhouse:gitlab:8638232002": unique across all sources
    source: str        # which ATS: greenhouse, lever, ashby
    company: str
    title: str
    location: str      # raw text as the company wrote it, e.g. "Remote - European Union"
    workplace: str     # remote / hybrid / onsite, or "" when the ATS doesn't say
    department: str
    url: str           # the job post on the company's own board: where "Open" takes you
    description: str   # plain text, for search and classifying later
    posted_at: str     # ISO date, "" if unknown
    salary: str        # as written, "" if not shown


def to_text(markup: str) -> str:
    """HTML -> plain text. Greenhouse sends HTML that is itself HTML-escaped, so unescape first."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(markup or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


async def greenhouse(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    r.raise_for_status()
    return [Job(
        id=f"greenhouse:{slug}:{j['id']}", source="greenhouse", company=name,
        title=j["title"], location=(j.get("location") or {}).get("name", ""), workplace="",
        department=", ".join(d["name"] for d in j.get("departments", [])),
        url=j["absolute_url"], description=to_text(j.get("content", "")),
        posted_at=(j.get("first_published") or j.get("updated_at") or "")[:10], salary="",
    ) for j in r.json()["jobs"]]


async def lever(client: httpx.AsyncClient, slug: str, name: str) -> list[Job]:
    r = await client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    r.raise_for_status()
    jobs = []
    for j in r.json():
        cats = j.get("categories", {})
        created = j.get("createdAt")  # milliseconds since 1970
        pay = j.get("salaryRange")
        jobs.append(Job(
            id=f"lever:{slug}:{j['id']}", source="lever", company=name,
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
    r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                         params={"includeCompensation": "true"})
    r.raise_for_status()
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


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}