import hashlib
from urllib.parse import urlencode

from .base import SiteBot
from .common import fill_form, pause


class FounditBot(SiteBot):
    name = "foundit"
    env_prefix = "FOUNDIT"

    def logged_in(self):
        url = self.page.url
        return "foundit.in" in url and "login" not in url and self.page.locator(
            "text=/my profile|dashboard|logout/i").count() > 0

    def login(self):
        pg = self.page
        pg.goto("https://www.foundit.in/seeker/dashboard", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        if self.logged_in():
            return True
        email, password = self.creds()
        if "login" not in pg.url:
            pg.goto("https://www.foundit.in/rio/login", wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
        if email and password:
            try:
                pg.locator("#userName, input[name='userName'], input[type='email'], "
                           "input[placeholder*='Email' i]").first.fill(email)
                pwd = pg.locator("input[type='password']").first
                if not pwd.count() or not pwd.is_visible():
                    pg.locator("text=/(login|sign in) (via|with|using) password/i").first.click()
                    pg.wait_for_timeout(1000)
                pg.locator("input[type='password']").first.fill(password)
                self.click_button(pg, r"^(login|log in|sign in)$")
                pg.wait_for_timeout(5000)
            except Exception:
                pass
        return self.logged_in() or self.wait_until(
            lambda: "login" not in self.page.url,
            "finish logging in to Foundit in the Brave window (OTP / password)...")

    def search_url(self, kw, loc, start):
        return "https://www.foundit.in/srp/results?" + urlencode({
            "query": kw, "locations": loc, "start": start, "limit": 15,
            "experienceRanges": f"{self.search.get('min_experience', 0)}~{self.search.get('max_experience', 2)}",
            "jobFreshness": self.search.get("posted_within_days", 7),
        })

    def run(self):
        pg = self.page
        for kw, loc in self.searches():
            for page_no in range(int(self.search.get("pages_per_search", 2))):
                if self.done():
                    return
                pg.goto(self.search_url(kw, loc, page_no * 15), wait_until="domcontentloaded")
                pg.wait_for_timeout(5000)
                self.check_human_verification()
                cards = pg.locator(".srpResultCardContainer, .cardContainer, [class*='jobCard']")
                if cards.count() == 0:
                    break
                for i in range(cards.count()):
                    if self.done():
                        return
                    self.handle_card(cards.nth(i))

    def handle_card(self, card):
        pg = self.page
        title = self.text(card, ".jobTitle, [class*='jobTitle'], h3")
        company = self.text(card, ".companyName, [class*='companyName'], .company-name")
        if not title:
            return
        job_id = hashlib.md5(f"{title}|{company}".lower().encode()).hexdigest()[:12]
        if self.tracker.seen(self.name, job_id) or not self.wanted(title, company):
            return
        apply = card.locator("button, div[class*='applyBtn'], span[class*='apply']").filter(
            has_text="Apply").first
        if not apply.count():
            return
        label = apply.inner_text().strip().lower()
        if "applied" in label:
            self.tracker.record(self.name, job_id, title, company, "already_applied")
            return
        if "company" in label:
            self.tracker.record(self.name, job_id, title, company, "external_site")
            return
        if self.dry_run:
            self.log(f"would apply: {title} @ {company}")
            return

        pages_before = len(self.ctx.pages)
        apply.scroll_into_view_if_needed()
        apply.click()
        pg.wait_for_timeout(4000)
        if len(self.ctx.pages) > pages_before:  # opened an external company site
            self.ctx.pages[-1].close()
            self.tracker.record(self.name, job_id, title, company, "external_site")
            return
        status = self.finish_application({"title": title, "company": company}, card)
        self.tracker.record(self.name, job_id, title, company, status)
        if status == "applied":
            self.applied += 1
        pause(2, 5)

    def finish_application(self, job, card):
        pg = self.page
        for _ in range(6):
            body = pg.locator("body").inner_text().lower()
            if "successfully applied" in body or "application submitted" in body or (
                    "applied" in self.text(card, "button, [class*='apply']").lower()):
                return "applied"
            dialog = pg.locator("[role='dialog'], .modal, [class*='Modal']").first
            if dialog.count() and dialog.is_visible():
                fill_form(pg, dialog, self.prof, job)
                if not self.click_button(dialog, r"submit|apply|continue|next|save"):
                    break
            pg.wait_for_timeout(2500)
        return "skipped"
