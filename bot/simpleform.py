"""Single-page application forms on company sites (Greenhouse, Lever, Ashby and most custom career pages).

Safety rule: the form is only submitted when every required field has a value. Otherwise the job is skipped
and the open questions are written to data/unanswered_questions.txt so they can be added to config.yaml.
"""
import re

from .common import ACKNOWLEDGE, OPT_IN, fill_form, pause, tick

SUCCESS = re.compile(r"thank(s| you)[^.]{0,20} (for )?(applying|your (application|interest))"
                     r"|application (has been |was )?(received|submitted|sent)|we('ve| have) received your application"
                     r"|successfully (applied|submitted)", re.I)
APPLY = re.compile(r"^\s*(apply( now| here| online| for this (job|position|role))?|i'?m interested|start application)\s*$", re.I)
SUBMIT = re.compile(r"^\s*(submit( application| your application)?|send( application)?|apply( now)?|finish|complete application)\s*$", re.I)
# recognised by its email box too - after a resume upload some sites (Greenhouse) remove the file input
EMAIL_CODE = re.compile(r"security code|verification code|one[- ]time (code|password)|\botp\b|code (was )?sent to", re.I)
NOT_AN_APPLICATION = re.compile(r"job alerts?|get notified|notify me|similar jobs|newsletter|subscribe|talent community sign", re.I)
FORM = ("form:has(input[type=file]), form:has(input[type=email]), form:has(input[name*='email' i]), "
        "form:has(input[id*='email' i]), form:has(input[autocomplete=email])")

REQUIRED_EMPTY_JS = r"""(root) => {
  const out = [];
  for (const el of root.querySelectorAll('input, select, textarea')) {
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (!required || el.disabled || el.type === 'hidden') continue;
    // dropdown widgets keep an invisible required input that fills itself when an option is picked
    if (el.tabIndex === -1 && !el.id && getComputedStyle(el).opacity === '0') continue;
    const shown = el.offsetWidth || el.offsetHeight || ['file', 'radio', 'checkbox'].includes(el.type);
    if (!shown) continue;
    let empty;
    if (el.type === 'radio') empty = !root.querySelector(`input[type=radio][name="${CSS.escape(el.name)}"]:checked`);
    else if (el.type === 'checkbox') empty = !el.checked;
    else if (el.type === 'file') empty = !(el.files && el.files.length);
    else if (el.getAttribute('role') === 'combobox') {
      // a picked option is shown as a label next to the (now empty) search box
      const box = el.closest('[class*="control"]') || el.closest('[class*="select"]:not([class*="input"])')
        || el.parentElement.parentElement;
      const shown = box && box.querySelector('[class*="single-value"], [class*="singleValue"], [class*="multi-value"], [class*="multiValue"]');
      const hidden = box && [...box.querySelectorAll('input')].find(i => i !== el && i.value);
      empty = !String(el.value || '').trim() && !shown && !hidden;
    }
    else empty = !String(el.value || '').trim();
    if (!empty) continue;
    const fs = el.closest('fieldset');
    const label = (el.labels && el.labels[0] && el.labels[0].innerText) || (fs && fs.querySelector('legend') && fs.querySelector('legend').innerText)
      || el.getAttribute('aria-label') || el.placeholder || el.name || el.id || el.type;
    out.push(label.trim().replace(/\s+/g, ' ').replace(/\*$/, '').trim().slice(0, 120));
  }
  return [...new Set(out)];
}"""


