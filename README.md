# Job Radar

Find jobs that never show up on LinkedIn or job boards, straight from **6,400+ company career pages**, and see
only the ones **you can actually take**: remote from Pakistan, or with visa sponsorship / relocation. Ranked for
**your** profile, on a local web page with a link to every original post and a tracker for your applications.

```
companies.csv ──► fetch ──► eligibility ──► match ──► web page
(6,400 boards)   (~400k jobs)  (Pakistan? visa?    (ranked for     (links + tracker)
                               relocation?)        your profile)
```

**What it does, in short**

- **Fetches** every open job from company boards on 7 hiring platforms (Greenhouse, Lever, Ashby, Workable,
  SmartRecruiters, Recruitee, Personio) and from career sites that publish job data for Google.
- **Decides for every job:** can you do it **from Pakistan** without moving? Does it offer a **visa** or
  **relocation**? Is the company on the **UK or Netherlands visa sponsor register**? Each answer stores the words it
  was based on, so you can check it.
- **Ranks** the possible jobs by how well they match what you're looking for (by meaning, not just keywords:
  "C# backend" matches ".NET API developer").
- **Double-checks your top matches** with an AI model (Gemini, free tier): it reads the full post for visa and
  relocation wording, in any language.
- **Shows** everything on a page on your own computer, with a tracker: saved → applied → interview → offer.

Your profile and your tracker stay on your computer. Only public job text is ever sent to Gemini.

---

## Contents

