I'm setting up a job search tool. I've attached my CV(s). Read them and write a `profiles.toml` file for me.

Make one [[profile]] for each kind of role my CVs show I could apply for (for example: backend .NET, Java, Python,
AI engineer, support, full stack). Don't invent roles my CVs don't support. Use exactly this format for each profile:

[[profile]]
name = "short-name"                 # lowercase, one word or with hyphens: it becomes a tab name
looking_for = """2-3 plain sentences describing the job I'd want for this role: the work, the tech stack,
the industry and the level, based on my CV."""
cv = "cvs/<the CV file name that fits this role>"
routes = ["remote", "move"]
regions = []
sizes = []
max_age_days = 15
exclude_countries = []
title_any = ["words", "that", "a matching job title would contain"]
title_none = ["intern", "director", "and other title words for roles I should not see"]
top = 15

Rules:
- `looking_for` describes the job, not me, in the words job posts use.
- `title_any`: 4-8 words or short phrases from typical titles for this role.
- `title_none`: always include "intern", "working student", "trainee", "director", "head of"; add seniority or
  specialties that don't fit me.
- Only output the file content, nothing else, so I can paste it straight into profiles.toml.
