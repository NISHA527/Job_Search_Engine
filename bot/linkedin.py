import re
from urllib.parse import quote, urlencode

from .base import SiteBot
from .common import fill_form, pause
from .external import apply_external
from .simpleform import REQUIRED_EMPTY_JS

# LinkedIn's 2026 job search: cards carry the job id in componentkey, Easy Apply opens a native <dialog>.
# The older selectors are kept as fallbacks in case an account still gets the old layout.
CARDS = "div[role='button'][componentkey^='job-card-component-ref-'], li[data-occludable-job-id]"
EASY_APPLY = "button[aria-label^='Easy Apply to' i]:visible, button#jobs-apply-button-id:visible"
COMPANY_SITE = "a[aria-label='Apply on company website']:visible, button[aria-label^='Apply to' i]:visible"
MODAL = "dialog[open], .jobs-easy-apply-modal, div[data-test-modal][role='dialog']"
# LinkedIn ignores a plain location name in the new search and shows all of India, so use its location ids
GEO_IDS = {"bangalore": "105214831", "bengaluru": "105214831", "hyderabad": "105556991", "pune": "114806696",
           "chennai": "106888327", "mumbai": "106164952", "india": "102713980"}


class LinkedInBot(SiteBot):
    """Applies to 'Easy Apply' jobs inside LinkedIn and to jobs whose Apply button goes to a company site
    (company sites on Workday are filled in automatically; others are logged as external_site)."""
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
            "keywords": kw, "sortBy": "DD", "start": start,
            "f_TPR": f"r{int(self.search.get('posted_within_days', 7)) * 86400}",
        }
        geo = GEO_IDS.get(loc.strip().lower())
        if geo:
            params["geoId"] = geo
        else:
            params["location"] = loc
        if int(self.search.get("max_experience", 2)) <= 2:
            params["f_E"] = "1,2"  # internship + entry level
        if self.search.get("linkedin_easy_apply_only"):
            params["f_AL"] = "true"
        return "https://www.linkedin.com/jobs/search-results/?" + urlencode(params, quote_via=quote)

    def run(self):
        for kw, loc in self.searches():
            for page_no in range(int(self.search.get("pages_per_search", 2))):
                if self.done():
                    return
                self.results_url = self.search_url(kw, loc, page_no * 25)
                self.open_results()
                ids = self.card_ids()
                if not ids:
                    break
                for job_id in ids:
                    if self.done():
                        return
                    if self.handle_card(job_id) is not False:
                        yield  # let the other tab take a turn

    def open_results(self):
        pg = self.page
        pg.goto(self.results_url, wait_until="domcontentloaded")
        self.check_human_verification()
        for attempt in range(3):
            try:
                pg.locator(CARDS).first.wait_for(timeout=15000)
                break
            except Exception:
                # the new search page sometimes hangs on a spinner (e.g. right after the feed) - reload it
                pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2000)

    def card_ids(self):
        ids = self.page.locator(CARDS).evaluate_all(
            "els => els.map(e => (e.getAttribute('componentkey') || '').replace('job-card-component-ref-', '')"
            " || e.getAttribute('data-occludable-job-id')).filter(Boolean)")
        self.log(f"{len(ids)} jobs on this results page")
        return ids

    def find_card(self, job_id):
        selector = f"[componentkey='job-card-component-ref-{job_id}'], li[data-occludable-job-id='{job_id}']"
        card = self.page.locator(selector).first
        if not card.count():
            self.open_results()  # we were on another page (company site / job page) - reload the list
        return card if card.count() else None

    def job_info(self, card):
        """Title from the card's 'Dismiss <title> job' button, company from the card text."""
        title = ""
        dismiss = card.locator("button[aria-label^='Dismiss']").first
        if dismiss.count():
            title = re.sub(r"^Dismiss\s+|\s+job$", "", dismiss.get_attribute("aria-label") or "").strip()
        if not title:
            title = self.text(self.page, ".job-details-jobs-unified-top-card__job-title, h1")
        lines = [l.strip() for l in card.inner_text().split("\n") if l.strip()]
        lines = [l for l in lines if title not in l and not l.startswith("Selected")]
        company = lines[0] if lines else ""
        return title, company

    def handle_card(self, job_id):
        pg = self.page
        if self.tracker.seen(self.name, job_id):
            return False
        card = self.find_card(job_id)
        if not card:
            return False
        try:
            card.scroll_into_view_if_needed()
            card.click()
        except Exception:
            return
        pg.wait_for_timeout(3000)
        title, company = self.job_info(card)
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        job = {"title": title, "company": company}
        if not self.wanted(title, company):
            return
        if self.tracker.applied_to_role(company, title):
            self.tracker.record(self.name, job_id, title, company, "already_applied", url)
            return  # same role listed again - don't send a second application
        easy = pg.locator(EASY_APPLY).first
        external = pg.locator(COMPANY_SITE).first
        if not easy.count() and not external.count():
            if pg.get_by_text(re.compile(r"^Applied\b|application (was )?submitted", re.I)).count():
                self.tracker.record(self.name, job_id, title, company, "already_applied", url)
            return
        if not easy.count():
            if self.dry_run or self.search.get("linkedin_easy_apply_only"):
                self.log(f"would apply on company site: {title} @ {company}")
                return
            status = apply_external(self, external, job)
        else:
            if self.dry_run:
                self.log(f"would apply (Easy Apply): {title} @ {company}")
                return
            easy.click()
            status = self.easy_apply(job)
        self.tracker.record(self.name, job_id, title, company, status, url)
        if status == "applied":
            self.applied += 1
        pause(2, 4)

    def easy_apply(self, job):
        pg = self.page
        modal = pg.locator(MODAL).last
        try:
            modal.wait_for(timeout=10000)
        except Exception:
            return "failed"
        stuck = 0
        for _ in range(20):
            pause(1, 2)
            # a saved resume is usually already selected; only upload ours when none is
            resume_chosen = modal.locator("input[type=radio][aria-label$='.pdf' i]:checked, "
                                          "input[type=radio][aria-label$='.docx' i]:checked").count() > 0
            fill_form(pg, modal, self.prof, job, skip_files=resume_chosen)
            self.unfollow_company(modal)

            submit = modal.get_by_role("button", name=re.compile(r"^submit", re.I))
            if submit.count():
                submit.first.click()
                pg.wait_for_timeout(4000)
                self.close_dialogs()
                return "applied"
            nxt = modal.get_by_role("button", name=re.compile(r"^(next|review|continue)", re.I))
            if not nxt.count():
                break
            before = modal.inner_text()
            nxt.first.click()
            pg.wait_for_timeout(2000)
            if modal.inner_text() == before:  # page didn't advance - a required question is unanswered
                stuck += 1
                if stuck >= 2:
                    errors = [t.strip() for t in modal.locator(
                        "[role=alert], .artdeco-inline-feedback--error").all_inner_texts() if t.strip()]
                    open_qs = self.open_questions(modal)
                    self.log(f"stuck on: {open_qs or errors or 'unknown'} - add a rule in config.yaml")
                    self.note_questions(open_qs or errors, job)
                    self.save_debug(modal, job)
                    break
            else:
                stuck = 0
        self.discard()
        return "skipped"

    def open_questions(self, modal):
        try:
            return modal.evaluate(REQUIRED_EMPTY_JS)
        except Exception:
            return []

    def unfollow_company(self, modal):
        boxes = modal.locator("input[type=checkbox]")
        for i in range(boxes.count()):
            box = boxes.nth(i)
            try:
                label = box.evaluate("el => (el.labels && el.labels[0] ? el.labels[0].innerText : '') + ' ' + el.id")
                if re.search(r"follow", label, re.I) and box.is_checked():
                    box.uncheck(force=True)
            except Exception:
                continue

    def close_dialogs(self):
        pg = self.page
        for _ in range(3):
            done = pg.get_by_role("button", name=re.compile(r"^(done|dismiss|close)$", re.I))
            if not done.count():
                break
            try:
                done.first.click()
            except Exception:
                break
            pg.wait_for_timeout(800)

    def discard(self):
        pg = self.page
        try:
            pg.locator(MODAL).last.locator("button[aria-label='Dismiss']").first.click()
            pg.wait_for_timeout(1200)
            if not self.click_button(pg, r"^discard$"):
                pg.keyboard.press("Escape")
        except Exception:
            pg.keyboard.press("Escape")
