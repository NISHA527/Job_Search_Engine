import csv
import datetime

# Jobs with these statuses are never retried. "skipped" / "failed" jobs are retried next run.
FINAL = {"applied", "already_applied", "external"}


class Tracker:
    FIELDS = ["time", "site", "job_id", "title", "company", "status", "url"]

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.done = set()
        if path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["status"] in FINAL:
                        self.done.add((row["site"], row["job_id"]))

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
        print(f"  [{site}] {status:<15} {title} @ {company}")
