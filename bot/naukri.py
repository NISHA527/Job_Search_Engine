import re

from .base import SiteBot
from .common import pause, pick_option
from .external import apply_external

RESUME_QUESTION = re.compile(r"upload.*(resume|cv)|(resume|cv).*upload|attach.*(resume|cv)", re.I)
SKIP_CHIP = re.compile(r"^\s*skip( this question)?\s*$", re.I)
YES_NO_QUESTION =re.compile(r"^\s*(are|do|did|have|has|can|could|will|would|is)\s+you\b|willing|comfortable", re.I)


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
                    if self.handle_job(job["href"], job["title"]) is not False:
                        yield  # let the other tab take a turn

    def handle_job(self, url, title):
        m = re.search(r"-(\d{9,})", url)
        job_id = m.group(1) if m else url.split("?")[0]
        if self.tracker.seen(self.name, job_id):
            return False
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
            external = pg.locator("#company-site-button").first
            if not external.count():
                return
            if self.dry_run:
                self.log(f"would apply on company site: {title} @ {company}")
                return
            status = apply_external(self, external, {"title": title, "company": company})
            self.tracker.record(self.name, job_id, title, company, status, url)
            if status == "applied":
                self.applied += 1
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
        self.uploaded_for = None
        for _ in range(40):  # some recruiters ask 10+ questions
            pg.wait_for_timeout(2500)
            body = pg.locator("body").inner_text().lower()
            if "successfully applied" in body or pg.locator("#already-applied").count():
                return "applied"
            drawer = pg.locator("[class*='chatbot_Drawer']").first
            if not (drawer.count() and drawer.is_visible()):
                dialog = pg.locator("[role=dialog]:has(input[type=file]), [class*='Modal']:has(input[type=file])").first
                if dialog.count() and dialog.is_visible():
                    self.upload_resume(dialog)  # "upload your resume to apply" pop-up
                elif last_question is not None:
                    return self.confirm_applied()  # the chat closed after the last answer
                continue
            # the newest bot message is the open question (the first one is often just a greeting)
            msgs = drawer.locator("li.botItem")
            question = msgs.last.inner_text().strip() if msgs.count() else ""
            progress = (msgs.count(), question)
            repeats = repeats + 1 if progress == last_question else 0
            last_question = progress
            if repeats >= 2:
                self.log(f"stuck on question: {question.strip()[:150]!r}")
                self.note_questions([question], job)
                self.save_debug(drawer, job)
                return "skipped"  # couldn't get past a question - add a rule to config.yaml
            self.answer_chatbot(drawer, question, job)
        return self.confirm_applied()

    def confirm_applied(self):
        """Naukri's success message varies, so reload the job page and see whether it now says Applied."""
        pg = self.page
        try:
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)
            if pg.locator("#already-applied").count() or pg.get_by_text(re.compile(r"^\s*applied\s*$", re.I)).count():
                return "applied"
        except Exception:
            pass
        return "skipped"

    @staticmethod
    def click_chip(chip):
        text = chip.locator("span, label").first
        (text if text.count() else chip).click()

    def upload_resume(self, scope):
        """Attach the resume to a file input inside `scope`. Returns True if a file was attached."""
        if not self.prof.resume.exists():
            return False
        # the chat wants its "Upload Resume" button clicked, which opens a file picker
        chip = scope.locator("[class*='chatbot_Chip']:visible").filter(has_text=re.compile(r"upload", re.I)).first
        if chip.count():
            try:
                with self.page.expect_file_chooser(timeout=8000) as chooser:
                    self.click_chip(chip)
                chooser.value.set_files(str(self.prof.resume))
                self.log("uploaded resume")
                pause(3, 4)
                return True
            except Exception:
                pass
        file_input = scope.locator("input[type=file]")
        if not file_input.count():
            return False
        file_input.first.set_input_files(str(self.prof.resume))
        self.log("uploaded resume")
        pause(2, 3)
        if not self.click_button(scope, r"^(save|submit|send|upload|done|continue)$"):
            send = scope.locator(".sendMsg, [class*='sendMsg']").first
            if send.count():
                send.click()
        return True

    def answer_chatbot(self, drawer, question, job):
        # only when asked - the chat window always holds a hidden file box, so its presence means nothing
        if RESUME_QUESTION.search(question or "") and question != self.uploaded_for:
            self.uploaded_for = question
            if self.upload_resume(drawer) or self.upload_resume(self.page):
                return
        options = drawer.locator(".ssrc__radio-btn-container:visible, [class*='chatbot_Chip']:visible")
        labels = [t.strip() for t in options.all_inner_texts()]
        # "Skip this question" marks an optional question - it is not one of the answers
        choices = [l for l in labels if not SKIP_CHIP.match(l)]
        skip = labels.index(next(l for l in labels if SKIP_CHIP.match(l))) if len(choices) < len(labels) else None
        if choices:
            choice = pick_option(choices, self.prof.answers(question, job))
            if choice not in choices:
                if skip is not None:
                    self.click_chip(options.nth(skip))  # optional and we have no answer - skip just this question
                return  # otherwise deliberately unanswered - the job ends up "skipped"
            chosen = options.nth(labels.index(choice))
            label = chosen.locator("label")
            if label.count():
                label.first.click()  # clicking the row's middle can miss the radio button
            elif chosen.locator("input").count():
                chosen.locator("input").first.check(force=True)
            else:
                self.click_chip(chosen)
        else:
            box = drawer.locator("[contenteditable='true']").first
            if not box.count():
                return
            answer = self.prof.guess(question, job)
            if answer is None and YES_NO_QUESTION.search(question or ""):
                answer = "Yes"
            if not answer:
                if skip is not None:
                    self.click_chip(options.nth(skip))  # optional and we have no answer - skip just this question
                return  # no answer for this one - the job ends up "skipped" instead of getting a wrong reply
            box.click()
            box.type(answer, delay=40)
        pause(0.5, 1)
        send = drawer.locator(".sendMsg, [class*='sendMsg']").first
        if send.count():
            send.click()
        else:
            self.click_button(drawer, r"^(save|submit|send)$")
