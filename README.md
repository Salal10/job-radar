# Job Radar

Finds jobs straight from **6,400+ company career pages** (jobs that often never reach LinkedIn), keeps only the ones
you can actually take (**remote from Pakistan**, or with **visa sponsorship / relocation**), ranks them for you, and
helps you apply, with an optional CV tailored to each job.

```
6,400 company boards ─► ~400k jobs ─► can you take it? ─► ranked for your profile ─► web page: apply / tailor CV / track
```

Everything runs on your computer. Your CVs and profile never leave it. Only public job text is sent to an AI.

---

## Setup (once, ~20 minutes)

**1. Install `uv`** (it installs Python for you), then close and reopen PowerShell:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**2. Download the project:**
```powershell
git clone https://github.com/Salal10/job-radar.git
```
```powershell
cd job-radar
```
```powershell
uv sync
```

**3. Add your CVs.** Create a folder named `cvs` inside `job-radar` and copy your CV PDFs into it. One CV is fine.
If you keep different CVs for different roles, add them all. (This folder is never uploaded.)

**4. Create your profiles.** A *profile* is one kind of job you'd apply for ("backend .NET", "support", "AI engineer").
Each profile becomes a tab of matching jobs. Pick one way:

| Way | How |
|---|---|
| By hand | `copy profiles.example.toml profiles.toml`, then edit it in Notepad (see [Profile settings](#profile-settings)) |
| Any AI chat | Paste [`prompts/make-profiles.md`](prompts/make-profiles.md) into ChatGPT or Claude, attach your CVs, and save the answer as `profiles.toml` |
| Claude Code | Type `/setup-profile` in Claude Code, opened in this folder. It reads your CVs, asks 4 questions and writes everything |

**5. (Optional) Free AI double-check.** Get a key at <https://aistudio.google.com/apikey> and create a file named `.env`
in `job-radar` containing `GEMINI_API_KEY=your-key`. Without it, everything else still works.

---

## Every day (2 commands)

```powershell
uv run python -m job_radar.daily
```
This fetches new jobs, checks each one and ranks them for your profiles. It takes ~10–15 min (the first time ~30 min).

```powershell
uv run python -m job_radar.web
```
This opens your matches at <http://localhost:8766>. Keep the window open while you use it; Ctrl+C stops it.

Changed `profiles.toml`? Re-rank in under a minute without fetching again:
```powershell
uv run python -m job_radar.daily --skip-fetch
```

---

## Applying

Each tab shows your best matches. Each job card has:

- **Badges:**
  - ✅ doable from Pakistan / ❔ remote, place not stated
  - 🛂 visa sponsorship / ⛔ no sponsorship
  - 🧳 relocation support
  - 🏛 the company is on the UK/NL official visa-sponsor list *for this job's country*
- **Buttons:**
  - ☆ **Save** for later
  - ✉ **Applied:** moves the job to the Tracker
  - ✕ **Not for me:** hides it. The next run fills its place
  - 📝 **Tailor CV**

**📝 Tailor CV** gives three ways to apply:

1. **Apply directly:** open the post and apply with any CV.
2. **Any AI chat:** click **Copy prompt** (the job is already filled in), paste it into ChatGPT or Claude, and attach
   your CV. It does three steps:
   - a recruiter's score out of 100, with the 5 missing keywords and 3 red flags;
   - a rewrite of your experience using the XYZ formula ("accomplished X, measured by Y, by doing Z");
   - an ATS / busy-hiring-manager check.

   It may only use facts from your CV. Always read the result before sending it.
3. **Claude Code:** run the command shown (`/tailor-cv <job>`). It produces a one-page PDF and a report in
   `applications/`, using your *experience bank* (`cvs/experience.md`: every true fact from all your CVs).

The **Tracker** tab keeps every job you saved or applied to, with a status (applied → interview → offer / rejected)
and notes.

---

## Profile settings

```toml
skip_red_flags = []                 # top of file: things the CV prompt should leave alone

[[profile]]
name = "backend"                    # tab name
looking_for = """Backend engineer building APIs in C# / .NET, fintech, Azure, SQL. Mid level."""
cv = "cvs/My-CV-Backend.pdf"        # the CV you'd send for this kind of job
routes = ["remote", "move"]
regions = []
sizes = []
max_age_days = 15
exclude_countries = []
title_any = ["engineer", "developer"]
title_none = ["intern", "director", "sales"]
top = 15
```

| Setting | Meaning |
|---|---|
| `looking_for` | **The most important one.** 2–3 plain sentences about the job you want: the work, the tech, the industry, the level. Jobs are ranked by how close they are in *meaning* |
| `cv` | Your CV for this kind of job (a list if several fit) |
| `routes` | `"remote"` = jobs you can do from Pakistan · `"move"` = jobs offering a visa or relocation, or at a licensed sponsor |
| `regions` | `[]` = anywhere, or e.g. `["Europe", "Middle East", "North America", "Asia"]` |
| `sizes` | `[]` = any, or `["startup", "scale-up"]` (by number of open roles: ≤20 / 21–100 / >100) |
| `max_age_days` | Only jobs posted in the last N days |
| `exclude_countries` | Never show jobs located there, e.g. `["Pakistan"]` |
| `title_any` | The job title must contain one of these words |
| `title_none` | Skip titles containing any of these |
| `top` | How many jobs to show in this tab |

Seeing the wrong kind of job? Reword `looking_for`, or add a word from those titles to `title_none`.

---

## How accurate are the badges?

Measured on 65 hand-checked jobs the rules were never tuned on: **"doable from Pakistan" is right 89% of the time and
visa 100%**, after the AI double-check (83% / 95% without it). That's a small sample, so treat the badges as a strong
filter, not a guarantee, and read the post.

---

## Good to know

- **More companies:** `uv run python -m job_radar.careers https://company.com/careers` adds one company.
  `uv run python -m job_radar.discover` (slow, weekly at most) finds many more.
- **"Daily quota used up":** the free AI tier has a daily limit. Answers are saved, and the next day continues.
- **Starting over:** delete the `data` folder. Note that this also deletes your tracker.
- **Privacy:** `cvs/`, `applications/`, `profiles.toml`, `.env` and `data/` are in `.gitignore`.
