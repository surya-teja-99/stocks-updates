#!/usr/bin/env python3
"""Seed the SQLite database from data/stocks.json and data/watchlist.json.

Usage:
    python backend/seed.py

Idempotent: safe to re-run. Existing daily updates are never touched,
existing watchlist flags are preserved, and canonical names/sectors from
data/watchlist.json are merged into the directory.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import DB_PATH, SCHEMA_SQL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL)
        with open(os.path.join(ROOT, "data", "stocks.json"), encoding="utf-8") as fh:
            stocks = json.load(fh)
        with open(os.path.join(ROOT, "data", "watchlist.json"), encoding="utf-8") as fh:
            watchlist = {w["ticker"]: w for w in json.load(fh)}

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for s in stocks:
            w = watchlist.get(s["ticker"])
            rows.append((
                s["ticker"],
                (w.get("company_name") if w else None) or s["company_name"],
                (w.get("sector") if w else None) or s.get("sector", ""),
                (w.get("exchange") if w else None) or s.get("exchange", ""),
                1 if w else 0,
                now,
            ))
        conn.executemany(
            """INSERT INTO stocks (ticker, company_name, sector, exchange, on_watchlist, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   company_name = excluded.company_name,
                   sector = excluded.sector,
                   exchange = excluded.exchange,
                   on_watchlist = stocks.on_watchlist OR excluded.on_watchlist""",
            rows,
        )
        # Watchlist tickers missing from stocks.json (shouldn't happen, but be safe).
        for ticker, w in watchlist.items():
            conn.execute(
                """INSERT INTO stocks (ticker, company_name, sector, exchange, on_watchlist, created_at)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(ticker) DO UPDATE SET on_watchlist = 1""",
                (ticker, w["company_name"], w.get("sector", ""), w.get("exchange", ""), now),
            )
        conn.commit()

        n_stocks = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        n_wl = conn.execute("SELECT COUNT(*) FROM stocks WHERE on_watchlist = 1").fetchone()[0]
        n_upd = conn.execute("SELECT COUNT(*) FROM daily_updates").fetchone()[0]
        print("stocks: %d, on watchlist: %d, daily updates: %d -> %s" % (n_stocks, n_wl, n_upd, DB_PATH))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
