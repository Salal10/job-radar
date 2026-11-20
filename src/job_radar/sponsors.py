"""Official lists of employers that sponsor (or recently sponsored) work visas, matched to our companies.

Licences (the company is approved to sponsor; counts for the "move" route):
  UK: Home Office register of licensed sponsors (CSV, updated almost daily), Skilled Worker route.
  NL: IND public register of recognised sponsors (an HTML table) for regular labour and highly skilled migrants.
  DK: SIRI list of companies certified for the Fast-Track scheme (an HTML table).
Hiring history (the company recently hired foreign workers; a bonus badge and a small boost only, never a filter):
  IE: DETE "permits issued to companies" (Excel, this year so far).
  CA: ESDC employers issued a positive LMIA (Excel per quarter; the last 4 quarters).
  US: USCIS H-1B Employer Data Hub (CSV per fiscal year; employers with at least one initial approval).

Run `uv run python -m job_radar.sponsors` to refresh the lists and re-match all companies without a full fetch
(add --refresh to download them again even if the local copies are less than a week old).
"""
import csv
import html
import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

CACHE = Path("data/registers")
MAX_AGE_DAYS = 7  # the lists change slowly: download again once a week
UK_PAGE = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
NL_PAGE = "https://ind.nl/en/public-register-recognised-sponsors/public-register-regular-labour-and-highly-skilled-migrants"
DK_PAGE = "https://www.nyidanmark.dk/en-GB/Words-and-concepts/SIRI/Certified-companies"
IE_PAGE = "https://www.enterprise.gov.ie/en/publications/employment-permit-statistics-{year}.html"
CA_API = "https://open.canada.ca/data/api/action/package_show?id=90fed587-1364-4f33-a9ee-208181dc0b97"
US_PAGE = "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub/h-1b-employer-data-hub-files"
LICENCES = {"UK", "NL", "DK"}  # approved to sponsor
HISTORY = {"IE", "CA", "US"}   # hired foreign workers recently: a bonus signal only
COUNTRY = {"UK": "United Kingdom", "NL": "Netherlands", "DK": "Denmark", "IE": "Ireland", "CA": "Canada",
           "US": "United States"}

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


def html_table_first_column(page: str) -> list[str]:
    rows = re.findall(r"<tr.*?</tr>", page, re.S)[1:]  # first row is the header
    cells = [re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S) for row in rows]
    return [html.unescape(re.sub(r"<[^>]+>", "", c[0])).strip() for c in cells if c]


def excel_rows(content: bytes) -> list[tuple]:
    import openpyxl
    sheet = openpyxl.load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    return list(sheet.iter_rows(values_only=True))


def download_uk(client: httpx.Client) -> list[str]:
    page = client.get(UK_PAGE).json()
    url = next(a["url"] for a in page["details"]["attachments"] if a["url"].endswith(".csv"))
    rows = csv.DictReader(io.StringIO(client.get(url).text))
    return [row["Organisation Name"].strip() for row in rows if row["Route"] == "Skilled Worker"]


def download_nl(client: httpx.Client) -> list[str]:
    return html_table_first_column(client.get(NL_PAGE, follow_redirects=True).text)  # name, then KvK number


def download_dk(client: httpx.Client) -> list[str]:
    return html_table_first_column(client.get(DK_PAGE, follow_redirects=True).text)  # name, then CVR number


def download_ie(client: httpx.Client) -> list[str]:
    """This year's file; early in January it may not exist yet, then last year's."""
    for year in (date.today().year, date.today().year - 1):
        page = client.get(IE_PAGE.format(year=year), follow_redirects=True)
        link = re.search(r'href="([^"]*permits-issued-to-companies[^"]*\.xlsx)"', page.text) if page.is_success else None
        if link:
            rows = excel_rows(client.get(page.url.join(link.group(1))).content)
            return [str(r[0]).strip() for r in rows[1:] if r and r[0] and str(r[0]).strip() != "Grand Total"]
    raise RuntimeError("no 'permits issued to companies' file found")


