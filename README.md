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
- Each portal gets its **own Brave tab** (e.g. tab 1 Naukri, tab 2 LinkedIn). It logs in to all of them first, then the
  tabs take turns applying, one job each.
- **LinkedIn**: "Easy Apply" jobs and jobs whose Apply button goes to a company site (set
  `linkedin_easy_apply_only: true` in config.yaml to stop that). **Naukri**: on-site Apply (resume is uploaded when the
  chatbot asks for it) and company-site jobs. **Indeed**: only "Easily apply" jobs. **Foundit**: on-site Apply only.
- **Company sites**: Workday (`*.myworkdayjobs.com`) is filled in automatically. The bot creates an account with
  `WORKDAY_EMAIL` / `WORKDAY_PASSWORD` from `.env`, or signs in if that company already has one
  (remembered in `data/workday_accounts.txt`). If Workday sends a verification email, click the link in Gmail; the bot
  waits 3 minutes. Other company sites are logged as `external_site` and skipped.
- When a screening question can't be answered, the job is logged as `skipped`. To fix that, add a rule under
  `answers:` in `config.yaml`; skipped jobs are retried on the next run.
- Portals change their pages often. If one stops working, the selectors in `bot/<site>.py` need updating.
- LinkedIn and Indeed prohibit automated applying, and accounts can get restricted. Keep `max_applications_per_site` low.
