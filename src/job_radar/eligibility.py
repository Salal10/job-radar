"""Can someone living in Pakistan take this job? Rules first: fast, free, and they explain themselves.

For every open job:
  from_pakistan  yes / no / maybe : do it without moving (remote from Pakistan, or the office is in Pakistan)
  visa           yes / no / unknown : does the post offer visa sponsorship?
  relocation     yes / unknown      : does it offer relocation support?
  regions        where the job is: "Europe,Middle East"
  how            which rule decided, so every answer can be checked
"maybe" and "unknown" are honest answers: the post doesn't say. An LLM looks only at those (later).

Run: uv run python -m job_radar.eligibility
"""
import re
import sqlite3
import time
from collections import Counter
from datetime import date

from job_radar import places, store

PAKISTAN = "Pakistan"
# Region words that may or may not include Pakistan. "APAC" and "EMEA" sometimes do, so: maybe.
MAYBE_INCLUDES_PAKISTAN = {places.ASIA, places.MIDDLE_EAST}

# --- description phrases (lowercase). Each has a cheap "word" check first: regex on 1 GB of text is slow ---
ANYWHERE = ("anywhere", re.compile(
    r"work (from |remotely from )?anywhere|anywhere in the world|"
    r"(work|working|live|living|join us|based|apply|hire you) from any(where| country| location)|"  # not "read from anywhere"
    r"hire (talent |people )?(from )?(anywhere|globally|worldwide)|fully distributed (team|company)|"
    r"(location|geographically)[- ]independent|regardless of (your )?location|no matter where you (are|live)"))
RESTRICTED = ("based", re.compile(
    r"(must|need to|required to|should|have to) (currently )?(be )?(based|located|resid\w+|living|live) in ([^.;:]{3,60})|"
    r"(open|available) (only )?to (candidates|applicants|residents|people) (who are )?(based |located |living )?in ([^.;:]{3,60})|"
    r"based in ([^.;:]{3,40}) and (legally )?(authori[sz]ed|eligible) to work"))
# "Eligible locations: Bangladesh, Brazil, ..., Pakistan, ..." - Pakistan named in a list of allowed places.
PAKISTAN_LISTED = ("pakistan", re.compile(
    r"(eligib\w*|locations?|countries|based in|open to|hiring in|candidates from)[^.]{0,200}\bpakistan\b"))
RESTRICTED_SHORT = re.compile(r"\b(us|u\.s\.|usa|uk|eu|canada|europe)[- ]only\b|"
                              r"\bonly (hire|hiring|accept\w*) (candidates |applicants )?(in|from) ([^.;:]{3,40})")
UTC_PLUS_5 = re.compile(r"\b(utc|gmt) ?\+ ?5\b|\bpkt\b|pakistan (standard )?time")

VISA_YES = ("sponsor", re.compile(
    r"visa sponsorship (is )?(available|provided|offered|possible)(?!:?\s*no\b)|(we|will|can|do) (also )?(sponsor|provide|support) "
    r"(a |your |the )?(work |skilled worker |h-?1b )?visas?|sponsor(ship)? (for |of )?(your |a )?(work |skilled worker )?visa|"
    r"visa (support|assistance|sponsorship) (and|&) relocation|offer\w* visa sponsorship"))
VISA_NO = ("sponsor", re.compile(
    r"(not|unable to|cannot|can.t|won.t|will not|do not|does not|don.t|aren.t|are not|is not|isn.t) "
    r"(be |currently |presently )?(able to |in a position to )?(provide |offer |support )?(any )?(visa |immigration |employment )?sponsor|"
    r"(not|n.t) (now or in the future |currently or in the future )?(require|need) (visa |employment |immigration )?sponsorship|"
    r"sponsorship (is )?(not|un)(available| available| provided| offered)|without (the need for )?(current or future )?(visa )?sponsorship|"
    r"no (visa )?sponsorship|sponsorship available:\s*no\b"))  # "Visa Sponsorship Available: No"
RIGHT_TO_WORK = re.compile(r"must (already )?(have|hold|possess) (the |a |valid )?(full |existing )?(right|authori[sz]ation|permit) to work")
RELOCATION = ("relocat", re.compile(
    r"relocation (support|package|assistance|bonus|budget|allowance|stipend|help)|help(ing)? you (to )?relocate|"
    r"(support|assist|cover|pay)\w* (for |with |your )*relocat|relocation (is )?(available|provided|offered|covered)"))


def find(rule: tuple[str, re.Pattern], text: str) -> re.Match | None:
    word, pattern = rule
    return pattern.search(text) if word in text else None


def snippet(text: str, match: re.Match) -> str:
    return text[max(0, match.start() - 40): match.end() + 40].strip()


def limited_to(text_after: str) -> set[str]:
    """'anywhere within the united states' -> {'North America'}: 'anywhere' followed by a place is not anywhere."""
    m = re.match(r"[^.;]{0,15}?\b(in|within|across|throughout|inside)\b( the)? ([^.;,]{2,40})", text_after)
    return places.regions_in(m.group(3).upper()) if m else set()  # text is lowercase; "us" here means US


