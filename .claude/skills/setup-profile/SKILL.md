---
name: setup-profile
description: Set up a new Job Radar user from their CVs - build the experience bank (cvs/experience.md), the master CV in the PDF format (cvs/master.md), and profiles.toml with one profile per role family. Use when the user says /setup-profile, "set up my profile", or "here are my CVs".
---

# Set up a user's profile from their CVs

## 1. Get the CVs
- Look in `cvs/` (create it if missing; it's gitignored). If it's empty, ask the user for their CV files (PDF, DOCX
  or text) and copy them into `cvs/`. One CV is fine; several role-specific CVs are better.
- Read every CV.

## 2. Ask only what the CVs can't tell you (one short question round)
- Remote jobs from home, moving abroad (visa / relocation), or both? (`routes`)
- Any regions to focus on? Any countries to never show (`exclude_countries`)?
- How recent should jobs be (`max_age_days`, default 15)?
- Any red flags the CV prompt should leave alone (`skip_red_flags`, e.g. "overlapping dates between jobs")?

## 3. Write `cvs/experience.md` (the experience bank: the only facts a tailored CV may use)
Merge all CVs. Sections: Contact; Headline options (one per CV); Core facts; Skills (everything claimed anywhere,
grouped); Experience (per employer: title, dates, then **every distinct bullet** from any CV, merged when they say the
same thing, numbers kept exactly); Projects; Education & additional; and an empty "Gaps: never claim these".
Never add anything that isn't in a CV.

## 4. Write `cvs/master.md`
The broadest CV in the renderer's format (copy the structure of a CV in `cvs/` if one exists, or follow the format in
the docstring of `src/job_radar/cv_pdf.py`). Render it to check: `uv run python -m job_radar.cv_pdf cvs/master.md`.

## 5. Write `profiles.toml`
Start from `profiles.example.toml` (keep its comments). Set `skip_red_flags` at the top. Then one `[[profile]]` per
role family the CVs support (don't invent roles), each with:
- `name`: short, lowercase (it becomes a tab name)
- `looking_for`: 2–3 sentences describing the *job* in job-post language (work, stack, industry, level), from the CVs
- `cv`: the matching CV file in `cvs/` (a list if two fit)
- `routes`, `regions`, `sizes = []`, `max_age_days`, `exclude_countries`: from step 2
- `title_any`: 4–8 title words for this role; `title_none`: always intern, working student, trainee, director,
  head of, plus seniority or specialties that don't fit
- `top = 15`

Validate it: `uv run python -c "import tomllib; print([p['name'] for p in tomllib.load(open('profiles.toml','rb'))['profile']])"`

## 6. Rank and hand over
Run `uv run python -m job_radar.daily --skip-fetch` if the database exists (otherwise tell the user to run
`uv run python -m job_radar.daily` first), then tell the user:
- the profiles created and the top 3 jobs in each,
- to read `cvs/experience.md` once and fix anything wrong (tailored CVs only use what's in it),
- how to apply: open `uv run python -m job_radar.web`, then for each job either apply directly or click 📝 Tailor CV.
