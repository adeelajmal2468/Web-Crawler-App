"""Scheduled rechecks use saved open-web discovery settings; retry partial checks after 1 day."""
from __future__ import annotations
import argparse
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
from crawler import scan
from database import due_watches, store_scan
from engine import PlotQuery

load_dotenv(Path(__file__).resolve().parent / ".env")


def run_due():
    rows = due_watches()
    print(f"{datetime.now().isoformat(timespec='seconds')}  Due watches: {len(rows)}", flush=True)
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    for w in rows:
        query = PlotQuery(w["plot_number"], w["city"], w["society"], w["block"])
        try:
            output = scan(query, mode=w["scan_mode"], seed_text=w["seed_urls"],
                          domains=tuple(x for x in w["source_domains"].split(",") if x),
                          max_pages=w["max_pages"], api_key=api_key)
            if output.stats.fetched == 0:
                store_scan(w["id"], error=output.stats.report(), note=output.stats.report(),
                           discovered_urls=output.stats.discovered_urls)
                print(f"Plot {w['plot_number']}: no HTML pages verified", flush=True)
                continue
            store_scan(w["id"], output.results, note=output.stats.report(),
                       complete=output.stats.complete, fetched=output.stats.fetched,
                       blocked=output.stats.blocked,
                       discovered_urls=output.stats.discovered_urls)
            print(f"Plot {w['plot_number']}  Matches: {len(output.results)}  {output.stats.report()}", flush=True)
        except Exception as exc:
            store_scan(w["id"], error=str(exc)[:600], note="Recheck could not complete")
            print(f"Plot {w['plot_number']}: {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Check due watches once and exit (Task Scheduler)")
    args = parser.parse_args()
    if args.once:
        run_due()
        return
    tz = ZoneInfo("Asia/Karachi")
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(run_due, "interval", hours=1, next_run_time=datetime.now(tz),
                  id="recheck", max_instances=1, coalesce=True)
    print("Crawler monitor started; polls hourly, complete scans are due 15 days later.", flush=True)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("Monitoring stopped")


if __name__ == "__main__":
    main()
