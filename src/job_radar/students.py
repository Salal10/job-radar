"""Working-student and internship jobs in Germany, any field (turn on with [working_student] in profiles.toml).

Sources:
  - our own fetch: company career pages already in data/jobs.db
  - Bundesagentur für Arbeit: the German job agency's public job search API (the biggest source)
  - Arbeitnow: a German job board with a free public API
LinkedIn, Indeed, StepStone and englishjobs.de have no free API and don't allow automated collection, so the web
page shows ready-made search links for them instead.

Run: uv run python -m job_radar.students   (the daily command runs it too, when the option is on)
"""
import asyncio
import re
import sys
import tomllib
from base64 import b64encode
from datetime import date, datetime, timedelta, timezone

import httpx

from job_radar import check, store
from job_radar.match import PROFILES
from job_radar.places import countries_in
from job_radar.sources import get, to_text
from job_radar.sponsors import normalize

BA_SEARCH = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
BA_DETAILS = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/"
BA_PAGE = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"
BA_KEY = {"X-API-Key": "jobboerse-jobsuche"}  # the public key of the agency's own job search app
# The API only accepts some "published in the last N days" values (1, 7, 14, ...): ask for 7, cut to max_age_days here
BA_QUERIES = [{"was": "Werkstudent"}, {"was": "Working Student"}, {"was": "studentische Hilfskraft"},
              {"angebotsart": 34}]  # 34 = Praktikum / Trainee
ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
DEFAULTS = {"enabled": False, "max_age_days": 5, "top": 100, "avoid": [], "cities": [], "prefer": []}

SCHEMA = """CREATE TABLE IF NOT EXISTS student_jobs (
    id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, location TEXT, url TEXT,
    posted_at TEXT, description TEXT,
    lang TEXT,          -- de-required / de-ad / en / "" (unknown)
    seen_on TEXT,       -- the last run that found it
    remote TEXT         -- remote / hybrid / onsite / "" (not stated)
)"""


def ensure_table(db) -> None:
    db.execute(SCHEMA)
    if "remote" not in [row[1] for row in db.execute("PRAGMA table_info(student_jobs)")]:  # made before the badge
        db.execute("ALTER TABLE student_jobs ADD COLUMN remote TEXT DEFAULT ''")

# Student roles in German and English. "intern" only as a whole word: not "internal" or "international".
STUDENT = re.compile(r"(?i)werkstudent|working student|studentische?r? (?:hilfskraft|mitarbeiter|aushilfe)|"
                     r"student(?:ische)? assistant|studentenjob|\bhiwi\b|praktik|\bintern\b|internship")
# "(m/w/d)", "(m/f/d)", "(w/m/div.)", "(all genders)": German ads must name all genders. Removed from titles.
GENDER = re.compile(r"(?i)\s*[(\[]\s*(?:all genders?|(?:m|w|f|d|x|div\.?|gn|diverse|\*)(?:\s*[/|,*:]\s*"
                    r"(?:m|w|f|d|x|div\.?|gn|diverse|\*))+)\s*[)\]]")
# Language: the ad's own words decide. German first: "fluent German and English" means German is required.
ENGLISH_OK = re.compile(r"(?i)no german (?:required|needed|necessary)|german (?:is )?(?:not required|not necessary|"
                        r"(?:is )?a plus|nice to have|beneficial|an advantage)|(?:working|company|office) language is "
                        r"english|english is our (?:working|company|office) language|keine deutschkenntnisse")
GERMAN_REQUIRED = re.compile(
    r"(?i)(?:fließend|fliessend|sehr gut|verhandlungssicher|gut)\w*\s+(?:deutsch|german)|deutschkenntnisse|"
    r"fluent(?:ly)?\s+(?:in\s+)?german|(?:excellent|very good|good|strong|business|native)[\w\s-]{0,15}german|"
    r"german\s+(?:\w+\s+){0,3}(?:required|essential|mandatory|is a must)|(?:deutsch|german)\s*\(?\s*(?:c1|c2|b2)")
# Where the work happens. Fully remote needs clear words: "remote" alone is often "remote teams" or "remote support".
FULL_REMOTE = re.compile(r"(?i)100\s?%\s?(?:remote|home\s?office|mobil)|fully remote|full[- ]remote|remote[- ]first|"
                         r"(?:vollständig|komplett|ausschließlich)\s+(?:remote|im homeoffice|mobil)|"
                         r"(?:this|the) (?:role|position|job) is (?:fully )?remote|remote position|remote-stelle")
HYBRID = re.compile(r"(?i)hybrid|home\s?-?office|mobiles? arbeiten|mobile work|work(?:ing)? from home|"
                    r"remote[- ](?:option|möglich|work(?:ing)? (?:is )?possible)|(?:teilweise|mostly|partly|partially) remote|"
                    r"remote days?")
