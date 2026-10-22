import os
import re
import time

from .common import pause


class SiteBot:
    name = ""
    env_prefix = ""

    def __init__(self, ctx, cfg, prof, tracker, dry_run=False):
        self.ctx = ctx
        self.cfg = cfg
        self.search = cfg["search"]
        self.prof = prof
        self.tracker = tracker
        self.dry_run = dry_run
        self.applied = 0
        self.max = int(self.search.get("max_applications_per_site", 15))
        self.page = ctx.new_page()

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
        while time.time() < end:
            try:
                if condition():
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(2000)
        self.log("timed out waiting.")
        return False

    def check_human_verification(self):
        """If a CAPTCHA / bot check is showing, wait for the user to solve it."""
        def blocked():
            title = (self.page.title() or "").lower()
            return ("just a moment" in title or "security check" in title
                    or self.page.locator("iframe[src*='captcha'], iframe[title*='challenge']").count() > 0)
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

    def wanted(self, title, company):
        for word in self.search.get("exclude_title_words", []):
            if re.search(r"\b" + re.escape(word) + r"\b", title or "", re.I):
                return False
        excluded = {c.lower() for c in self.search.get("exclude_companies", [])}
        return (company or "").lower() not in excluded

    def searches(self):
        for kw in self.search["keywords"]:
            for loc in self.search["locations"]:
                yield kw, loc

    def close(self):
        try:
            self.page.close()
        except Exception:
            pass

    # ---- implemented per site ----------------------------------------
    def login(self):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError
