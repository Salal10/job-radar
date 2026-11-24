"""The daily routine in one command: fetch new jobs -> check eligibility -> rank for your profiles.

Run: uv run python -m job_radar.daily               (about 10-15 minutes)
     uv run python -m job_radar.daily --skip-fetch  (only re-rank: after you edit profiles.toml, ~1 minute)
Then open the page: uv run python -m job_radar.web
"""
import sys
import time

from job_radar import eligibility, fetch, match, students


def step(name: str, run) -> None:
    print(f"\n######## {name}")
    start = time.perf_counter()
    run()
    print(f"######## {name}: done in {time.perf_counter() - start:.0f}s")


def main() -> None:
    if "--skip-fetch" not in sys.argv:
        step("1/3 Fetch jobs from all companies", fetch.main)
        step("2/3 Check eligibility (Pakistan / visa / relocation)", eligibility.main)
    step("3/3 Rank jobs for your profiles", match.main)
    if "--skip-fetch" not in sys.argv and students.config()["enabled"]:
        step("+ Working-student jobs in Germany", students.main)
    print("\nDone. Open your matches:  uv run python -m job_radar.web")


if __name__ == "__main__":
    main()
