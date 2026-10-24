import os
import re
import time

from .common import pause


COOKIE_BUTTON = re.compile(
    r"^\s*(accept( all| all cookies| cookies| and (continue|close))?|allow( all)?( cookies)?|i accept|"
    r"i agree|agree( and (continue|proceed|close))?|got it|ok(ay)?|understood|accept & continue)\s*$", re.I)
AGREE_BUTTON = re.compile(r"^\s*(i )?(agree|accept)( (and|&) (continue|proceed|apply))?\s*$", re.I)
POPUP = "[role=dialog]:visible, [role=alertdialog]:visible, dialog[open], [aria-modal=true]:visible"


def beep():
    """Two short, quiet tones (Windows) - never let a missing sound device stop an application."""
    try:
        import winsound
        winsound.Beep(880, 150)
        winsound.Beep(1175, 150)
    except Exception:
        pass


class SiteBot:
    name = ""
    env_prefix = ""

    def __init__(self, ctx, page, cfg, prof, tracker, dry_run=False):
        self.ctx = ctx
        self.cfg = cfg
        self.search = cfg["search"]
        self.prof = prof
        self.tracker = tracker
        self.dry_run = dry_run
        self.applied = 0
        self.max = int(self.search.get("max_applications_per_site", 15))
        self.page = page  # this portal's own tab

    # ---- helpers -------------------------------------------------------
    def creds(self):
        return (os.getenv(f"{self.env_prefix}_EMAIL", "").strip(),
                os.getenv(f"{self.env_prefix}_PASSWORD", "").strip())

    def log(self, msg):
        print(f"  [{self.name}] {msg}")

    def done(self):
        return self.applied >= self.max

    def wait_until(self, condition, message, timeout=300):
        """Poll `condition` while the user handles CAPTCHA / OTP / manual login in the browser."""
        self.log(message)
        end = time.time() + timeout
        next_beep = 0
        while time.time() < end:
            try:
                if condition():
                    return True
            except Exception:
                pass
            if time.time() >= next_beep:  # Nisha: a soft beep whenever she has to tick / accept / type something
                beep()
                next_beep = time.time() + 45
            self.page.wait_for_timeout(2000)
        self.log("timed out waiting.")
        return False

    def ask_user(self, questions, job, answered, timeout=120):
        """Keep an application open for a short while when the bot can't answer a question, so Nisha can
        answer it in Brave - but don't sit on one job for long: after 2 minutes it is skipped."""
        self.page.bring_to_front()
        if re.search(r"captcha|security code|verification code", " ".join(questions or []), re.I):
            timeout = max(timeout, 300)  # a CAPTCHA or emailed code is only for her to type - give her more time
        where = f"{(job or {}).get('title', '')} @ {(job or {}).get('company', '')}"
        return self.wait_until(answered, f"NEED YOUR ANSWER for {where}: {questions or 'see the form'} - "
                                         f"answer it in the Brave window and click Next/Submit "
                                         f"(waiting {timeout // 60} min)...", timeout)

    def check_human_verification(self):
        """If a CAPTCHA / bot check is showing, wait for the user to solve it."""
        def blocked():
            title = (self.page.title() or "").lower()
            return ("just a moment" in title or "security check" in title
                    # only a CAPTCHA you can see - Naukri and others keep an invisible reCAPTCHA on every page
                    or self.page.locator("iframe[src*='captcha']:visible, iframe[title*='challenge']:visible")
                    .evaluate_all("els => els.some(e => e.offsetWidth > 100 && e.offsetHeight > 100)"))
        if blocked():
            self.wait_until(lambda: not blocked(), "verification check shown - please solve it in Brave...")

    @staticmethod
    def text(scope, selector):
        loc = scope.locator(selector).first
        try:
            return loc.inner_text(timeout=2000).strip() if loc.count() else ""
        except Exception:
            return ""

    @staticmethod
    def click_button(scope, pattern):
        """Click the first visible button whose accessible name matches `pattern`. Returns True if clicked."""
        buttons = scope.get_by_role("button", name=re.compile(pattern, re.I))
        for i in range(buttons.count()):
            b = buttons.nth(i)
            try:
                if b.is_visible() and b.is_enabled():
                    b.click()
                    pause(1, 2)
                    return True
            except Exception:
                continue
        return False

    def note_questions(self, questions, job=None):
        """Remember questions the bot had no answer for, so they can be added to config.yaml."""
        path = self.tracker.path.parent / "unanswered_questions.txt"
        known = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
        where = f"{self.name}: {(job or {}).get('company', '')}"
        with open(path, "a", encoding="utf-8") as f:
            for q in questions:
                line = f"{q.strip()}    [{where}]"
                if q.strip() and not any(l.startswith(q.strip() + "    [") for l in known):
                    f.write(line + "\n")
                    known.add(line)

    def accept_cookies(self):
        """Accept a cookie banner if one is showing (it often covers the Apply / Next buttons)."""
        pg = self.page
        if self.agree_to_popup():
            return True
        known = ("#onetrust-accept-btn-handler, #CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll, "
                 "#CybotCookiebotDialogBodyButtonAccept, [data-automation-id='legalNoticeAcceptButton'], "
                 "button#accept-recommended-btn-handler, .cc-allow, .cookie-accept, #cookie-accept")
        try:
            btn = pg.locator(known).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=3000)
                pg.wait_for_timeout(800)
                return True
            named = pg.get_by_role("button", name=COOKIE_BUTTON)
            for i in range(min(named.count(), 4)):
                if named.nth(i).is_visible():
                    named.nth(i).click(timeout=3000)
                    pg.wait_for_timeout(800)
                    return True
        except Exception:
            pass
        return False

    def agree_to_popup(self):
        """Consent popups such as Ascendion's 'Data Privacy Agreement' keep 'I Agree' disabled until the text
        is scrolled to the end - scroll it down, then agree (Nisha: always agree)."""
        pg = self.page
        try:
            popups = pg.locator(POPUP)
            for i in range(popups.count()):
                popup = popups.nth(i)
                button = popup.get_by_role("button", name=AGREE_BUTTON)
                if not button.count() or not button.first.is_visible():
                    continue
                for _ in range(10):
                    popup.evaluate("el => [el, ...el.querySelectorAll('*')].forEach(e => {"
                                   " if (e.scrollHeight > e.clientHeight + 5) e.scrollTop = e.scrollHeight; })")
                    popup.hover()
                    pg.mouse.wheel(0, 3000)
                    pg.wait_for_timeout(600)
                    if button.first.is_enabled():
                        break
                button.first.click(timeout=5000)
                self.log("agreed to a consent popup")
                pg.wait_for_timeout(1500)
                return True
        except Exception:
            pass
        return False

    def save_debug(self, scope, job=None):
        """Save the HTML of a form the bot got stuck on (data/debug/), to fix the bot from it later."""
        try:
            folder = self.tracker.path.parent / "debug"
            folder.mkdir(exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9]+", "_", f"{self.name}_{(job or {}).get('company', '')}")[:60]
            (folder / f"{name}.html").write_text(scope.evaluate("e => e.outerHTML"), encoding="utf-8")
        except Exception:
            pass

    def wanted(self, title, company):
        must = self.search.get("require_title_words") or []
        if must and not any(re.search(r"\b" + re.escape(w) + r"\b", title or "", re.I) for w in must):
            return False
        for word in self.search.get("exclude_title_words", []):
            if re.search(r"\b" + re.escape(word) + r"\b", title or "", re.I):
                return False
        excluded = {c.lower() for c in self.search.get("exclude_companies", [])}
        return (company or "").lower() not in excluded

    def searches(self):
        for kw in self.search["keywords"]:
            for loc in self.search["locations"]:
                yield kw, loc

    def steps(self):
        """Apply one job per step, so portals in different tabs can take turns.

        Sites whose run() is a generator yield after every job; the others do all their work in one step.
        """
        work = self.run()
        if work is not None:
            yield from work

    # ---- implemented per site ----------------------------------------
    def login(self):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError
