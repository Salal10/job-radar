"""Second opinion from an LLM, only for jobs the rules couldn't decide ("maybe" / "unknown").

Rules handle ~97% of jobs for free in seconds. The rest are posts that say it in their own words
("we're a remote-first team spread across 30 countries", "relocation to Berlin is part of the package"),
which regex can't cover. The LLM reads only the relevant sentences (not the whole post) to keep it cheap.

Needs GEMINI_API_KEY in .env. Job posts are public data, so the free tier is fine.
"""
import os
import time
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()  # before reading settings: JOB_RADAR_MODEL can be set in .env
MODEL = os.getenv("JOB_RADAR_MODEL", "gemini-3.5-flash-lite")  # cheapest/fastest; 2.5 was retired for new users
REQUESTS_PER_MINUTE = 15  # free tier limit: stay under it instead of hitting 429s

PROMPT = """You read job posts for a job seeker who lives in Pakistan. Answer only from the text given.

from_pakistan: can a person living in Pakistan do this job WITHOUT relocating?
  yes   = office in Pakistan, or remote and Pakistan is allowed (worldwide / any country / lists Pakistan /
          a time zone range that includes UTC+5)
  no    = not remote (onsite or hybrid outside Pakistan), or remote but limited to places that exclude Pakistan
  maybe = remote but the text doesn't say where, or names a region that may or may not include Pakistan
visa: yes = the post offers visa sponsorship; no = it says it won't sponsor, or candidates must already have
  the right to work, or it only considers people already living in that country; unknown = not mentioned or unclear
relocation: yes = the post offers relocation support; unknown = not mentioned
evidence: the decisive words, quoted, max 15 words (or "nothing stated")
Be literal: do not assume what is typical for this company or country."""


class Answer(BaseModel):
    from_pakistan: Literal["yes", "no", "maybe"]
    visa: Literal["yes", "no", "unknown"]
    relocation: Literal["yes", "unknown"]
    evidence: str = Field(max_length=200)


def client():
    from google import genai
    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is missing: add it to .env (see README)")
    return genai.Client()


def ask(gemini, title: str, location: str, workplace: str, excerpts: str) -> tuple[Answer | None, dict]:
    """One job -> (answer or None, cost info). Retries once on rate limits."""
    from google.genai import errors, types
    text = f"Title: {title}\nLocation: {location or '(none)'}\nWorkplace: {workplace or '(not given)'}\n\n{excerpts}"
    for attempt in range(3):
        start = time.perf_counter()
        try:
            response = gemini.models.generate_content(
                model=MODEL, contents=text,
                config=types.GenerateContentConfig(system_instruction=PROMPT, temperature=0,
                                                   response_mime_type="application/json", response_schema=Answer))
        except errors.APIError as e:
            if "quota" in str(e).lower() and "per day" in str(e).lower() or "check your plan" in str(e).lower():
                return None, {"error": "daily quota used up: try again tomorrow", "daily_quota": True}
            if e.code in (429, 503) and attempt < 2:  # per-minute limit / "high demand": both pass if you wait
                time.sleep(30 * (attempt + 1))
                continue
            return None, {"error": str(e)[:200]}
        usage = response.usage_metadata
        if response.parsed is None:  # rare: empty or cut-off JSON
            return None, {"error": "no parsable answer"}
        return response.parsed, {"seconds": round(time.perf_counter() - start, 2),
                                 "tokens_in": usage.prompt_token_count, "tokens_out": usage.candidates_token_count}
    return None, {"error": "rate limited"}
