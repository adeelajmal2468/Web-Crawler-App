"""SQLite storage, including migration from the original PlotWatch MVP schema."""
from __future__ import annotations
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from engine import PlotQuery

ROOT = Path(os.environ.get("PLOTWATCH_DATA_DIR", str(Path(__file__).resolve().parent))).resolve()
INTERVAL_DAYS = 15


def utc_now():
    return datetime.now(timezone.utc)


def _ensure_column(conn, table: str, name: str, definition: str):
    names = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


@contextmanager
def connect(demo: bool = False):
    root = Path(os.environ.get("PLOTWATCH_DATA_DIR", str(ROOT))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / ("plotwatch_demo.db" if demo else "plotwatch.db")
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watches (
          id INTEGER PRIMARY KEY, plot_number TEXT NOT NULL, city TEXT NOT NULL,
          society TEXT NOT NULL, block TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, last_checked_at TEXT, next_check_at TEXT,
          last_status TEXT NOT NULL DEFAULT 'Not checked', last_error TEXT,
          UNIQUE(plot_number,city,society,block));
        CREATE TABLE IF NOT EXISTS findings (
          id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id),
          url TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL,
          snippet TEXT NOT NULL, confidence TEXT NOT NULL,
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
          seen_in_latest INTEGER NOT NULL DEFAULT 1, UNIQUE(watch_id,url));
        CREATE TABLE IF NOT EXISTS discovered_links (
          id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id),
          url TEXT NOT NULL, discovered_at TEXT NOT NULL, UNIQUE(watch_id,url));
        CREATE TABLE IF NOT EXISTS checks (
          id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id),
          checked_at TEXT NOT NULL, status TEXT NOT NULL,
          result_count INTEGER NOT NULL, error TEXT);
    """)
    _ensure_column(conn, "watches", "scan_mode", "TEXT NOT NULL DEFAULT 'hybrid'")
    _ensure_column(conn, "watches", "seed_urls", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "watches", "source_domains", "TEXT NOT NULL DEFAULT ''")
    # One-time v3 -> v4 migration. Don't repeatedly overwrite user-selected filters.
    schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if schema_version < 4:
        conn.execute("""UPDATE watches SET source_domains=''
            WHERE source_domains='zameen.com,graana.com,pakmls.com'""")
        conn.execute("PRAGMA user_version=4")
    _ensure_column(conn, "watches", "max_pages", "INTEGER NOT NULL DEFAULT 20")
    _ensure_column(conn, "watches", "last_scan_note", "TEXT")
    _ensure_column(conn, "checks", "pages_fetched", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "checks", "pages_blocked", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "checks", "scan_note", "TEXT")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_watch(query: PlotQuery, mode: str = "direct", seed_urls: str = "",
              domains: tuple[str, ...] = (),
              max_pages: int = 20, demo: bool = False) -> int:
    query.validate()
    if mode not in ("direct", "hybrid"):
        raise ValueError("Unknown crawl mode")
    with connect(demo) as conn:
        values = (query.plot_number.strip(), query.city.strip(), query.society.strip(),
                  query.block.strip())
        conn.execute("""INSERT OR IGNORE INTO watches
          (plot_number,city,society,block,created_at)
          VALUES (?,?,?,?,?)""", (*values, utc_now().isoformat()))
        row = conn.execute("""SELECT id FROM watches WHERE plot_number=? AND city=?
          AND society=? AND block=?""", values).fetchone()
        wid = int(row["id"])
        conn.execute("""UPDATE watches SET scan_mode=?,seed_urls=?,source_domains=?,max_pages=?
            WHERE id=?""", (mode, seed_urls.strip(), ",".join(domains), max_pages, wid))
        return wid


def store_scan(watch_id: int, results: list[dict] | None = None, error: str | None = None,
               note: str = "", complete: bool = True, fetched: int = 0,
               blocked: int = 0, demo: bool = False,
               discovered_urls: list[str] | None = None) -> None:
    now = utc_now()
    stamp = now.isoformat()
    results = results or []
    if error:
        status = "Crawl error"
    elif not complete:
        status = "Partial crawl - coverage incomplete"
    elif results:
        status = "Matching advertisement(s) found - unverified"
    else:
        status = "No match on checked pages (not proof of no sale)"
    next_stamp = (now + timedelta(days=1 if error or not complete else INTERVAL_DAYS)).isoformat()
    with connect(demo) as conn:
        if discovered_urls:
            # Preserve previously discovered URLs across unsuccessful rechecks.
            for url in dict.fromkeys(discovered_urls[:80]):
                conn.execute("""INSERT INTO discovered_links (watch_id,url,discovered_at)
                  VALUES (?,?,?) ON CONFLICT(watch_id,url) DO UPDATE SET
                  discovered_at=excluded.discovered_at""", (watch_id, url, stamp))
        if not error:
            if complete:
                conn.execute("UPDATE findings SET seen_in_latest=0 WHERE watch_id=?", (watch_id,))
            for item in results:
                url = item["url"] or f"demo://watch/{watch_id}"
                conn.execute("""INSERT INTO findings
                  (watch_id,url,title,source,snippet,confidence,first_seen_at,last_seen_at,seen_in_latest)
                  VALUES (?,?,?,?,?,?,?,?,1)
                  ON CONFLICT(watch_id,url) DO UPDATE SET
                    title=excluded.title,source=excluded.source,snippet=excluded.snippet,
                    confidence=excluded.confidence,last_seen_at=excluded.last_seen_at,
                    seen_in_latest=1""", (watch_id, url, item["title"], item["source"],
                                         item["snippet"], item["confidence"], stamp, stamp))
        conn.execute("""UPDATE watches SET last_checked_at=?, next_check_at=?,last_status=?,
          last_error=?,last_scan_note=? WHERE id=?""",
                     (stamp, next_stamp, status, error, note, watch_id))
        conn.execute("""INSERT INTO checks
          (watch_id,checked_at,status,result_count,error,pages_fetched,pages_blocked,scan_note)
          VALUES (?,?,?,?,?,?,?,?)""",
                     (watch_id, stamp, status, len(results), error, fetched, blocked, note))


def watches(demo: bool = False) -> list[dict]:
    with connect(demo) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM watches ORDER BY id DESC")]


def get_watch(watch_id: int, demo: bool = False) -> dict | None:
    with connect(demo) as conn:
        row = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
        return dict(row) if row else None


def findings(watch_id: int, demo: bool = False) -> list[dict]:
    with connect(demo) as conn:
        return [dict(row) for row in conn.execute("""SELECT * FROM findings WHERE
          watch_id=? AND seen_in_latest=1 ORDER BY last_seen_at DESC""", (watch_id,))]


def checks(demo: bool = False) -> list[dict]:
    with connect(demo) as conn:
        return [dict(row) for row in conn.execute("""SELECT c.*, w.plot_number,w.society,w.city
          FROM checks c JOIN watches w ON c.watch_id=w.id
          ORDER BY c.id DESC LIMIT 75""")]


def due_watches(demo: bool = False) -> list[dict]:
    with connect(demo) as conn:
        return [dict(row) for row in conn.execute("""SELECT * FROM watches WHERE enabled=1
          AND (next_check_at IS NULL OR next_check_at<=?) ORDER BY next_check_at LIMIT 100""",
                     (utc_now().isoformat(),))]


def set_enabled(watch_id: int, enabled: bool, demo: bool = False):
    with connect(demo) as conn:
        conn.execute("UPDATE watches SET enabled=? WHERE id=?", (int(enabled), watch_id))


def discovered_links(watch_id: int, demo: bool = False) -> list[dict]:
    """Search engine discoveries, NOT HTML-verified sale advertisements."""
    with connect(demo) as conn:
        return [dict(row) for row in conn.execute("""SELECT url,discovered_at FROM discovered_links
            WHERE watch_id=? ORDER BY discovered_at DESC,id DESC LIMIT 50""", (watch_id,))]
