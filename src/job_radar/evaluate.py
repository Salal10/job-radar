"""Measure the eligibility classifier against hand-checked answers.

  uv run python -m job_radar.evaluate sample   pick ~150 jobs -> eval/sample.jsonl (no classifier answers in it)
  uv run python -m job_radar.evaluate review   local page to check/fix labels -> eval/labels.csv
  uv run python -m job_radar.evaluate score    rules vs your labels: precision and recall per answer

Labels are the truth only after review: "reviewed" = 1 means a person checked that row.
"""
import csv
import html
import json
import random
import re
import sys
import time
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from job_radar import store
from job_radar.eligibility import classify

EVAL = Path("eval")
# --holdout: a second, separate set. The rules were tuned on the first one, so only the holdout gives an honest number.
HOLDOUT = "--holdout" in sys.argv
PREFIX = "holdout_" if HOLDOUT else ""
SAMPLE, LABELS = EVAL / f"{PREFIX}sample.jsonl", EVAL / f"{PREFIX}labels.csv"
FIELDS = ["job_id", "from_pakistan", "visa", "relocation", "reviewed", "note"]
ANSWERS = {"from_pakistan": ["yes", "no", "maybe"], "visa": ["yes", "no", "unknown"], "relocation": ["yes", "unknown"]}
# Several of each kind of answer, so rare answers (visa yes) get measured too, not just "no".
STRATA = {"from_pakistan = 'yes'": 35, "from_pakistan = 'maybe'": 25, "how = 'remote, limited to other countries'": 15,
          "how = 'not remote'": 10, "visa = 'yes'": 25, "visa = 'no'": 15, "relocation = 'yes'": 15}
KEYWORDS = re.compile(r"(?i)remote|anywhere|worldwide|based in|located in|resid|time ?zone|utc|gmt|countr|"
                      r"visa|sponsor|relocat|right to work|work authori|eligib|pakistan|asia|emea|europe")


def excerpts(description: str, limit: int = 1800) -> str:
    """The sentences a person would look at: around the words about place, visa and relocation."""
    spans = []
    for m in KEYWORDS.finditer(description):
        start, end = max(0, m.start() - 160), m.end() + 160
        if spans and start <= spans[-1][1]:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    text = " … ".join(description[a:b].strip() for a, b in spans)
    return text[:limit] or description[:400]


