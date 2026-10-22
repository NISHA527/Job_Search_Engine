# Job Search Engine

Opens Brave, logs in to LinkedIn, Naukri, Foundit and Indeed, searches for jobs and applies automatically.

## Setup (once)
1. `pip install -r requirements.txt`
2. Put your resume at `documents/resume.pdf` (optional: `documents/photo.jpg`).
3. Fill in logins in `.env` (git-ignored). If you leave a password empty, log in by hand the first time. The bot's Brave profile (`brave_profile/`) remembers the login.
4. Review `config.yaml`: keywords, location, **notice period / current CTC / expected CTC**.

## Run
- Double-click `run.bat`, or run `python main.py`.
- `python main.py --dry-run` lists the jobs it would apply to, without applying.
- `python main.py --sites naukri linkedin --max 5` runs only those portals and limits applications.

If a CAPTCHA or OTP appears, solve it in the Brave window; the bot waits up to 5 minutes.
Every job is logged to `data/applied_jobs.csv`. Jobs already applied to are never retried.

## What it can and can't do
- **LinkedIn**: only "Easy Apply" jobs. **Indeed**: only "Easily apply" jobs. **Naukri / Foundit**: jobs with an
  on-site Apply button. Jobs that send you to a company website are logged as `external` and skipped.
- When a screening question can't be answered, the job is logged as `skipped`. To fix that, add a rule under
  `answers:` in `config.yaml`; skipped jobs are retried on the next run.
- Portals change their pages often. If one stops working, the selectors in `bot/<site>.py` need updating.
- LinkedIn and Indeed prohibit automated applying, and accounts can get restricted. Keep `max_applications_per_site` low.
