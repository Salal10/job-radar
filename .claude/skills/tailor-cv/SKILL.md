---
name: tailor-cv
description: Tailor the user's CV to one Job Radar job (job id or job URL) with the 3-step recruiter / XYZ / ATS process from prompts/tailor-cv.md, and produce a one-page PDF plus a report. Use when the user says /tailor-cv, "tailor my CV", or "make a CV for this job".
---

# Tailor a CV for one job

Input: a Job Radar job id (shown on the web page) or a job URL. If none was given, ask for one.
If `cvs/experience.md` doesn't exist yet, tell the user to run `/setup-profile` first and stop.

## 0. Ground rules (apply in every step)

- **Truth only.** The only facts allowed are in `cvs/experience.md` (the experience bank). Reword, reorder, merge,
  shorten and choose freely, but never add a skill, tool, employer, title, date, number, team size or responsibility
  that isn't in the bank. Keep every metric exactly as written there.
- A missing keyword the bank supports goes into the CV. One it doesn't support is a **gap**: put it in the report,
  never in the CV.
- Don't change employers, job titles or dates. Don't report or "fix" anything listed in `skip_red_flags` at the top of
  `profiles.toml`.
- One page. Standard ATS section names (Professional Summary, Technical Skills, Professional Experience, Projects,
  Education & Certifications).
- The CV and the experience bank are personal data: don't send them to any other service or model.

## 1. Gather

1. `PYTHONIOENCODING=utf-8 uv run python -m job_radar.job "<id or URL>"` prints the job, its profile, the **BASE CV**
   and the full post. If the job isn't in the database, fetch the URL with WebFetch or ask the user to paste it.
2. Read the base CV (if the profile lists several, use the one closest to this job; if none, the closest in `cvs/`).
3. Read `cvs/experience.md`, `prompts/tailor-cv.md` (the 3 steps, word for word), and `cvs/master.md` if it exists
   (the file format the PDF renderer expects; otherwise use the format shown in `src/job_radar/cv_pdf.py`).

## 2. Run the 3 steps from prompts/tailor-cv.md

- **Step 1, recruiter:** match score out of 100 (say it's a judgment, not a measurement), the top 5 missing keywords
  (each **in bank** or **gap**), the 3 red flags a hiring manager spots in under 10 seconds.
- **Step 2, rewrite:** experience bullets in the XYZ formula (accomplished X, measured by Y, by doing Z), including the
  in-bank keywords and removing the red flags. Pick stronger bullets from the bank if they fit better. Most relevant
  bullets first. Adjust the headline, summary and skills order to the job.
- **Step 3, ATS + hiring manager reading 200 CVs:** which sections get skipped and why, then rewrite them (numbers
  first, job words early, no filler). Re-check every line against the ground rules.

## 3. Write the files

Folder `applications/<YYYY-MM-DD>-<company>-<short-role>/` (lowercase, hyphens, today's date).
`<Name>` = the person's name from the bank, with hyphens (e.g. `Jane-Doe`).

1. `<Name>-CV-<Company>.md` in the renderer's format (`#` name, `>` headline, contact lines, `##` sections,
   `### Company | Location`, `*Title* | Dates`, `- ` bullets, `**Label:**` skill lines).
2. `uv run python -m job_radar.cv_pdf "applications/<folder>/<Name>-CV-<Company>.md"`. If it says 2 pages, cut the
   least relevant bullets or skills and render again.
3. Read the PDF back and check it (nothing cut off, sections in order).
4. `report.md`: the job (title, company, URL, id), the base CV, step 1 (score, keywords, red flags), step 3 (skipped
   sections and changes), the score after (judgment) with a short before → after list, and **Gaps** (each with a
   one-line suggestion: learn it, a small project, or how to talk about adjacent experience).
5. `uv run python -m job_radar.job "<job id>" --cv "applications/<folder>/<Name>-CV-<Company>.pdf"` (saves it in the tracker).

## 4. Reply

Short: score before → after, the 5 keywords (added vs gaps), the 3 red flags and how they were handled, the PDF path.
Offer tweaks. On a tweak: edit the .md, re-render, update report.md.
