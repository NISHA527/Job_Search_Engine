import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BRAVE_CANDIDATES = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
]


def find_brave(cfg):
    configured = (cfg.get("browser") or {}).get("brave_path")
    for path in [configured, *BRAVE_CANDIDATES]:
        if path and Path(path).exists():
            return path
    raise SystemExit("Brave browser not found. Set browser.brave_path in config.yaml")


def launch_brave(cfg, root):
    """Open Brave with a dedicated profile so logins survive between runs.

    The profile is separate from your everyday Brave, so both can be open at once.
    """
    profile = root / (cfg.get("browser") or {}).get("profile_dir", "brave_profile")
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(profile),
        executable_path=find_brave(cfg),
        headless=False,
        no_viewport=True,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    ctx.set_default_timeout(15000)
    return pw, ctx


def open_tabs(ctx, count):
    """Return `count` tabs (one per portal), reusing open ones and closing any extra tabs Brave restored."""
    while len(ctx.pages) < count:
        ctx.new_page()
    for extra in ctx.pages[count:]:
        extra.close()
    return ctx.pages[:count]
