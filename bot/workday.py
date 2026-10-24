"""Workday career sites (*.myworkdayjobs.com): create an account (or sign in), then fill every step and submit.

Each company on Workday has its own accounts. The first time, the bot creates one with WORKDAY_EMAIL /
WORKDAY_PASSWORD from .env; companies that already have an account are remembered in data/workday_accounts.txt.
"""
import datetime
import os
import re
from urllib.parse import urlparse

from .common import _matching_option, fill_form, pause


def A(automation_id):
    return f"[data-automation-id='{automation_id}']"


NEXT = f"{A('bottom-navigation-next-button')}, {A('pageFooterNextButton')}"
# the empty-dropdown label in the languages Workday sites use ("Select One", German "Wert auswählen", ...)
UNANSWERED_DROPDOWN = re.compile(r"^\s*(select one|select|choose one|wert auswählen|bitte auswählen|auswählen|"
                                 r"sélectionner|choisir|seleccionar|selecione|seleziona|selecteren|wybierz)\s*$", re.I)
SIGN_IN_WITH_EMAIL = (f"{A('SignInWithEmailButton')}, button:has-text('Sign in with email'), "
                      "button:has-text('Sign In with Email'), button:has-text('Create Account with email')")
APPLY_BUTTONS =f"{A('adventureButton')}, {A('jobPostingApplyButton')}, {A('continueButton')}"
SUCCESS = re.compile(r"application (has been |was )?(successfully )?submitted|thank you for (applying|your application)"
                     r"|we have received your application|successfully applied", re.I)
ALREADY_APPLIED = re.compile(r"already applied|you have applied for this job", re.I)
VERIFY_EMAIL = re.compile(r"verify your (email|account)|verification (email|link)|activate your account", re.I)
ACCOUNT_EXISTS = re.compile(r"already (exists|in use|been used|registered)|account with this email", re.I)
WRONG_LOGIN = re.compile(r"wrong|incorrect|invalid (email|password|username)", re.I)
AGREE = re.compile(r"agree|acknowledge|consent|certify|terms|accept|privacy", re.I)

FIELD_QUESTIONS_JS = r"""
(els) => els.map(el => {
  const field = el.closest('[data-automation-id^="formField"]');
  const label = field && field.querySelector('label, legend');
  return {
    question: (label ? label.innerText : (el.getAttribute('aria-label') || '')).replace(/\*/g, '').trim(),
    text: (el.innerText || el.value || '').trim(),
    selected: !!(field && field.querySelector('[data-automation-id="selectedItem"]')),
    selectedText: (field && field.querySelector('[data-automation-id="selectedItem"]') || {}).innerText || '',
  };
})
"""


