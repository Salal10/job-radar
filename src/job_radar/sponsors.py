"""Official lists of employers licensed to sponsor work visas, and matching our companies against them.

UK: Home Office register of licensed sponsors (CSV, updated almost daily), Skilled Worker route.
NL: IND public register of recognised sponsors (an HTML table) for regular labour and highly skilled migrants.
"""
import csv
import html
import io
import json
import re
import time
from pathlib import Path

import httpx

CACHE = Path("data/registers")
MAX_AGE_DAYS = 7  # the lists change slowly: download again once a week
UK_PAGE = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
NL_PAGE = "https://ind.nl/en/public-register-recognised-sponsors/public-register-regular-labour-and-highly-skilled-migrants"

# Legal-form words carry no identity: "Monzo Bank Limited" and "Monzo Bank Ltd" are the same company.
LEGAL_WORDS = {"ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "corp", "corporation", "co",
               "company", "bv", "nv", "gmbh", "ag", "sa", "sas", "sarl", "srl", "spa", "ab", "as", "oy",
               "aps", "pty", "the", "uk", "nederland", "netherlands"}
# Words a legal name often adds after the brand: "Monzo" -> "Monzo Bank", "Wise" -> "Wise Payments".
BRAND_EXTRAS = {"bank", "technologies", "technology", "tech", "software", "labs", "ai", "group", "holdings",
                "europe", "international", "payments", "platforms", "systems", "global", "digital"}


def normalize(name: str) -> str:
    """'Monzo Bank Limited' -> 'monzo bank'. 'Adyen N.V.' -> 'adyen'."""
    words = re.findall(r"[a-z0-9]+", name.lower().replace("&", " and ").replace(".", ""))
    return " ".join(w for w in words if w not in LEGAL_WORDS)


def download_uk(client: httpx.Client) -> list[str]:
    page = client.get(UK_PAGE).json()
    url = next(a["url"] for a in page["details"]["attachments"] if a["url"].endswith(".csv"))
    rows = csv.DictReader(io.StringIO(client.get(url).text))
    return [row["Organisation Name"].strip() for row in rows if row["Route"] == "Skilled Worker"]


def download_nl(client: httpx.Client) -> list[str]:
    page = client.get(NL_PAGE, follow_redirects=True).text
    rows = re.findall(r"<tr.*?</tr>", page, re.S)[1:]  # first row is the header
    # the company name is in a <th> cell, the KvK number in a <td>
    cells = [re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S) for row in rows]
    return [html.unescape(re.sub(r"<[^>]+>", "", c[0])).strip() for c in cells if c]


def registers() -> dict[str, list[str]]:
    """{'UK': [names...], 'NL': [names...]}, from the local copy if it is less than a week old."""
    CACHE.mkdir(parents=True, exist_ok=True)
    result = {}
    with httpx.Client(timeout=120, headers={"User-Agent": "Mozilla/5.0 (job-radar)"}) as client:
        for country, download in {"UK": download_uk, "NL": download_nl}.items():
            path = CACHE / f"{country}.json"
            if not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE_DAYS * 86400:
                path.write_text(json.dumps(download(client)), encoding="utf-8")
            result[country] = json.loads(path.read_text(encoding="utf-8"))
    return result


class SponsorMatcher:
    """Looks up a company name in every register. Two kinds of match:
    'exact'  - same name once legal words are removed ("Adyen" = "Adyen N.V.")
    'brand'  - the register name is our name plus brand words ("Monzo" ~ "Monzo Bank Limited"). Riskier."""

    def __init__(self, lists: dict[str, list[str]]):
        self.exact: dict[str, set[str]] = {}
        self.brand: dict[str, set[str]] = {}
        for country, names in lists.items():
            # "Roofoods Ltd t/a Deliveroo": the legal name and the brand are both worth matching on
            parts = [p for name in names for p in re.split(r"\s+(?:t/a|trading as)\s+", name, flags=re.I)]
            for name in parts:
                key = normalize(name)
                self.exact.setdefault(key, set()).add(country)
                words = key.split()
                while len(words) > 1 and words[-1] in BRAND_EXTRAS:  # "monzo bank" also files under "monzo"
                    words = words[:-1]
                    self.brand.setdefault(" ".join(words), set()).add(country)

    def lookup(self, company: str) -> tuple[list[str], str]:
        """'Monzo' -> (['UK'], 'brand'). No match -> ([], '')."""
        key = normalize(company)
        if not key:
            return [], ""
        if key in self.exact:
            return sorted(self.exact[key]), "exact"
        if key in self.brand:
            return sorted(self.brand[key]), "brand"
        return [], ""