def from_pakistan(location: str, workplace: str, text: str, title: str = "") -> tuple[str, str, str]:
    """(answer, rule, evidence). Location first (structured, short), then the title, then the description."""
    countries, regions = places.countries_in(location), places.regions_in(location)
    if not regions and (in_title := re.search(r"\(([^)]*)\)|[-–|] ?(remote[^|]*)$", title, re.I)):
        where = in_title.group(1) or in_title.group(2)  # "Engineer (Remote - US)", "Engineer - Remote, Europe"
        countries, regions = places.countries_in(where), places.regions_in(where)
        location = f"{location} [title: {where}]"
    remote = workplace == "remote" or places.is_remote(location)

    if PAKISTAN in countries:
        return "yes", "location is in Pakistan", location
    if not remote:
        return "no", "not remote", f"{workplace or 'no remote word'}: {location}"
    if m := find(PAKISTAN_LISTED, text):  # the location field is often shorter than the real list
        return "yes", "description: Pakistan listed", snippet(text, m)
    if places.is_anywhere(location) and not countries:  # "Anywhere in France" is France
        if m := find(RESTRICTED, text):  # "World Wide - Remote" ... "based in the US and authorized to work"
            named = places.regions_in(m.group(0).upper())
            if named and not named & MAYBE_INCLUDES_PAKISTAN:
                return "no", "location says anywhere, description limits it", snippet(text, m)
        return "yes", "remote anywhere (location)", location
    if regions:  # "Remote - US", "Remote, EMEA"
        if regions & MAYBE_INCLUDES_PAKISTAN and not countries - {PAKISTAN}:
            return "maybe", "remote in a region that may include Pakistan", location
        return "no", "remote, limited to other countries", location

    # Plain "Remote": the description has to say where.
    if m := UTC_PLUS_5.search(text):
        return "yes", "time zone fits Pakistan", snippet(text, m)
    if m := (find(RESTRICTED, text) or RESTRICTED_SHORT.search(text)):
        where = m.group(0)
        if PAKISTAN in places.countries_in(where):
            return "yes", "description: open to Pakistan", snippet(text, m)
        named = places.regions_in(where)
        if named and not named & MAYBE_INCLUDES_PAKISTAN:
            return "no", "description: remote but limited to other countries", snippet(text, m)
        return "maybe", "description: limited, but unclear where", snippet(text, m)
    if m := find(ANYWHERE, text):
        if (where := limited_to(text[m.end():])) and not where & MAYBE_INCLUDES_PAKISTAN:
            return "no", "description: anywhere, but within other countries", snippet(text, m)
        return "yes", "description: remote from anywhere", snippet(text, m)
    if not text:
        return "maybe", "remote, no description to check", location
    return "maybe", "remote, location not stated", location


def visa_and_relocation(text: str) -> tuple[str, str, str]:
    """(visa, relocation, evidence). If a post both offers and refuses sponsorship ("we sponsor in the UK,
    not in the US"), that's not a rule's call: unknown."""
    yes, no = find(VISA_YES, text), find(VISA_NO, text) or RIGHT_TO_WORK.search(text)
    if yes and no and no.start() <= yes.start() <= no.end() + 30:
        yes = None  # "we are not able to SPONSOR VISAS": the "yes" words are inside the "no" sentence
    visa = "unknown" if yes and no else "yes" if yes else "no" if no else "unknown"
    reloc = find(RELOCATION, text)
    evidence = " | ".join(snippet(text, m) for m in (yes, no, reloc) if m)
    return visa, "yes" if reloc else "unknown", evidence


def classify(location: str, workplace: str, description: str, title: str = "") -> dict:
    text = (description or "").lower()
    answer, rule, evidence = from_pakistan(location or "", workplace or "", text, title or "")
    visa, relocation, visa_evidence = visa_and_relocation(text)
    return {"from_pakistan": answer, "visa": visa, "relocation": relocation,
            "regions": ",".join(sorted(places.regions_in(location or ""))), "how": rule,
            "evidence": " | ".join(e for e in (evidence, visa_evidence) if e)[:400]}


SCHEMA = """
CREATE TABLE IF NOT EXISTS eligibility (
    job_id TEXT PRIMARY KEY,
    from_pakistan TEXT,   -- yes / no / maybe
    visa TEXT,            -- yes / no / unknown
    relocation TEXT,      -- yes / unknown
    regions TEXT,         -- "Europe,Middle East"
    how TEXT,             -- the rule that decided (or "llm")
    evidence TEXT,        -- the words it decided on
    checked_on TEXT
)"""


def main() -> None:
    db = store.connect()
    db.execute(SCHEMA)
    start = time.perf_counter()
    jobs = db.execute("SELECT id, location, workplace, description, title FROM jobs WHERE closed_on IS NULL").fetchall()
    today = date.today().isoformat()
    rows = [{"job_id": job_id, **classify(loc, wp, desc, title), "checked_on": today}
            for job_id, loc, wp, desc, title in jobs]
    db.execute("DELETE FROM eligibility")  # rules are cheap: recompute all, so a better rule applies everywhere
    db.executemany("INSERT INTO eligibility VALUES (:job_id, :from_pakistan, :visa, :relocation, :regions, :how, "
                   ":evidence, :checked_on)", rows)
    db.commit()
    print(f"{len(rows)} open jobs classified in {time.perf_counter() - start:.0f}s")
    for field in ("from_pakistan", "visa", "relocation"):
        print(f"{field}:", dict(Counter(row[field] for row in rows).most_common()))
    print("Rules used:")
    for rule, count in Counter(row["how"] for row in rows).most_common():
        print(f"  {count:7}  {rule}")


if __name__ == "__main__":
    main()
