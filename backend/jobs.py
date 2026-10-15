"""Durable jobs stored with watches; a single in-process scheduler runs them.

Deploy exactly ONE uvicorn worker and ONE Railway replica. Uses SQLite volume.
"""
from __future__ import annotations
import logging
import sqlite3
from datetime import timedelta
from threading import Lock
from database import connect, utc_now, add_watch, get_watch, due_watches, store_scan
from engine import PlotQuery
from crawler import scan
import os

log = logging.getLogger(__name__)
process_lock = Lock()


def init_jobs():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             watch_id INTEGER NOT NULL REFERENCES watches(id),
             status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
             queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
             report TEXT NOT NULL DEFAULT ''
        )""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active_per_watch
          ON jobs(watch_id) WHERE status IN ('queued','running')""")
        # Resume interrupted processing on restart instead of leaving jobs indefinitely running.
        db.execute("UPDATE jobs SET status='queued', started_at=NULL, report='Resumed after restart' WHERE status='running'")


def queue_watch(watch_id: int) -> int:
    if not get_watch(watch_id):
        raise KeyError('Plot watch not found')
    with connect() as db:
        active = db.execute("SELECT id FROM jobs WHERE watch_id=? AND status IN ('queued','running')", (watch_id,)).fetchone()
        if active:
            return int(active['id'])
        db.execute("INSERT INTO jobs (watch_id,status,queued_at) VALUES (?,?,?)",
                   (watch_id, 'queued', utc_now().isoformat()))
        return int(db.execute('SELECT last_insert_rowid()').fetchone()[0])


def read_job(job_id: int):
    with connect() as db:
        r = db.execute("SELECT id,watch_id,status,queued_at,started_at,finished_at,report FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None


def _execute(watch: dict):
    q = PlotQuery(watch['plot_number'], watch['city'], watch['society'], watch['block'])
    try:
        output = scan(q, mode='hybrid', seed_text='',
                      api_key=os.getenv('SERPER_API_KEY','').strip(),
                      max_pages=min(max(int(watch.get('max_pages', 8)), 1), 20), domains=())
        summary = output.stats.report()
        if output.stats.fetched == 0:
            store_scan(watch['id'], error=summary, note=summary,
                       discovered_urls=output.stats.discovered_urls)
            return 'failed', 'No website HTML verified. ' + summary
        store_scan(watch['id'], output.results, note=summary,
                   complete=output.stats.complete, fetched=output.stats.fetched,
                   blocked=output.stats.blocked,
                   discovered_urls=output.stats.discovered_urls)
        return 'completed', f'{len(output.results)} matching listing(s). {summary}'
    except Exception as ex:
        message = str(ex)[:800]
        store_scan(watch['id'], error=message, note='Scan failed')
        return 'failed', message


def process_next_job():
    if not process_lock.acquire(blocking=False):
        return
    try:
        # Create an explicit queue item for watches due after 15 days.
        for watch in due_watches()[:25]:
            try:
                queue_watch(watch['id'])
            except (KeyError, sqlite3.IntegrityError):
                continue
        with connect() as db:
            row = db.execute("SELECT id, watch_id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return
            job_id, wid = int(row['id']), int(row['watch_id'])
            db.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (utc_now().isoformat(), job_id))
        watch = get_watch(wid)
        if watch is None:
            status, report = 'failed', 'Plot watch was deleted'
        else:
            status, report = _execute(watch)
        with connect() as db:
            db.execute("UPDATE jobs SET status=?, finished_at=?, report=? WHERE id=?",
                       (status, utc_now().isoformat(), report[:1500], job_id))
        log.info('Job %s: %s, %s', job_id, status, report[:100])
    except Exception:
        log.exception('Unexpected worker failure')
    finally:
        process_lock.release()