ONSITE = re.compile(r"(?i)\bon[- ]?site\b|\bvor ort\b|in[- ]office\b|in[- ]person\b|präsenzpflicht|in präsenz")
GERMAN_WORDS = re.compile(r"(?i)\b(?:und|der|die|das|wir|sie|für|mit|ihre|deine|bei|eine|oder)\b")
ENGLISH_WORDS = re.compile(r"(?i)\b(?:and|the|we|you|for|with|your|our|are|will|of|to)\b")


def config() -> dict:
    """The [working_student] block of profiles.toml, with defaults. Off unless enabled = true."""
    data = tomllib.loads(PROFILES.read_text(encoding="utf-8")) if PROFILES.exists() else {}
    return DEFAULTS | data.get("working_student", {})


def preferred(title: str, words: list[str]) -> str:
    """The first prefer word in the title, or ''. Long words match inside German compounds ("Software" in
    "Softwareentwicklung"); short ones like "AI", "IT", "QA" only as a whole word with the same capitals
    ("IT-Support" yes, "Aircraft" or "with" no)."""
    for word in words:
        if len(word) <= 3:
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", title):
                return word
        elif word.lower() in title.lower():
            return word
    return ""


def clean_title(title: str) -> str:
    return re.sub(r"\s{2,}", " ", GENDER.sub("", title)).strip(" -–|,")


def language(text: str) -> str:
    """'en' (English ad, no German needed), 'de-required', 'de-ad' (written in German), or '' (no text)."""
    if len(text) < 200:
        return ""
    if ENGLISH_OK.search(text):
        return "en"
    if GERMAN_REQUIRED.search(text):
        return "de-required"
    return "de-ad" if len(GERMAN_WORDS.findall(text)) > len(ENGLISH_WORDS.findall(text)) else "en"


def workplace(hint: str, location: str, text: str) -> str:
    """'remote', 'hybrid', 'onsite' or '' (the ad doesn't say). hint = what the source itself says, if anything."""
    if FULL_REMOTE.search(text) or re.search(r"(?i)^\s*(?:remote|home\s?office)\b", location or ""):
        return "remote"
    hybrid, onsite = HYBRID.search(location or "") or HYBRID.search(text), ONSITE.search(text)
    if hint == "remote" and not hybrid and not onsite:  # a source's "remote" flag loses to the ad's own words
        return "remote"
    if hint in ("remote", "hybrid") or hybrid:
        return "hybrid"
    if hint == "onsite" or onsite:
        return "onsite"
    return ""


def in_germany(location: str, title: str, description: str) -> bool:
    places = countries_in(location)
    if places:
        return "Germany" in places
    # A small town our place list doesn't know: an ad with "(m/w/d)" that mentions Germany is German
    return bool(GENDER.search(title)) and bool(re.search(r"(?i)deutschland|germany", description or ""))


def from_our_jobs(db, cutoff: str) -> list[dict]:
    rows = db.execute("""SELECT id, source, company, title, location, url, COALESCE(NULLIF(posted_at, ''), first_seen),
                                description, workplace FROM jobs WHERE closed_on IS NULL
                         AND COALESCE(NULLIF(posted_at, ''), first_seen) >= ?""", (cutoff,)).fetchall()
    return [dict(id=r[0], source=r[1], company=r[2], title=r[3], location=r[4], url=r[5], posted_at=r[6][:10],
                 description=r[7] or "", hint=(r[8] or "").lower()) for r in rows if STUDENT.search(r[3]) and in_germany(r[4], r[3], r[7])]


async def from_agency(client: httpx.AsyncClient, cutoff: str) -> list[dict]:
    jobs = {}
    for query in BA_QUERIES:
        for page in range(1, 51):
            r = await get(client, BA_SEARCH, params=query | {"veroeffentlichtseit": 7, "size": 100, "page": page},
                          headers=BA_KEY)
            results = r.json().get("ergebnisliste", [])
            for j in results:
                posted = j.get("datumErsteVeroeffentlichung") or j.get("veroeffentlichungszeitraum", {}).get("von", "")
                if posted < cutoff or not STUDENT.search(j.get("stellenangebotsTitel", "")):
                    continue  # "Praktikum" also finds "Trainee" posts; keep the student ones
                place = (j.get("stellenlokationen") or [{}])[0].get("adresse", {})
                jobs[j["referenznummer"]] = dict(
                    id=f"ba:{j['referenznummer']}", source="arbeitsagentur", company=j.get("firma", ""),
                    title=j["stellenangebotsTitel"], location=", ".join(filter(None, (place.get("ort"), "Germany"))),
                    url=BA_PAGE + j["referenznummer"], posted_at=posted[:10], description="",
                    hint="hybrid" if j.get("homeofficemoeglich") else "")  # "home office possible"
            if len(results) < 100:
                break
    return list(jobs.values())


