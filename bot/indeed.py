from urllib.parse import urlencode

from .base import SiteBot
from .common import fill_form, pause

BASE = "https://in.indeed.com"


class IndeedBot(SiteBot):
    """Applies to 'Easily apply' jobs (Indeed Apply). Other jobs redirect to company sites."""
    name = "indeed"
    env_prefix = "INDEED"

    def logged_in(self):
        pg = self.page
        return "indeed.com" in pg.url and "secure.indeed.com/auth" not in pg.url and pg.locator(
            "[data-gnav-element-name='AccountMenu'], #AccountMenu, [data-gnav-element-name='Profile']").count() > 0

    def login(self):
        pg = self.page
        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        self.check_human_verification()
        if self.logged_in():
            return True
        email, password = self.creds()
        pg.goto("https://secure.indeed.com/auth?hl=en_IN&co=IN", wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        self.check_human_verification()
        if email:
            try:
                pg.locator("input[type='email']").first.fill(email)
                pg.locator("button[type='submit']").first.click()
                pg.wait_for_timeout(4000)
                pwd = pg.locator("input[type='password']").first
                if password and pwd.count() and pwd.is_visible():
                    pwd.fill(password)
                    pg.locator("button[type='submit']").first.click()
                    pg.wait_for_timeout(4000)
            except Exception:
                pass

        def back_on_indeed():
            if "secure.indeed.com" in pg.url:
                return False
            if "indeed.com" not in pg.url:
                return False
            return True
        # Indeed usually emails a one-time code - type it in the browser.
        ok = back_on_indeed() or self.wait_until(
            back_on_indeed, "enter the code Indeed emailed you (or finish login) in the Brave window...")
        return ok

    def search_url(self, kw, loc, start):
        return f"{BASE}/jobs?" + urlencode({
            "q": kw, "l": loc, "start": start, "sort": "date",
            "fromage": self.search.get("posted_within_days", 7),
            "sc": "0kf:attr(DSQF7);",  # "Easily apply" filter
        })

    def run(self):
        pg = self.page
        for kw, loc in self.searches():
            for page_no in range(int(self.search.get("pages_per_search", 2))):
                if self.done():
                    return
                pg.goto(self.search_url(kw, loc, page_no * 10), wait_until="domcontentloaded")
                pg.wait_for_timeout(4000)
                self.check_human_verification()
                jobs = pg.locator("a[data-jk]").evaluate_all(
                    "els => els.map(e => ({jk: e.dataset.jk, title: e.innerText.trim()}))")
                if not jobs:
                    break
                seen_here = set()
                for job in jobs:
                    if self.done():
                        return
                    if job["jk"] in seen_here:
                        continue
                    seen_here.add(job["jk"])
                    self.handle_job(job["jk"], job["title"])

    def handle_job(self, jk, title):
        if self.tracker.seen(self.name, jk):
            return
        pg = self.page
        url = f"{BASE}/viewjob?jk={jk}"
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        self.check_human_verification()
        company = self.text(pg, "[data-testid='inlineHeader-companyName'], [data-company-name='true']")
        if not self.wanted(title, company):
            return
        button = pg.locator("#indeedApplyButton, button[aria-label*='Apply now']").first
        if not button.count():
            self.tracker.record(self.name, jk, title, company, "external", url)
            return
        if "applied" in button.inner_text().lower():
            self.tracker.record(self.name, jk, title, company, "already_applied", url)
            return
        if self.dry_run:
            self.log(f"would apply: {title} @ {company}")
            return

        pages_before = len(self.ctx.pages)
        button.click()
        pg.wait_for_timeout(4000)
        flow = self.ctx.pages[-1] if len(self.ctx.pages) > pages_before else pg
        status = self.apply_flow(flow, {"title": title, "company": company})
        if flow is not pg:
            flow.close()
        self.tracker.record(self.name, jk, title, company, status, url)
        if status == "applied":
            self.applied += 1
        pause(2, 5)

    def apply_flow(self, page, job):
        last_url, stuck = None, 0
        for _ in range(15):
            page.wait_for_timeout(2500)
            body = page.locator("body").inner_text().lower()
            if "application has been submitted" in body or "your application was sent" in body:
                return "applied"
            fill_form(page, page.locator("body"), self.prof, job)
            if self.click_button(page, r"submit (your )?application"):
                continue
            if not self.click_button(page, r"^(continue|review your application|next)"):
                break
            stuck = stuck + 1 if page.url == last_url else 0
            last_url = page.url
            if stuck >= 3:  # same page keeps failing validation
                break
        return "skipped"