class WorkdayApplier:
    @staticmethod
    def handles(url):
        return bool(re.search(r"myworkday(jobs|site)\.com|\.workday\.com", url or "", re.I))

    def __init__(self, bot, job):
        self.bot, self.page, self.prof, self.job = bot, bot.page, bot.prof, job
        self.email = os.getenv("WORKDAY_EMAIL", "").strip() or bot.prof.p.get("email", "")
        self.password = os.getenv("WORKDAY_PASSWORD", "").strip()
        self.accounts_file = bot.tracker.path.parent / "workday_accounts.txt"
        self.tried_create = self.sign_ins = 0
        self.waited_for_email = False
        self.submitted = False

    # ---- small helpers ---------------------------------------------------
    def log(self, msg):
        self.bot.log(f"workday: {msg}")

    def visible(self, selector):
        loc = self.page.locator(selector).first
        try:
            return loc.count() > 0 and loc.is_visible()
        except Exception:
            return False

    def click(self, selector):
        """Workday often lays a transparent 'click_filter' over its buttons, so fall back to a forced click."""
        loc = self.page.locator(selector).first
        try:
            loc.click(timeout=5000)
        except Exception:
            loc.click(force=True, timeout=5000)
        pause(1.5, 2.5)

    def body(self):
        try:
            return self.page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

    def tenant(self):
        u = urlparse(self.page.url)
        parts = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        return f"{u.netloc}/{parts[0] if parts else ''}".lower()

    def has_account(self):
        if not self.accounts_file.exists():
            return False
        return self.tenant() in self.accounts_file.read_text(encoding="utf-8").split()

    def remember_account(self):
        if not self.has_account():
            with open(self.accounts_file, "a", encoding="utf-8") as f:
                f.write(self.tenant() + "\n")

    # ---- main loop ---------------------------------------------------------
    def apply(self):
        if not self.password:
            self.log("WORKDAY_PASSWORD is empty in .env - skipping")
            return "failed"
        pg = self.page
        last_step, stuck = None, 0
        for _ in range(40):
            pg.wait_for_timeout(2500)
            self.bot.check_human_verification()
            self.bot.accept_cookies()
            body = self.body()
            if SUCCESS.search(body):
                self.log("application submitted")
                return "applied"
            if ALREADY_APPLIED.search(body) and not self.visible(NEXT):
                return "already_applied"

            if self.visible(A("verifyPassword")):
                if self.create_account() is False:
                    return "failed"
            elif self.visible("input[type=password]"):
                if VERIFY_EMAIL.search(body) and not self.waited_for_email:
                    self.wait_for_email_verification()
                if self.sign_in_or_create() is False:
                    return "failed"
            elif self.visible(SIGN_IN_WITH_EMAIL):
                self.click(SIGN_IN_WITH_EMAIL)  # NVIDIA-style page: Google / LinkedIn / email choices first
            elif self.visible(A("autofillWithResume")) or self.visible(A("applyManually")):
                use_resume =self.prof.resume.exists() and self.visible(A("autofillWithResume"))
                self.click(A("autofillWithResume") if use_resume else A("applyManually"))
            elif self.visible(APPLY_BUTTONS):
                self.click(APPLY_BUTTONS)  # Apply, or Continue Application for a saved draft
            elif self.visible(NEXT):
                step = self.current_step()
                stuck = stuck + 1 if step == last_step and self.errors() else 0
                last_step = step
                if stuck >= 3:
                    self.log(f"stuck on '{step}': {'; '.join(self.errors())[:300]}")
                    self.bot.note_questions(self.errors(), self.job)
                    self.bot.save_debug(pg.locator("body"), self.job)
                    self.log("add answers for these questions under answers: in config.yaml")
                    if not self.bot.ask_user(self.errors(), self.job, lambda: self.current_step() != step):
                        return "skipped"
                    stuck = 0
                    continue
                self.fill_step()
                button = pg.locator(NEXT).first
                if re.search(r"submit", button.inner_text() or "", re.I):
                    self.submitted = True
                self.click(NEXT)
            elif self.submitted:
                return "applied"  # the form is gone after Submit
        self.log(f"gave up on step '{self.current_step()}' - errors: {self.errors() or 'none shown'} - url {pg.url[:90]}")
        self.bot.note_questions(self.errors(), self.job)
        self.bot.save_debug(pg.locator("body"), self.job)
        return "skipped"

    # ---- account -------------------------------------------------------------
    def create_account(self):
        pg = self.page
        self.tried_create += 1
        if self.tried_create > 2:
            self.log("could not create an account")
            return False
        self.log(f"creating account for {self.email}")
        email = pg.locator(A("email")).first
        if email.is_enabled():  # some companies pre-fill the email and lock the box
            email.fill(self.email)
        pg.locator(A("password")).first.fill(self.password)
        pg.locator(A("verifyPassword")).first.fill(self.password)
        box = pg.locator(f"{A('createAccountCheckbox')}, input[type=checkbox]").first
        if box.count() and not box.is_checked():
            box.check(force=True)
        self.click(f"{A('click_filter')}[aria-label*='Create Account' i], {A('createAccountSubmitButton')}")
        pg.wait_for_timeout(4000)
        body = self.body()
        if ACCOUNT_EXISTS.search(body):
            self.log("account already exists - signing in instead")
            self.remember_account()
            if self.visible(A("signInLink")):
                self.click(A("signInLink"))
            return True
        if self.visible(A("verifyPassword")) and re.search(r"password must|requirement", body, re.I):
            self.log("Workday rejected the password (too weak for this company) - change WORKDAY_PASSWORD in .env")
            return False
        self.remember_account()
        return True

    def sign_in_or_create(self):
        if not self.has_account() and not self.tried_create and self.visible(A("createAccountLink")):
            self.click(A("createAccountLink"))
            return True
        pg = self.page
        self.sign_ins += 1
        if self.sign_ins > 2:
            self.log("could not sign in - check WORKDAY_EMAIL / WORKDAY_PASSWORD in .env")
            return False
        self.log(f"signing in as {self.email}")
        pg.locator(f"{A('email')}, input[type=email]").first.fill(self.email)
        pg.locator(f"{A('password')}, input[type=password]").first.fill(self.password)
        self.click(f"{A('click_filter')}[aria-label*='Sign In' i], {A('signInSubmitButton')}")
        pg.wait_for_timeout(4000)
        if self.visible("input[type=password]") and WRONG_LOGIN.search(self.body()):
            if not self.tried_create and self.visible(A("createAccountLink")):
                self.click(A("createAccountLink"))  # no account with this company yet
                return True
            if self.tried_create:
                self.log(f"new account can't sign in yet - check Gmail ({self.email}) for a verification email "
                         f"from {self.job.get('company')}, click it, and this job is retried next run")
            else:
                self.log("Workday says the email or password is wrong")
            return False
        self.remember_account()
        return True

    def wait_for_email_verification(self):
        self.waited_for_email = True
        self.bot.wait_until(
            lambda: not VERIFY_EMAIL.search(self.body()) or self.visible(NEXT),
            f"Workday sent a verification email to {self.email} - open Gmail and click the link "
            "(waiting up to 3 minutes)...", timeout=180)

    # ---- form steps ------------------------------------------------------------
    def current_step(self):
        return self.bot.text(self.page, f"{A('progressBarActiveStep')}, h2") or self.page.url

    def errors(self):
        loc = self.page.locator(f"{A('errorMessage')}:visible, {A('inputAlert')}:visible")
        try:
            return [t.strip() for t in loc.all_inner_texts() if t.strip()]
        except Exception:
            return []

    def fill_step(self):
        pg = self.page
        self.upload_resume()
        fill_form(pg, pg.locator("body"), self.prof, self.job, skip_files=True)
        self.fix_phone()
        self.fill_dates()
        self.fill_work_history()
        self.fill_dropdowns()
        self.fill_multiselects()
        self.tick_agreements()

    def fix_phone(self):
        """Workday wants just the digits (the country code is a separate field); resume autofill often adds +91 / spaces."""
        digits = re.sub(r"\D", "", str(self.prof.p.get("phone", "")))[-10:]
        box = self.page.locator(f"input{A('phone-number')}:visible, {A('phone-number')} input:visible, "
                                "input[name='phoneNumber']:visible, input[id$='phoneNumber' i]:visible, "
                                "input[type=tel]:visible").first
        try:
            if digits and box.count() and box.input_value() != digits:
                box.fill(digits)
        except Exception:
            pass

    def fill_dates(self):
        """Workday dates are three boxes (MM / DD / YYYY). Start-date questions get today + notice period,
        'today' / signature dates get today, date of birth comes from the profile; any other date question
        is left for config.yaml."""
        pg = self.page
        wrappers = pg.locator(f"{A('dateInputWrapper')}:visible")
        for i in range(wrappers.count()):
            w = wrappers.nth(i)
            try:
                question = w.evaluate(
                    "e => { const f = e.closest('fieldset, [data-automation-id^=\"formField\"]');"
                    " const l = f && f.querySelector('legend, label'); return l ? l.innerText : ''; }")
                if re.search(r"start|join|available|availability|begin", question, re.I):
                    when = datetime.date.today() + datetime.timedelta(days=int(self.prof.p.get("notice_period_days", 30)))
                elif re.search(r"today|signature|signed|current date|^\s*date\s*\*?\s*$", question, re.I):
                    when = datetime.date.today()
                elif re.search(r"birth|\bdob\b|geburt", question, re.I) and self.prof.p.get("date_of_birth"):
                    when = datetime.date.fromisoformat(str(self.prof.p["date_of_birth"]))
                else:
                    continue
                month = w.locator(f"{A('dateSectionMonth-input')}, input[aria-label='Month']").first
                day = w.locator(f"{A('dateSectionDay-input')}, input[aria-label='Day']").first
                # German / European sites show DD.MM.YYYY - start in whichever box comes first
                day_first = day.count() and day.evaluate(
                    "(d, m) => !!(d.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING)", month.element_handle())
                first, digits = (day, "%d%m%Y") if day_first else (month, "%m%d%Y")
                # a display layer sits on top of the date boxes - click through it
                w.scroll_into_view_if_needed(timeout=5000)
                try:
                    first.click(force=True, timeout=5000)
                except Exception:
                    first.focus()
                pg.keyboard.type(when.strftime(digits), delay=80)
                pg.keyboard.press("Tab")
                shown = w.inner_text().replace("\n", "")
                self.log(f"date '{question.strip()[:40]}' -> {when:%d %b %Y} (form shows {shown[:20]!r})")
            except Exception as e:
                self.log(f"could not fill a date field: {str(e)[:120]}")
                continue

    def fill_work_history(self):
        """Resume autofill often leaves Company empty - fill it from the matching job title in config.yaml."""
        jobs = self.prof.p.get("experience") or []
        companies = self.page.locator("input[id*='workExperience'][id$='companyName']:visible")
        for i in range(companies.count()):
            box = companies.nth(i)
            try:
                if box.input_value().strip():
                    continue
                title_id = (box.get_attribute("id") or "").replace("companyName", "jobTitle")
                title_box = self.page.locator(f"[id='{title_id}']")
                title = title_box.input_value().lower() if title_box.count() else ""
                match = next((j for j in jobs if j["title"].lower() in title or title and title in j["title"].lower()), None)
                if match:
                    box.fill(match["company"])
            except Exception:
                continue

    def upload_resume(self):
        pg = self.page
        file_input = pg.locator(f"{A('file-upload-input-ref')}, input[type=file]").first
        if not file_input.count() or not self.prof.resume.exists():
            return
        if self.visible(f"{A('file-upload-successful')}, {A('file-upload-item')}"):
            return  # already attached
        file_input.set_input_files(str(self.prof.resume))
        self.log("uploaded resume")
        pg.wait_for_timeout(4000)

    def fill_dropdowns(self, again=True):
        pg = self.page
        buttons = pg.locator("button[aria-haspopup='listbox']:visible")
        try:
            info = buttons.evaluate_all(FIELD_QUESTIONS_JS)
        except Exception:
            return
        country_changed = False
        for i, f in enumerate(info):
            wanted = self.prof.answers(f["question"], self.job)
            answered = f["text"] and not UNANSWERED_DROPDOWN.match(f["text"])
            if answered and not self.wrong_country(f["question"], f["text"], wanted):
                continue
            if not wanted:
                continue
            try:
                buttons.nth(i).click()
                pg.wait_for_timeout(800)
                options = pg.locator("[role='listbox'] [role='option']:visible")
                labels = [t.strip() for t in options.all_inner_texts()]
                choice = _matching_option(labels, wanted)
                if choice is None:  # long lists (countries) only render a part - type to jump to it
                    pg.keyboard.type(wanted[0][:5], delay=60)
                    pg.wait_for_timeout(800)
                    labels = [t.strip() for t in options.all_inner_texts()]
                    choice = _matching_option(labels, wanted)
                search = pg.locator("[role='listbox'] input:visible, [data-automation-id='searchBox'] input:visible, "
                                    "input[role='combobox'][aria-expanded='true']:visible").first
                if choice is None and search.count():  # some lists have their own search box - search for it
                    search.fill(wanted[0])
                    pg.wait_for_timeout(1200)
                    labels = [t.strip() for t in options.all_inner_texts()]
                    choice = _matching_option(labels, wanted)
                if choice is None and self.arrow_to(wanted):  # walk the list with the keyboard
                    country_changed = country_changed or bool(re.search(r"country", f["question"], re.I))
                    pause(0.4, 0.8)
                    continue
                listbox = pg.locator("[role='listbox']:visible").last
                for _ in range(30):  # typing doesn't jump in every list (States) - scroll down to the answer
                    if choice is not None or not listbox.count():
                        break
                    # the scrolling box can be the list itself, something inside it or a wrapper around it
                    moved = listbox.evaluate("el => { const up = []; for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) up.push(p);"
                                             " const s = [el, ...el.querySelectorAll('*'), ...up]"
                                             ".find(e => e.scrollHeight > e.clientHeight + 2 && getComputedStyle(e).overflowY !== 'visible') || el;"
                                             " const top = s.scrollTop; s.scrollTop += s.clientHeight * 0.8;"
                                             " return s.scrollTop !== top; }")
                    pg.wait_for_timeout(300)
                    labels = [t.strip() for t in options.all_inner_texts()]
                    choice = _matching_option(labels, wanted)
                    if not moved:
                        break
                if choice is not None:  # never fall back to the first option (that picked "Iceland" for India)
                    options.nth(choice).click()
                    country_changed = country_changed or bool(re.search(r"country", f["question"], re.I))
                else:
                    self.log(f"no option matching {wanted[:2]} for '{f['question'][:40]}' - list shows "
                             f"{len(labels)} options: {labels[:8]}")
                    pg.keyboard.press("Escape")
                pause(0.4, 0.8)
            except Exception:
                pg.keyboard.press("Escape")
        if country_changed and again:
            # a new country reloads the State / Region list (and can redraw the form) - go over it once more
            pg.wait_for_timeout(2000)
            self.fill_dropdowns(again=False)

    def fill_multiselects(self):
        """Search-and-pick fields such as "How did you hear about us?"."""
        pg = self.page
        inputs = pg.locator("input[data-uxi-widget-type='selectinput']:visible")
        try:
            info = inputs.evaluate_all(FIELD_QUESTIONS_JS)
        except Exception:
            return
        for i, f in enumerate(info):
            wanted = self.prof.answers(f["question"], self.job)
            if f["selected"] and not self.wrong_country(f["question"], f["selectedText"], wanted):
                continue
            if not wanted:
                continue
            for attempt in wanted:
                try:
                    box = inputs.nth(i)
                    box.fill(attempt)
                    box.press("Enter")
                    pg.wait_for_timeout(1500)
                    options = pg.locator(f"{A('promptOption')}:visible, [role='option']:visible")
                    if not options.count():
                        continue
                    labels = [t.strip() for t in options.all_inner_texts()]
                    choice = _matching_option(labels, wanted)
                    if choice is None:  # no real match for this search - try the next wording
                        pg.keyboard.press("Escape")
                        continue
                    options.nth(choice).click()
                    pg.wait_for_timeout(1000)
                    sub = pg.locator(f"{A('promptOption')}:visible")  # some lists open a second level
                    if sub.count():
                        sub.first.click()
                    pg.keyboard.press("Escape")
                    break
                except Exception:
                    pg.keyboard.press("Escape")

    def arrow_to(self, wanted, steps=80):
        """Press Down through an open list until the highlighted option matches, then Enter.
        Works when the list only draws the visible part (Workday State lists) because the
        highlight - not the mouse - moves through it."""
        pg = self.page
        seen = None
        for _ in range(steps):
            pg.keyboard.press("ArrowDown")
            pg.wait_for_timeout(60)
            current = pg.evaluate("""() => {
                const lb = [...document.querySelectorAll('[role=listbox]')].filter(e => e.offsetParent).pop();
                const id = (lb && lb.getAttribute('aria-activedescendant'))
                        || (document.activeElement && document.activeElement.getAttribute('aria-activedescendant'));
                const el = (id && document.getElementById(id))
                        || (lb && lb.querySelector('[role=option][aria-selected=true], [role=option]:focus, [role=option][data-focused]'))
                        || (document.activeElement && document.activeElement.closest('[role=option]'));
                return el ? el.innerText.trim() : '';
            }""")
            if current and _matching_option([current], wanted) is not None:
                pg.keyboard.press("Enter")
                pg.wait_for_timeout(500)
                return True
            if current and current == seen:
                break  # reached the end of the list
            seen = current
        return False

    @staticmethod
    def wrong_country(question, current, wanted):
        """A country / phone-code field already holding another country (resume autofill or an earlier
        bad pick) - phone validation then fails, so it gets picked again."""
        return bool(wanted and current and re.search(r"country|phone code", question, re.I)
                    and _matching_option([current], wanted) is None)

    def tick_agreements(self):
        boxes = self.page.locator("input[type=checkbox]:visible")
        for i in range(boxes.count()):
            box = boxes.nth(i)
            try:
                label = box.evaluate(
                    "el => (el.labels && el.labels[0] ? el.labels[0].innerText : '') || "
                    "(el.closest('[data-automation-id^=\"formField\"]') || el.parentElement).innerText")
                if AGREE.search(label or "") and not box.is_checked():
                    box.check(force=True)
            except Exception:
                continue