async def agency_description(client: httpx.AsyncClient, job: dict, limit: asyncio.Semaphore) -> None:
    """The search results have no text; the details call (one per job) does. Done once: it's kept in the table."""
    async with limit:
        try:
            refnr = job["id"].removeprefix("ba:")
            r = await get(client, BA_DETAILS + b64encode(refnr.encode()).decode(), headers=BA_KEY)
            job["description"] = to_text(r.json().get("stellenangebotsBeschreibung", ""))
        except (httpx.HTTPError, ValueError):
            pass  # no text: the language badge says "unknown"


async def from_arbeitnow(client: httpx.AsyncClient, cutoff: str) -> list[dict]:
    jobs = []
    for page in range(1, 31):  # newest first: stop at the first page that reaches older jobs
        data = (await get(client, ARBEITNOW, params={"page": page})).json().get("data", [])
        for j in data:
            posted = datetime.fromtimestamp(j["created_at"], timezone.utc).date().isoformat()
            student = STUDENT.search(j["title"]) or any(STUDENT.search(t) for t in j.get("job_types", []))
            description = to_text(j.get("description", ""))
            if posted >= cutoff and student and in_germany(j.get("location", "") or "Germany", j["title"], description):
                jobs.append(dict(id=f"arbeitnow:{j['slug']}", source="arbeitnow", company=j["company_name"],
                                 title=j["title"], location=j.get("location", ""), url=j["url"], posted_at=posted,
                                 description=description, hint="remote" if j.get("remote") else ""))
        if not data or datetime.fromtimestamp(data[-1]["created_at"], timezone.utc).date().isoformat() < cutoff:
            break
    return jobs


def dedupe(jobs: list[dict]) -> list[dict]:
    """The same job from two sources is shown once: the company's own page first, then Arbeitnow, then the agency."""
    order = {"arbeitnow": 1, "arbeitsagentur": 2}
    seen, out = set(), []
    for job in sorted(jobs, key=lambda j: order.get(j["source"], 0)):
        key = (normalize(job["company"]), re.sub(r"[^a-z0-9]+", " ", clean_title(job["title"]).lower()).strip())
        if key not in seen:
            seen.add(key)
            out.append(job)
    return out


async def collect(db, cutoff: str, jobs: list[dict]) -> list[dict]:
    print(f"  our company pages: {len(jobs)}")
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "job-radar"}) as client:
        for name, fetch in (("Bundesagentur für Arbeit", from_agency), ("Arbeitnow", from_arbeitnow)):
            try:
                found = await fetch(client, cutoff)
                print(f"  {name}: {len(found)}")
                jobs += found
            except Exception as e:  # one source down must not stop the others
                print(f"  {name}: failed ({type(e).__name__}: {str(e)[:80]})")
        jobs = dedupe(jobs)
        # Agency jobs need one extra call for their text; reuse the text we already have from earlier runs
        known = dict(db.execute("SELECT id, description FROM student_jobs WHERE description != ''").fetchall())
        missing = []
        for job in jobs:
            if job["source"] == "arbeitsagentur":
                job["description"] = known.get(job["id"], "")
                if not job["description"]:
                    missing.append(job)
        print(f"  job texts to download from the agency: {len(missing)}")
        limit = asyncio.Semaphore(4)  # polite: 4 at a time
        await asyncio.gather(*(agency_description(client, job, limit) for job in missing))
    return jobs


def main() -> None:
    settings = config()
    if not settings["enabled"]:
        print("Working-student search is off. Turn it on with [working_student] enabled = true in profiles.toml.")
        return
    db = store.connect()
    ensure_table(db)
    today = date.today()
    cutoff = (today - timedelta(days=settings["max_age_days"])).isoformat()
    ours = from_our_jobs(db, cutoff)
    # SmartRecruiters lists have no job text: download it once (it's stored) so the language badge works
    check.fill_descriptions(db, [j["id"] for j in ours if not j["description"]])
    jobs = asyncio.run(collect(db, cutoff, from_our_jobs(db, cutoff)))
    for job in jobs:
        job["lang"] = language(job["description"])
        job["remote"] = workplace(job.pop("hint"), job["location"], job["description"])
        job["seen_on"] = today.isoformat()
    db.execute("DELETE FROM student_jobs")  # a fresh list each run: old ones are past max_age_days anyway
    db.executemany("""INSERT INTO student_jobs VALUES (:id, :source, :company, :title, :location, :url, :posted_at,
                                                       :description, :lang, :seen_on, :remote)""", jobs)
    db.commit()
    english = sum(j["lang"] == "en" for j in jobs)
    print(f"{len(jobs)} working-student / internship jobs in Germany from the last {settings['max_age_days']} days "
          f"({english} in English without German required; remote {sum(j['remote'] == 'remote' for j in jobs)}, "
          f"hybrid {sum(j['remote'] == 'hybrid' for j in jobs)}).")


if __name__ == "__main__":
    sys.exit(main())