1. [One-time setup (15 minutes)](#1-one-time-setup-15-minutes)
2. [Your first run](#2-your-first-run)
3. [Every day: 2 commands](#3-every-day-2-commands)
4. [Using the web page](#4-using-the-web-page)
5. [Where to configure what](#5-where-to-configure-what)
6. [Every week or so (optional)](#6-every-week-or-so-optional)
7. [What the answers mean](#7-what-the-answers-mean)
8. [How accurate is it?](#8-how-accurate-is-it)
9. [Troubleshooting](#9-troubleshooting)
10. [All commands](#10-all-commands)
11. [Project layout](#11-project-layout)

---

## 1. One-time setup (15 minutes)

The commands below are for **Windows PowerShell**. On macOS/Linux they are the same, except installing `uv`.

**You need:** Git, an internet connection, and about **2 GB of free disk space** (the job database is ~1.4 GB).
No GPU needed.

### Step 1: Install `uv` (it installs Python and everything else for you)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen PowerShell, then check it works:

```powershell
uv --version
```

(macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Step 2: Download the project

```powershell
git clone https://github.com/Salal10/job-radar.git
```
```powershell
cd job-radar
```
```powershell
uv sync
```

`uv sync` installs Python 3.13 and all packages into the project folder. Nothing is installed system-wide.

### Step 3: Create your search profile

```powershell
copy profiles.example.toml profiles.toml
```

Open `profiles.toml` in any text editor (Notepad is fine) and change `looking_for` to describe the job you want,
in plain sentences. The example has two profiles (`backend` and `ai-engineer`); keep, edit or delete them. See
[section 5](#5-where-to-configure-what) for every option.

### Step 4 (optional, recommended): Add a free Gemini key

This turns on the AI double-check of your top matches. Without it, everything else still works.

1. Go to <https://aistudio.google.com/apikey>, sign in with a Google account, and click **Create API key**.
2. In the `job-radar` folder, create a file named `.env` (no other name) containing one line:

   ```
   GEMINI_API_KEY=paste-your-key-here
   ```

`.env` and `profiles.toml` are in `.gitignore`: they are never uploaded if you push your copy to GitHub.

---

## 2. Your first run

```powershell
uv run python -m job_radar.daily
```

This runs the whole routine: fetch → eligibility → match. **The first run takes about 25–30 minutes:**

| Step | First run | Later runs |
|---|---|---|
| Fetch ~400k jobs from 6,400 boards | ~10 min | ~8–10 min |
| Check eligibility of every job | ~1–3 min | ~1–3 min |
| Download the matching model (~65 MB, once) and process your candidate jobs | ~15 min | under 1 min |
| AI check of your top matches (needs the Gemini key) | ~5 min | only new top matches |

You'll see a progress summary for every step. When it says **Done**, open the page:

```powershell
uv run python -m job_radar.web
```

Your browser opens <http://localhost:8766>. Leave the PowerShell window open while you use the page;
press **Ctrl+C** in it to stop.

---

## 3. Every day: 2 commands

Open PowerShell in the `job-radar` folder (`cd` to wherever you cloned it), then:

**① Get today's jobs and rankings** (~10–15 minutes; you can do something else meanwhile)

```powershell
uv run python -m job_radar.daily
```

**② Look at your matches**

```powershell
uv run python -m job_radar.web
```

On the page, go through each profile tab:

- Click a job title to read the original post (opens on the company's site).
- **☆ Save** the ones you like, **✉ Applied** once you've applied, **✕ Not for me** to hide one.
  Applied and hidden jobs leave the list, and the next run fills their places with new matches.
- Open the **Tracker** tab to update statuses (interview, offer, rejected) and add notes.

That's the whole routine. Jobs marked **NEW** appeared since your previous fetch.

> **Changed your profile?** You don't need to fetch again. Re-rank in about a minute:
> ```powershell
> uv run python -m job_radar.daily --skip-fetch
> ```

---

## 4. Using the web page

| Part of the page | What it shows |
|---|---|
| **Tabs** | One tab per profile in `profiles.toml`, plus **Tracker** |
| **Job title** | Link to the original post on the company's site |
| Grey line | Company · size (startup / scale-up / large) · location · posting date |
| ✅ doable from Pakistan | Remote and open to Pakistan, or an office in Pakistan |
| ❔ remote, place not stated | Remote, but the post doesn't say where from |
| 🛂 visa sponsorship / ⛔ no visa sponsorship | What the post says about sponsoring a work visa |
| 🧳 relocation support | The post offers help to move |
| 🏛 on the UK / NL visa sponsor list | The company is licensed to sponsor visas **in the country of this job** (official government registers) |
| *"Quoted text"* | The words the AI check based its answer on: read them before you trust the answer |
| ☆ Save / ✉ Applied / ✕ Not for me | Your decision; stored in the tracker |
| **Tracker** tab | Your jobs grouped by status, with a note field. Kept even after the company removes the post |

The page only runs on your computer (`127.0.0.1`): nobody else on your network can open it.

---

## 5. Where to configure what

| I want to change… | Where | How |
|---|---|---|
| **What kind of job I'm looking for** | `profiles.toml` → `looking_for` | Plain sentences: role, tech stack, domain, level |
| Remote only, or relocation only | `profiles.toml` → `routes` | `["remote"]`, `["move"]`, or both |
| Countries / regions | `profiles.toml` → `regions` | e.g. `["Europe", "Middle East"]`; `[]` = anywhere |
| Only startups / scale-ups | `profiles.toml` → `sizes` | e.g. `["startup", "scale-up"]`; `[]` = any |
| Words the title must contain | `profiles.toml` → `title_any` | e.g. `["engineer", "developer"]`; `[]` = no check |
| Words that rule a job out | `profiles.toml` → `title_none` | e.g. `["intern", "director", "sales"]` |
| How many matches to show | `profiles.toml` → `top` | e.g. `25` |
| A second, different search | `profiles.toml` | Copy a whole `[[profile]]` block and give it a new `name` |
| The AI key | `.env` | `GEMINI_API_KEY=...` |
| The AI model | `.env` | `JOB_RADAR_MODEL=gemini-3.5-flash-lite` (default) |
| Which companies are fetched | `companies.csv` | One row per board: `ats,slug,name`. Easiest via the commands in section 6 |

**Profile example** (`profiles.toml`):

```toml
[[profile]]
name = "backend"                    # the tab name on the page
looking_for = """Backend software engineer building APIs and services in C# / .NET or Python,
payments or fintech, cloud (Azure or AWS), SQL databases."""
routes = ["remote", "move"]         # remote = from Pakistan; move = visa / relocation / sponsor list
regions = []                        # [] = anywhere; or ["Europe", "Middle East", "North America", "Asia", ...]
sizes = []                          # [] = any; or ["startup", "scale-up", "large"]
title_any = ["engineer", "developer", "programmer"]
title_none = ["intern", "principal", "director", "sales", "manager", "frontend"]
top = 25
```

**Regions you can use:** `Europe`, `Middle East`, `Africa`, `North America`, `Latin America`, `Asia`, `Oceania`.
A job that's doable from Pakistan always passes the region filter (where the company is doesn't matter then).

**Tips for `looking_for`:** describe the job, not yourself. Mention the stack and the domain. Run
`daily --skip-fetch` after each change, and look at whether the top 10 got better. If unwanted kinds of jobs keep
showing up, add a word from their titles to `title_none`.

**Company size** is estimated from the number of open roles (it's a signal, not a headcount):
startup = up to 20 open roles, scale-up = 21–100, large = more than 100.

---

## 6. Every week or so (optional)

**Find more companies** automatically, from Common Crawl (a free public copy of the web). Takes a while:

```powershell
uv run python -m job_radar.discover
```

**Add a specific company** by its careers page URL. It detects which hiring platform the page uses:

```powershell
uv run python -m job_radar.careers https://example.com/careers
```

Add `--dry-run` to see what it found without adding it. If it says the platform isn't supported (e.g. Workday),
that company can't be added yet.

New companies are fetched on your next `daily` run.

---

## 7. What the answers mean

| Answer | Meaning |
|---|---|
| from Pakistan: **yes** | Remote and Pakistan is allowed (worldwide, lists Pakistan, or a UTC+5 time zone), or the office is in Pakistan |
| from Pakistan: **maybe** | Remote, but the post doesn't say from where. Worth reading |
| from Pakistan: **no** | Onsite/hybrid elsewhere, or remote but limited to other countries |
| visa: **yes / no / unknown** | The post offers sponsorship / says it won't (or you must already have the right to work) / doesn't say |
| relocation: **yes** | The post offers relocation help |
| sponsor list | The company is on the official UK or NL register of licensed visa sponsors, **and the job is in that country**. Licensed doesn't guarantee they'll sponsor this role, but it makes it possible |

**How the answers are made:**

1. **Rules** check every job (free, about a minute for 400k jobs): the location, the workplace type, the title,
   and phrases in the description ("must be based in…", "unable to sponsor…").
2. For your **top matches only**, the full post is fetched and **Gemini reads it**. Its answers are combined
   with the rules in the way that measured best (section 8). Jobs that turn out to be impossible are dropped.

Answers are a strong filter, not a guarantee. **Always read the post before applying.**

---

## 8. How accurate is it?

Measured on hand-checked jobs. The **holdout** set is 67 jobs that were never used to tune the rules, so it's the
honest number. It's a small sample: read it as a direction, not an exact figure.

| Holdout set | Rules only | AI only | Rules + AI (what the app uses) |
|---|---|---|---|
| "Doable from Pakistan" correct | 83% | 75% | **89%** |
| Visa answer correct | 95% | 100% | **100%** |
| Relocation answer correct | 100% | 100% | **100%** |

Rules alone are good at clear cases and say "maybe" when a post is vague. The AI reads vague text well but alone
was *less* accurate, because it ignores the structured location fields. Combined, they beat both, while the AI only
reads about 70 posts a day instead of 400,000.

To re-run the measurement yourself:

```powershell
uv run python -m job_radar.evaluate score --holdout
```

---

## 9. Troubleshooting

| Problem | What to do |
|---|---|
| `uv` is not recognized | Close and reopen PowerShell after installing it |
| "No profiles.toml" | Do [setup step 3](#step-3-create-your-search-profile) |
| "Skipping the LLM check: GEMINI_API_KEY is missing" | Optional: add the key ([setup step 4](#step-4-optional-recommended-add-a-free-gemini-key)). Everything else works without it |
| "Gemini's free daily quota is used up" | Normal on the free tier. Answers are saved, so the next day's run continues where it stopped |
| The first `match` is slow | It's processing thousands of jobs once; later runs only process new jobs |
| A warning about "symlinks" on Windows | Harmless; ignore it |
| A few companies "failed" during fetch | Normal (a site was down or slow). Their jobs aren't touched; they're tried again next run |
| "Address already in use" when opening the page | The page is already running in another window. Use that, or close it with Ctrl+C |
| The page shows old matches | Run `uv run python -m job_radar.daily` (or `--skip-fetch`) first, then refresh the page |
| Disk is filling up | Jobs closed for 30+ days are deleted automatically. Deleting the `data` folder resets everything (your tracker too) |

---

## 10. All commands

Run them from the `job-radar` folder.

| Command | What it does | When |
|---|---|---|
| `uv run python -m job_radar.daily` | Fetch + eligibility + match | Every day |
| `uv run python -m job_radar.daily --skip-fetch` | Re-rank only | After editing `profiles.toml` |
| `uv run python -m job_radar.web` | Open the web page | Every day |
| `uv run python -m job_radar.fetch` | Only fetch jobs | Rarely on its own |
| `uv run python -m job_radar.eligibility` | Only re-check eligibility | Rarely on its own |
| `uv run python -m job_radar.match` | Only rank (and AI-check top matches) | Rarely on its own |
| `uv run python -m job_radar.discover` | Find more companies | Weekly, optional |
| `uv run python -m job_radar.careers <url>` | Add a company by careers page URL | When you find one |
| `uv run python -m job_radar.evaluate score` | Accuracy against labelled jobs (`--holdout` for the honest set) | When changing the rules |

---

## 11. Project layout

```
job-radar/
├── companies.csv            the company boards to fetch (ats, slug, name)
├── profiles.example.toml    template for your search profiles
├── profiles.toml            YOUR profiles (not in git)
├── .env                     YOUR Gemini key (not in git)
├── data/                    database, model and sponsor registers (not in git, created on first run)
├── eval/                    hand-checked jobs used to measure accuracy
└── src/job_radar/
    ├── daily.py             the daily routine in one command
    ├── fetch.py             fetch all boards, close/delete old jobs, run summary
    ├── sources.py           one fetcher per hiring platform
    ├── sponsors.py          UK and NL visa sponsor registers
    ├── discover.py          find new company boards (Common Crawl)
    ├── careers.py           add a company from its careers page URL
    ├── places.py            location text → countries and regions
    ├── eligibility.py       rules: from Pakistan / visa / relocation
    ├── llm_check.py         the Gemini question and answer format
    ├── check.py             second look at top matches: full post + AI + combining answers
    ├── match.py             filter and rank jobs for each profile
    ├── web.py               the local web page and tracker
    ├── evaluate.py          accuracy measurement
    └── store.py             SQLite storage
```

**Privacy:** the job database, your profile, your tracker and your key stay in this folder. Ranking runs on your
computer. Gemini receives only short excerpts of public job posts, never your profile.
