"""Job Search Engine - logs in to job portals in Brave and applies to matching jobs.

    python main.py                      # all portals enabled in config.yaml
    python main.py --sites naukri       # just one portal
    python main.py --dry-run            # search and list jobs, apply to nothing
"""
import argparse
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bot.browser import launch_brave
from bot.common import Profile
from bot.foundit import FounditBot
from bot.indeed import IndeedBot
from bot.linkedin import LinkedInBot
from bot.naukri import NaukriBot
from bot.tracker import Tracker

ROOT = Path(__file__).resolve().parent
BOTS = {"linkedin": LinkedInBot, "naukri": NaukriBot, "foundit": FounditBot, "indeed": IndeedBot}


def main():
    ap = argparse.ArgumentParser(description="Auto-apply to jobs in Brave")
    ap.add_argument("--sites", nargs="+", choices=list(BOTS), help="portals to run (default: enabled in config)")
    ap.add_argument("--dry-run", action="store_true", help="search and list matching jobs without applying")
    ap.add_argument("--max", type=int, help="max applications per portal (overrides config)")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if args.max:
        cfg["search"]["max_applications_per_site"] = args.max
    sites = args.sites or [s for s, on in cfg["sites"].items() if on]

    prof = Profile(cfg, ROOT)
    prof.check()
    tracker = Tracker(ROOT / "data" / "applied_jobs.csv")
    pw, ctx = launch_brave(cfg, ROOT)
    results = {}
    try:
        for site in sites:
            print(f"\n===== {site.upper()} =====")
            bot = BOTS[site](ctx, cfg, prof, tracker, args.dry_run)
            try:
                if bot.login():
                    bot.run()
                else:
                    print(f"  [{site}] not logged in - skipping")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"  [{site}] stopped early: {e}")
            results[site] = bot.applied
            bot.close()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        print("\n===== SUMMARY =====")
        for site, n in results.items():
            print(f"  {site:<10} {n} applications")
        print(f"  Full log: {ROOT / 'data' / 'applied_jobs.csv'}")
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    main()
