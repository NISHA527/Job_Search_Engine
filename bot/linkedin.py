from urllib.parse import urlencode

from .base import SiteBot
from .common import fill_form, pause

MODAL = ".jobs-easy-apply-modal, div[role='dialog'][aria-labelledby*='easy-apply'], div[data-test-modal][role='dialog']"


class LinkedInBot(SiteBot):
    """Applies only to 'Easy Apply' jobs (the rest redirect to company sites)."""
    name = "linkedin"
    env_prefix = "LINKEDIN"

    def logged_in(self):
        return any(p in self.page.url for p in ("/feed", "/jobs", "/mynetwork", "/in/"))

    def login(self):
        pg = self.page
        pg.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        if self.logged_in():
            return True
        email, password = self.creds()
        pg.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        email_box = pg.locator("#username:visible, input[name='session_key']:visible, input[type='email']:visible").first
        password_box = pg.locator("#password:visible, input[name='session_password']:visible, "
                                  "input[type='password']:visible").first
        if email and password and email_box.count():
            email_box.fill(email)
            pause(0.5, 1)
            password_box.fill(password)
            pause(0.5, 1)
            if not self.click_button(pg, r"^sign in$"):
                password_box.press("Enter")
            pg.wait_for_timeout(6000)
            if pg.locator("text=/wrong email or password|couldn.t find an account/i").count():
                self.log("LinkedIn says WRONG EMAIL OR PASSWORD - fix LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env")
                return False
        return self.logged_in() or self.wait_until(
            self.logged_in, "finish logging in (CAPTCHA / OTP) in the Brave window...")

    def search_url(self, kw, loc, start):
        params = {
            "keywords": kw, "location": loc, "f_AL": "true", "sortBy": "DD", "start": start,
            "f_TPR": f"r{int(self.search.get('posted_within_days', 7)) * 86400}",
        }
        if int(self.search.get("max_experience", 2)) <= 2:
            params["f_E"] = "1,2"  # internship + entry level
        return "https://www.linkedin.com/jobs/search/?" + urlencode(params)

    def run(self):
        pg = self.page
        for kw, loc in self.searches():
            for page_no in range(int(self.search.get("pages_per_search", 2))):
                if self.done():
                    return
                pg.goto(self.search_url(kw, loc, page_no * 25), wait_until="domcontentloaded")
                pg.wait_for_timeout(4000)
                self.check_human_verification()
                cards = pg.locator("li[data-occludable-job-id]")
                if cards.count() == 0:
                    break
                for i in range(cards.count()):
                    if self.done():
                        return
                    self.handle_card(cards.nth(i))

    def handle_card(self, card):
        pg = self.page
        job_id = card.get_attribute("data-occludable-job-id")
        if not job_id or self.tracker.seen(self.name, job_id):
            return
        try:
            card.scroll_into_view_if_needed()
            card.click()
        except Exception:
            return
        pause(1.5, 3)
        title = self.text(pg, ".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title, h1")
        company = self.text(pg, ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name")
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        if not self.wanted(title, company):
            return
        button = pg.locator("button#jobs-apply-button-id, .jobs-apply-button--top-card button, button.jobs-apply-button").first
        label = (button.inner_text() if button.count() else "").lower()
        if "easy apply" not in label:
            if "applied" in self.text(pg, ".jobs-s-apply, .artdeco-inline-feedback").lower():
                self.tracker.record(self.name, job_id, title, company, "already_applied", url)
            return
        if self.dry_run:
            self.log(f"would apply: {title} @ {company}")
            return
        button.click()
        status = self.easy_apply({"title": title, "company": company})
        self.tracker.record(self.name, job_id, title, company, status, url)
        if status == "applied":
            self.applied += 1
        pause(2, 5)

    def easy_apply(self, job):
        pg = self.page
        modal = pg.locator(MODAL).first
        try:
            modal.wait_for(timeout=8000)
        except Exception:
            return "failed"
        errors_in_a_row = 0
        for _ in range(15):
            pause(1, 2)
            fill_form(pg, modal, self.prof, job)
            follow = modal.locator("#follow-company-checkbox")
            if follow.count() and follow.is_checked():
                modal.locator("label[for='follow-company-checkbox']").click()

            submit = modal.locator("button[aria-label='Submit application']")
            if submit.count():
                submit.click()
                pg.wait_for_timeout(3000)
                self.close_dialogs()
                return "applied"
            nxt = modal.locator("button[aria-label='Review your application'], "
                                "button[aria-label='Continue to next step']").first
            if not nxt.count():
                break
            nxt.click()
            pg.wait_for_timeout(1500)
            if modal.locator(".artdeco-inline-feedback--error").count():
                errors_in_a_row += 1
                if errors_in_a_row >= 2:  # a question we can't answer - add a rule to config.yaml
                    break
            else:
                errors_in_a_row = 0
        self.discard()
        return "skipped"

    def close_dialogs(self):
        for _ in range(2):
            dismiss = self.page.locator("button[aria-label='Dismiss']").first
            if dismiss.count() and dismiss.is_visible():
                dismiss.click()
                self.page.wait_for_timeout(800)

    def discard(self):
        pg = self.page
        try:
            pg.locator("button[aria-label='Dismiss']").first.click()
            pg.wait_for_timeout(1000)
            confirm = pg.locator("button[data-control-name='discard_application_confirm_btn']")
            if confirm.count():
                confirm.click()
            else:
                self.click_button(pg, r"^discard$")
        except Exception:
            pg.keyboard.press("Escape")
