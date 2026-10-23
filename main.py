"""Job Search Engine - logs in to job portals in Brave and applies to matching jobs.

    python main.py                      # all portals enabled in config.yaml
    python main.py --sites naukri       # just one portal
    python main.py --dry-run            # search and list jobs, apply to nothing
"""
import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bot.browser import launch_brave, open_tabs
from bot.common import Profile
from bot.foundit import FounditBot
from bot.indeed import IndeedBot
from bot.linkedin import LinkedInBot
from bot.naukri import NaukriBot
from bot.tracker import Tracker

ROOT = Path(__file__).resolve().parent
BOTS = {"linkedin": LinkedInBot, "naukri": NaukriBot, "foundit": FounditBot, "indeed": IndeedBot}


def main():
    # job pages contain symbols (↵, ₹, emoji) the Windows console can't show - print them as "?" instead of crashing
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
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
    tabs = open_tabs(ctx, len(sites))
    results = {}
    try:
        # 1) log in to every portal first - each portal gets its own tab
        print("\n===== LOGIN =====")
        ready = []
        for site, page in zip(sites, tabs):
            bot = BOTS[site](ctx, page, cfg, prof, tracker, args.dry_run)
            page.bring_to_front()
            try:
                if bot.login():
                    print(f"  [{site}] logged in")
                    ready.append(bot)
                else:
                    print(f"  [{site}] not logged in - skipping")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"  [{site}] login failed: {e}")

        # 2) then apply - the tabs take turns, one job each
        print("\n===== APPLYING =====")
        running = {bot: bot.steps() for bot in ready}
        while running:
            for bot, steps in list(running.items()):
                bot.page.bring_to_front()
                try:
                    next(steps)
                except StopIteration:
                    print(f"  [{bot.name}] finished")
                    del running[bot]
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"  [{bot.name}] stopped early: {e}")
                    del running[bot]
                results[bot.name] = bot.applied
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
