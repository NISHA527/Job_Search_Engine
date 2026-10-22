import re

from .base import SiteBot
from .common import pause, pick_option


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class NaukriBot(SiteBot):
    name = "naukri"
    env_prefix = "NAUKRI"

    def logged_in(self):
        return "mnjuser" in self.page.url and not self.page.locator("#usernameField").count()

    def login(self):
        pg = self.page
        pg.goto("https://www.naukri.com/mnjuser/homepage", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        if self.logged_in():
            return True
        email, password = self.creds()
        pg.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded")
        pg.wait_for_timeout(2000)
        if email and password and pg.locator("#usernameField").count():
            pg.fill("#usernameField", email)
            pause(0.5, 1)
            pg.fill("#passwordField", password)
            pg.click("button[type=submit]")
            pg.wait_for_timeout(5000)
        return self.logged_in() or self.wait_until(
            self.logged_in, "finish logging in to Naukri in the Brave window...")

    def search_url(self, kw, loc, page_no):
        suffix = f"-{page_no}" if page_no > 1 else ""
        return (f"https://www.naukri.com/{slug(kw)}-jobs-in-{slug(loc)}{suffix}"
                f"?experience={self.search.get('max_experience', 2)}"
                f"&jobAge={self.search.get('posted_within_days', 7)}")

    def run(self):
        pg = self.page
        for kw, loc in self.searches():
            for page_no in range(1, int(self.search.get("pages_per_search", 2)) + 1):
                if self.done():
                    return
                pg.goto(self.search_url(kw, loc, page_no), wait_until="domcontentloaded")
                pg.wait_for_timeout(4000)
                self.check_human_verification()
                jobs = pg.locator(".srp-jobtuple-wrapper a.title, article.jobTuple a.title").evaluate_all(
                    "els => els.map(e => ({href: e.href, title: e.innerText.trim()}))")
                if not jobs:
                    break
                for job in jobs:
                    if self.done():
                        return
                    self.handle_job(job["href"], job["title"])

    def handle_job(self, url, title):
        m = re.search(r"-(\d{9,})", url)
        job_id = m.group(1) if m else url.split("?")[0]
        if self.tracker.seen(self.name, job_id):
            return
        pg = self.page
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        company = self.text(pg, "[class*='jd-header-comp-name'] a, .jd-header-comp-name a")
        if not self.wanted(title, company):
            return
        if pg.locator("#already-applied").count():
            self.tracker.record(self.name, job_id, title, company, "already_applied", url)
            return
        button = pg.locator("#apply-button").first
        if not button.count():
            if pg.locator("#company-site-button").count():
                self.tracker.record(self.name, job_id, title, company, "external", url)
            return
        if self.dry_run:
            self.log(f"would apply: {title} @ {company}")
            return
        button.click()
        status = self.finish_application({"title": title, "company": company})
        self.tracker.record(self.name, job_id, title, company, status, url)
        if status == "applied":
            self.applied += 1
        pause(2, 5)

    def finish_application(self, job):
        """After clicking Apply, Naukri either confirms or opens a chatbot with screening questions."""
        pg = self.page
        last_question, repeats = None, 0
        for _ in range(15):
            pg.wait_for_timeout(2500)
            body = pg.locator("body").inner_text().lower()
            if "successfully applied" in body or pg.locator("#already-applied").count():
                return "applied"
            drawer = pg.locator("[class*='chatbot_Drawer']").first
            if not (drawer.count() and drawer.is_visible()):
                continue
            question = self.text(drawer, "li.botItem:last-of-type, [class*='botMsg']:last-of-type")
            if not question:
                msgs = drawer.locator("[class*='botMsg']")
                question = msgs.last.inner_text() if msgs.count() else ""
            repeats = repeats + 1 if question == last_question else 0
            last_question = question
            if repeats >= 2:
                return "skipped"  # couldn't get past a question - add a rule to config.yaml
            self.answer_chatbot(drawer, question, job)
        return "skipped"

    def answer_chatbot(self, drawer, question, job):
        options = drawer.locator(".ssrc__radio-btn-container, [class*='chatbot_Chip']")
        if options.count():
            labels = [t.strip() for t in options.all_inner_texts()]
            choice = pick_option(labels, self.prof.answers(question, job))
            if choice not in labels:
                return  # deliberately unanswered - the job ends up "skipped"
            options.nth(labels.index(choice)).click()
        else:
            box = drawer.locator("[contenteditable='true']").first
            if not box.count():
                return
            answer = self.prof.guess(question, job)
            if self.prof.answers(question, job) == []:
                return
            box.click()
            box.type(answer or "Yes", delay=40)
        pause(0.5, 1)
        send = drawer.locator(".sendMsg, [class*='sendMsg']").first
        if send.count():
            send.click()
        else:
            self.click_button(drawer, r"^(save|submit|send)$")
