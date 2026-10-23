"""Applying on a company website (the plain "Apply" buttons that leave LinkedIn / Naukri)."""
from urllib.parse import parse_qs, urlparse

from .simpleform import SimpleFormApplier
from .workday import WorkdayApplier


def open_in_same_tab(bot, button):
    """Click an Apply button that opens the company site in a new tab, and load that site in our one tab instead.

    Returns the company site URL, or None if nothing opened.
    """
    pg = bot.page
    before = pg.url
    href = button.get_attribute("href") or ""
    if href.startswith("http"):
        # LinkedIn wraps the company URL in linkedin.com/safety/go/?url=... - open it straight in our tab
        url = parse_qs(urlparse(href).query).get("url", [href])[0]
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)  # let redirects finish
        return pg.url
    try:
        with bot.ctx.expect_page(timeout=15000) as info:
            button.click()
            pg.wait_for_timeout(1500)
            bot.click_button(pg, r"^continue$")  # LinkedIn's "share your profile with the company?" prompt
        popup = info.value
    except Exception:
        return pg.url if pg.url != before else None  # some sites navigate the same tab instead
    try:
        popup.wait_for_load_state("domcontentloaded")
        popup.wait_for_timeout(4000)  # let redirects finish
        url = popup.url
    finally:
        popup.close()
    if not url or url == "about:blank":
        return None
    pg.goto(url, wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    return pg.url


def apply_external(bot, button, job):
    """Apply on the company website. Returns a tracker status."""
    url = open_in_same_tab(bot, button)
    if not url:
        return "failed"
    bot.accept_cookies()
    if WorkdayApplier.handles(url):
        bot.log(f"Workday application: {job.get('title')} @ {job.get('company')}")
        try:
            return WorkdayApplier(bot, job).apply()
        except Exception as e:
            bot.log(f"Workday error: {e}")
            return "failed"
    bot.log(f"company site application ({urlparse(url).netloc}): {job.get('title')} @ {job.get('company')}")
    try:
        return SimpleFormApplier(bot, job).apply()
    except Exception as e:
        bot.log(f"company site error: {e}")
        return "failed"