def sample() -> None:
    db = store.connect()
    random.seed(7 if HOLDOUT else 42)
    used = set()
    if HOLDOUT:  # never reuse a job from the tuning set
        used = {json.loads(line)["job_id"] for line in open(EVAL / "sample.jsonl", encoding="utf-8")}
    picked = {}
    for where, count in STRATA.items():
        ids = [row[0] for row in db.execute(f"SELECT job_id FROM eligibility WHERE {where} ORDER BY job_id")
               if row[0] not in used]
        for job_id in random.sample(ids, min(count // 2 if HOLDOUT else count, len(ids))):
            picked.setdefault(job_id, where)
    EVAL.mkdir(exist_ok=True)
    with open(SAMPLE, "w", encoding="utf-8") as f:
        for job_id in sorted(picked):
            title, company, location, workplace, url, description = db.execute(
                "SELECT title, company, location, workplace, url, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
            f.write(json.dumps({"job_id": job_id, "title": title, "company": company, "location": location,
                                "workplace": workplace, "url": url, "excerpts": excerpts(description or "")},
                               ensure_ascii=False) + "\n")
    print(f"{len(picked)} jobs -> {SAMPLE}: {dict(Counter(picked.values()))}")


def load_labels() -> dict[str, dict]:
    if not LABELS.exists():
        return {}
    with open(LABELS, encoding="utf-8") as f:
        return {row["job_id"]: row for row in csv.DictReader(f)}


def save_labels(labels: dict[str, dict]) -> None:
    with open(LABELS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(labels[key] for key in sorted(labels))


def review_page(jobs: list[dict], labels: dict[str, dict]) -> str:
    done = sum(1 for job in jobs if labels.get(job["job_id"], {}).get("reviewed") == "1")
    cards = []
    for i, job in enumerate(jobs):
        label = labels.get(job["job_id"], {})
        choices = "".join(
            f"<fieldset><legend>{field.replace('_', ' ')}</legend>" + "".join(
                f"<label><input type=radio name='{field}' value='{a}' {'checked' if label.get(field) == a else ''}> {a}</label>"
                for a in answers) + "</fieldset>"
            for field, answers in ANSWERS.items())
        status = "✓ reviewed" if label.get("reviewed") == "1" else "draft" if label else "no label"
        cards.append(f"""<form method=post class='card {'ok' if label.get('reviewed') == '1' else ''}' id=j{i}>
<input type=hidden name=job_id value="{html.escape(job['job_id'])}"><input type=hidden name=next value="j{i + 1}">
<div class=top><b>{i + 1}. {html.escape(job['title'])}</b> · {html.escape(job['company'])} <span class=st>{status}</span></div>
<div>📍 {html.escape(job['location'] or '(no location)')} · {html.escape(job['workplace'] or 'workplace not given')}
 · <a href="{html.escape(job['url'])}" target=_blank rel=noopener>open the post ↗</a></div>
<p class=ex>{html.escape(job['excerpts'])}</p>{choices}
<input name=note placeholder="note (optional)" value="{html.escape(label.get('note', ''))}">
<button>Save as reviewed</button></form>""")
    return f"""<!doctype html><meta charset=utf-8><title>Label review</title><style>
body{{font:15px system-ui;max-width:900px;margin:auto;padding:16px;background:#f6f6f4;color:#222}}
.card{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:14px;margin:14px 0}}.ok{{border-color:#2a7}}
.ex{{background:#fafafa;font-size:13px;line-height:1.5;max-height:220px;overflow:auto;padding:8px}}
fieldset{{display:inline-block;border:1px solid #ddd;margin:4px 8px 4px 0}}input[name=note]{{width:60%}}
.st{{float:right;color:#777}}button{{margin-left:8px}}</style>
<h1>Label review: {done} of {len(jobs)} reviewed</h1>
<p>For each job: could someone <b>living in Pakistan</b> do it without moving (yes / no / maybe = the post doesn't say)?
Does the post <b>offer</b> visa sponsorship (no = it says it won't)? Relocation support? The answer is pre-filled
with a draft; fix it if you disagree, then save.</p>{''.join(cards)}"""


def review() -> None:
    jobs = [json.loads(line) for line in open(SAMPLE, encoding="utf-8")]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply(review_page(jobs, load_labels()))

        def do_POST(self):
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
            labels = load_labels()
            labels[form["job_id"]] = {field: form.get(field, "") for field in FIELDS} | {"reviewed": "1"}
            save_labels(labels)
            self.send_response(303)
            self.send_header("Location", f"/#{form.get('next', '')}")
            self.end_headers()

        def reply(self, page: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *args):
            pass

    print("Review page: http://localhost:8765  (Ctrl+C to stop)")
    webbrowser.open("http://localhost:8765")
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()


LLM_ANSWERS = EVAL / f"{PREFIX}llm_answers.jsonl"


def llm() -> None:
    """Ask the LLM about EVERY sampled job (cached), so we can compare rules, LLM, and rules + LLM."""
    from job_radar import llm_check
    cached = {}
    if LLM_ANSWERS.exists():
        cached = {row["job_id"]: row for row in map(json.loads, open(LLM_ANSWERS, encoding="utf-8")) if "answer" in row}
    jobs = [json.loads(line) for line in open(SAMPLE, encoding="utf-8")]
    gemini = llm_check.client()
    failures = 0
    with open(LLM_ANSWERS, "a", encoding="utf-8") as f:
        for i, job in enumerate(jobs):
            if job["job_id"] in cached:
                continue
            answer, cost = llm_check.ask(gemini, job["title"], job["location"], job["workplace"], job["excerpts"])
            row = {"job_id": job["job_id"], "model": llm_check.MODEL, **cost}
            failures = 0 if answer else failures + 1
            if failures == 3:  # a wrong model name or key fails every call: stop instead of spending the quota
                sys.exit(f"Stopped at job {i + 1}: {cost.get('error')}")
            if answer:
                row["answer"] = answer.model_dump()
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()  # saved after every job: a crash or Ctrl+C loses nothing
            print(f"{i + 1}/{len(jobs)} {job['job_id'][:50]} {row.get('answer', row.get('error'))}")
            time.sleep(60 / llm_check.REQUESTS_PER_MINUTE)


def report(name: str, predicted: dict[str, dict], labels: dict[str, dict]) -> None:
    print(f"===== {name}")
    for field, answers in ANSWERS.items():
        right = sum(predicted[j][field] == labels[j][field] for j in labels)
        print(f"{field}: {right}/{len(labels)} correct ({right / len(labels):.0%})")
        for answer in answers:
            said = [j for j in labels if predicted[j][field] == answer]
            truly = [j for j in labels if labels[j][field] == answer]
            both = [j for j in said if labels[j][field] == answer]
            precision = f"{len(both) / len(said):.0%}" if said else "-"
            recall = f"{len(both) / len(truly):.0%}" if truly else "-"
            print(f"  {answer:8} said {len(said):3}, truly {len(truly):3}  precision {precision:>4}  recall {recall:>4}")
        if "-v" in sys.argv:
            for j in labels:
                if predicted[j][field] != labels[j][field]:
                    print(f"    wrong: {j}  said {predicted[j][field]}, truly {labels[j][field]}  ({predicted[j]['how']})")
    print()


def score() -> None:
    labels = {k: v for k, v in load_labels().items() if v["reviewed"] == "1" or "--drafts" in sys.argv}
    if not labels:
        sys.exit("No reviewed labels yet: run 'evaluate review' first (or score --drafts)")
    db = store.connect()
    rules = {}
    for job_id in labels:
        location, workplace, description, title = db.execute(
            "SELECT location, workplace, description, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
        rules[job_id] = classify(location, workplace, description, title)
    print(f"{len(labels)} {'draft' if '--drafts' in sys.argv else 'reviewed'} labels\n")
    report("Rules only", rules, labels)

    answers = {}
    if LLM_ANSWERS.exists():
        answers = {r["job_id"]: r["answer"] for r in map(json.loads, open(LLM_ANSWERS, encoding="utf-8")) if "answer" in r}
    if not all(j in answers for j in labels):
        print("(LLM answers missing for some jobs: run 'evaluate llm' to compare)")
        return
    report("LLM only", {j: answers[j] | {"how": "llm"} for j in labels}, labels)
    hybrid = {}
    for j in labels:  # the rules' clear answers stand; only jobs they call "maybe" go to the LLM
        hybrid[j] = dict(rules[j])
        if rules[j]["from_pakistan"] == "maybe":  # one call answers all three; use it where rules had nothing
            for field, unclear in (("from_pakistan", "maybe"), ("visa", "unknown"), ("relocation", "unknown")):
                if rules[j][field] == unclear:
                    hybrid[j][field] = answers[j][field]
    report("Rules first, LLM only for 'maybe' jobs", hybrid, labels)
    sent = sum(rules[j]["from_pakistan"] == "maybe" for j in labels)
    print(f"The hybrid sends {sent} of {len(labels)} sampled jobs to the LLM (the sample over-represents "
          f"'maybe' jobs; on all open jobs it's about 2.5%).")


if __name__ == "__main__":
    command = next((a for a in sys.argv[1:] if not a.startswith("-")), "score")
    {"sample": sample, "review": review, "score": score, "llm": llm}[command]()