class SimpleFormApplier:
    def __init__(self, bot, job):
        self.bot, self.page, self.prof, self.job = bot, bot.page, bot.prof, job

    def log(self, msg):
        self.bot.log(f"company site: {msg}")

    def form(self):
        # a "get job alerts" / newsletter box is also a form with an email field (Virtusa) - never the application
        forms = self.page.locator(FORM).filter(has=self.page.locator("input:visible, textarea:visible"))
        for i in range(forms.count()):
            form = forms.nth(i)
            try:  # its heading often sits just outside the <form> - read the box around it too
                around = form.evaluate("f => { let b = f; for (let i = 0; i < 3 && b.parentElement; i++) {"
                                       " if (b.parentElement.querySelectorAll('input').length > 4) break;"
                                       " b = b.parentElement; } return b.innerText.slice(0, 600); }")
            except Exception:
                around = ""
            if not NOT_AN_APPLICATION.search(around):
                return form
        return None

    def open_form(self):
        """Click through 'Apply' buttons until an application form shows up (at most 3 clicks)."""
        pg = self.page
        self.accept_cookies()
        for _ in range(3):
            if self.form():
                return True
            target = None
            for role in ("button", "link"):
                loc = pg.get_by_role(role, name=APPLY)
                for i in range(loc.count()):
                    if loc.nth(i).is_visible():
                        target = loc.nth(i)
                        break
                if target:
                    break
            if not target:
                return False
            before = len(self.bot.ctx.pages)
            try:
                target.click(timeout=5000)
            except Exception:
                target.click(force=True, timeout=5000)  # something (banner, overlay) covers the button
            pg.wait_for_timeout(4000)
            if len(self.bot.ctx.pages) > before:  # opened in a new tab - bring it into our tab
                new = self.bot.ctx.pages[-1]
                url = new.url
                new.close()
                pg.goto(url, wait_until="domcontentloaded")
                pg.wait_for_timeout(3000)
        return bool(self.form())

    def accept_cookies(self):
        return self.bot.accept_cookies()

    def apply(self):
        pg = self.page
        self.bot.check_human_verification()
        if not self.open_form():
            if pg.locator("input[type=password]:visible").count():
                self.log("this site needs an account - skipped")
            return "external_site"
        if pg.locator("input[type=password]:visible").count():
            self.log("this site needs an account - skipped")
            return "external_site"
        self.accept_cookies()  # a consent popup (Ascendion) can cover the whole form
        form = self.form()
        self.upload_resume(form)
        self.accept_cookies()
        fill_form(pg, form, self.prof, self.job, skip_files=True)
        pg.wait_for_timeout(2500)
        self.accept_cookies()
        # some sites redraw the form after reading the resume and wipe earlier entries - fill again
        form = self.form() or form
        fill_form(pg, form, self.prof, self.job, skip_files=True)
        self.tick_acknowledgements(form)
        pause(1, 2)
        missing = form.evaluate(REQUIRED_EMPTY_JS)
        if missing:
            self.log(f"not submitted - no answer for: {missing}")
            self.bot.note_questions(missing, self.job)
            self.bot.save_debug(form, self.job)
            # she can fill the gaps and press Submit herself - then the checks below confirm it
            if not self.bot.ask_user(missing, self.job, lambda: not self.form() or not form.evaluate(REQUIRED_EMPTY_JS)):
                return "skipped"
        self.accept_cookies()  # a late banner can cover the Submit button
        if not self.submit(form):
            self.log("no Submit button found")
            return "skipped"
        pg.wait_for_timeout(6000)
        if self.bot.agree_to_popup():  # some sites ask for consent only after Submit
            pg.wait_for_timeout(6000)
        self.bot.check_human_verification()
        body = pg.locator("body").inner_text()
        confirmed_page = not self.form() and re.search(r"thank|confirm|success|submitted|received", pg.url + " " + body[:3000], re.I)
        if SUCCESS.search(body) or confirmed_page:
            self.log("application submitted (the site confirmed it)")
            return "applied"
        if not self.form():
            self.log(f"form closed after Submit but no confirmation shown - check {pg.url}")
            return "unconfirmed"
        missing = form.evaluate(REQUIRED_EMPTY_JS) if self.form() else []
        if missing and EMAIL_CODE.search(" ".join(missing)):
            # Greenhouse (Celonis) emails a security code before it takes the application - only she can read it
            self.log("the site emailed you a security code - type it into Brave and click Submit")
            if self.bot.ask_user(["Security code from your email"], self.job, lambda: not self.form()):
                pg.wait_for_timeout(4000)
                body = pg.locator("body").inner_text()
                if SUCCESS.search(body) or re.search(r"thank|confirm|success|submitted|received", pg.url + " " + body[:3000], re.I):
                    self.log("application submitted (the site confirmed it)")
                    return "applied"
                return "unconfirmed"
            return "skipped"
        if missing:
            self.bot.note_questions(missing, self.job)
        if missing:  # the site refused the form - nothing was sent, so it can be retried
            self.log(f"the site did not accept the application - missing: {missing}")
            self.bot.save_debug(pg.locator("body"), self.job)
            return "skipped"
        # Submit was clicked and nothing is reported missing - it may well have gone through, so never resend it
        self.log(f"submitted, but the site did not confirm it - check {pg.url[:90]}")
        return "unconfirmed"

    def tick_acknowledgements(self, form):
        """Tick 'I have read the privacy notice' / 'I agree to the terms' boxes (as on the Workday account page).
        Opt-ins such as talent pools, marketing, SMS or future roles are left alone."""
        boxes = form.locator("input[type=checkbox]")
        for i in range(boxes.count()):
            box = boxes.nth(i)
            try:
                if box.is_checked():
                    continue
                label = box.evaluate("e => (e.labels && e.labels[0] ? e.labels[0].innerText : '')"
                                     " || (e.closest('label, fieldset, div') || e).innerText")
                if ACKNOWLEDGE.search(label or "") and not OPT_IN.search(label or ""):
                    tick(box)
            except Exception:
                continue

    def upload_resume(self, form):
        if not self.prof.resume.exists():
            return
        inputs = form.locator("input[type=file]")
        target = None
        for i in range(inputs.count()):
            meta = inputs.nth(i).evaluate(
                "e => [e.id, e.name, e.getAttribute('aria-label') || '', e.labels && e.labels[0] ? e.labels[0].innerText : '',"
                " (e.closest('div, fieldset') || e).innerText.slice(0, 200)].join(' ')")
            if re.search(r"cover|photo|picture", meta, re.I) and not re.search(r"resume|cv", meta, re.I):
                continue
            target = inputs.nth(i)
            if re.search(r"resume|cv", meta, re.I):
                break
        if target:
            target.set_input_files(str(self.prof.resume))
            self.log("uploaded resume")
            self.page.wait_for_timeout(4000)  # many sites read the resume and pre-fill fields

    def submit(self, form):
        for loc in (form.get_by_role("button", name=SUBMIT), form.locator("input[type=submit]"),
                    self.page.get_by_role("button", name=SUBMIT)):
            for i in range(loc.count()):
                b = loc.nth(i)
                if b.is_visible() and b.is_enabled():
                    b.click()
                    return True
        return False
