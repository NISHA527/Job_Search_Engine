import csv
import datetime

# Jobs with these statuses are never retried. "skipped" / "failed" jobs are retried next run.
# "external_site" = company website the bot cannot fill in (only Workday is supported).
FINAL = {"applied", "already_applied", "external_site", "unconfirmed"}  # unconfirmed: never resend


class Tracker:
    FIELDS = ["time", "site", "job_id", "title", "company", "status", "url"]

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.done = set()
        self.applied_roles = set()  # (company, title) - the same role is often listed several times
        if path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["status"] in FINAL:
                        self.done.add((row["site"], row["job_id"]))
                    if row["status"] == "applied":
                        self.applied_roles.add(self.role(row["company"], row["title"]))

    @staticmethod
    def role(company, title):
        return ((company or "").strip().lower(), (title or "").strip().lower())

    def applied_to_role(self, company, title):
        return self.role(company, title) in self.applied_roles

    def seen(self, site, job_id):
        return (site, str(job_id)) in self.done

    def record(self, site, job_id, title, company, status, url=""):
        new = not self.path.exists()
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.FIELDS)
            if new:
                w.writeheader()
            w.writerow({
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "site": site, "job_id": job_id, "title": title,
                "company": company, "status": status, "url": url,
            })
        if status in FINAL:
            self.done.add((site, str(job_id)))
        if status == "applied":
            self.applied_roles.add(self.role(company, title))
        print(f"  [{site}] {status:<15} {title} @ {company}")