def download_ca(client: httpx.Client) -> list[str]:
    resources = client.get(CA_API).json()["result"]["resources"]
    urls = [r["url"] for r in resources if r["url"].endswith("_pos_en.xlsx")]
    files = sorted(urls, key=lambda u: u.rsplit("/", 1)[-1])[-4:]  # by file name (tfwp_2026q2_...): the last 4 quarters
    names = set()
    for url in files:
        rows = [tuple(str(c).strip() if isinstance(c, str) else c for c in r) for r in
                excel_rows(client.get(url, follow_redirects=True).content)]  # some headers are "Employer "
        header = next(i for i, r in enumerate(rows) if r and "Employer" in r)
        column = rows[header].index("Employer")
        names |= {str(r[column]).strip() for r in rows[header + 1:] if r and len(r) > column and r[column]}
    return sorted(names)


def download_us(client: httpx.Client) -> list[str]:
    """The USCIS site blocks automated downloads, so this reads a file you download yourself (once a year):
    open US_PAGE in a browser, download the newest h1b_datahubexport-YYYY.csv, save it in data/registers/."""
    files = sorted(CACHE.glob("h1b_datahubexport-*.csv"))
    if not files:
        raise RuntimeError(f"no H-1B file: download the newest h1b_datahubexport-YYYY.csv from {US_PAGE} "
                           f"into {CACHE}/ (optional)")
    with open(files[-1], encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        return sorted({r["Employer"].strip() for r in rows if r["Employer"] and int(r["Initial Approval"] or 0) > 0})


DOWNLOADS = {"UK": download_uk, "NL": download_nl, "DK": download_dk,
             "IE": download_ie, "CA": download_ca, "US": download_us}


def registers() -> dict[str, list[str]]:
    """{'UK': [names...], ...}, from the local copy if it is less than a week old. A list that fails to download
    keeps its old copy (or is skipped): these lists are a bonus, a broken government page must not stop the run."""
    CACHE.mkdir(parents=True, exist_ok=True)
    result = {}
    with httpx.Client(timeout=120, headers={"User-Agent": "Mozilla/5.0 (job-radar)"}) as client:
        for country, download in DOWNLOADS.items():
            path = CACHE / f"{country}.json"
            if not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE_DAYS * 86400:
                try:
                    path.write_text(json.dumps(download(client)), encoding="utf-8")
                except Exception as e:  # any failure: keep going with what we have
                    print(f"  sponsor list {country}: download failed ({type(e).__name__}: {str(e)[:80]})")
            if path.exists():
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
        # Both kinds, per list: an exact match in one list must not hide a brand match in another
        countries = self.exact.get(key, set()) | self.brand.get(key, set())
        if not countries:
            return [], ""
        return sorted(countries), "exact" if key in self.exact else "brand"


def main() -> None:
    """Refresh the lists and re-match every company now (the daily fetch does this too)."""
    from job_radar import store
    if "--refresh" in sys.argv:
        for path in CACHE.glob("*.json"):
            path.unlink()
    lists = registers()
    print("Names per list:", {country: len(names) for country, names in lists.items()})
    matcher = SponsorMatcher(lists)
    db = store.connect()
    rows = db.execute("SELECT board, company FROM companies").fetchall()
    for board, company in rows:
        countries, how = matcher.lookup(company)
        db.execute("UPDATE companies SET sponsor_in = ?, sponsor_match = ? WHERE board = ?",
                   (",".join(countries), how, board))
    db.commit()
    counts = {c: db.execute("SELECT COUNT(*) FROM companies WHERE ',' || sponsor_in || ',' LIKE ?",
                            (f"%,{c},%",)).fetchone()[0] for c in lists}
    print(f"{len(rows)} companies re-matched. Our companies on each list:", counts)


if __name__ == "__main__":
    main()
