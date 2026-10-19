#!/usr/bin/env python3
"""
Stocks Updates backend — Flask + SQLite (stdlib sqlite3, no ORM).

Run:
    python backend/app.py        # serves the app at http://localhost:5000

Serves the static frontend from the project root and exposes the JSON API
under /api/. The database file (stocks.db) is created on first run; load
the seed data with `python backend/seed.py`.
"""

import csv
import io
import os
import re
import sqlite3
from datetime import datetime, timezone

from flask import Flask, Response, g, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "stocks.db")

RATINGS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CSV_COLUMNS = ["ticker", "date", "price", "change_percent", "rating", "notes"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stocks (
    ticker TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    sector TEXT NOT NULL DEFAULT '',
    exchange TEXT NOT NULL DEFAULT '',
    on_watchlist INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_ticker TEXT NOT NULL REFERENCES stocks(ticker),
    date TEXT NOT NULL,
    price REAL NOT NULL,
    change_percent REAL NOT NULL DEFAULT 0,
    rating TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (stock_ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_updates_ticker_date ON daily_updates (stock_ticker, date);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_ticker TEXT NOT NULL REFERENCES stocks(ticker),
    date TEXT NOT NULL,
    side TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker_date ON paper_trades (stock_ticker, date, id);
"""

# Auto-rating ("breakout") rule: strong single-day moves are bucketed into
# buy/sell ratings. |change| >= BREAKOUT_PCT suggests buy/sell,
# |change| >= STRONG_PCT suggests strong buy/strong sell, anything
# smaller suggests hold. The Log Update editor calls suggest_rating()
# through /api/suggest-rating and pre-fills the rating (still editable).
BREAKOUT_PCT = 2.0
STRONG_PCT = 5.0


def suggest_rating(change_percent):
    """Suggest a rating from the day's % move.

    Returns (rating, rule_text). Up more than BREAKOUT_PCT -> buy side,
    down more than BREAKOUT_PCT -> sell side, else hold.
    """
    try:
        chg = float(change_percent)
    except (TypeError, ValueError):
        return None, "change_percent must be a number"
    if chg >= STRONG_PCT:
        return "strong_buy", "up %.1f%% (≥ +%.0f%%)" % (chg, STRONG_PCT)
    if chg >= BREAKOUT_PCT:
        return "buy", "up %.1f%% (≥ +%.0f%%)" % (chg, BREAKOUT_PCT)
    if chg <= -STRONG_PCT:
        return "strong_sell", "down %.1f%% (≤ -%.0f%%)" % (abs(chg), STRONG_PCT)
    if chg <= -BREAKOUT_PCT:
        return "sell", "down %.1f%% (≤ -%.0f%%)" % (abs(chg), BREAKOUT_PCT)
    return "hold", "move %.1f%% within ±%.0f%%" % (chg, BREAKOUT_PCT)


app = Flask(__name__, static_folder=ROOT, static_url_path="")


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path=DB_PATH):
    """Create tables if they don't exist."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_update(r):
    return {
        "id": r["id"],
        "ticker": r["stock_ticker"],
        "date": r["date"],
        "price": r["price"],
        "change_percent": r["change_percent"],
        "rating": r["rating"],
        "notes": r["notes"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def validate_update_payload(data):
    """Validate a daily-update payload. Returns (clean_dict, error_message)."""
    ticker = str(data.get("ticker") or "").upper().strip()
    date = str(data.get("date") or "").strip()
    rating = str(data.get("rating") or "").strip()
    if not ticker:
        return None, "ticker is required"
    if not DATE_RE.match(date):
        return None, "date must be YYYY-MM-DD"
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError):
        return None, "price must be a number"
    if price < 0:
        return None, "price must be non-negative"
    raw_chg = data.get("change_percent", 0)
    try:
        change_percent = float(raw_chg) if str(raw_chg).strip() != "" else 0.0
    except (TypeError, ValueError):
        return None, "change_percent must be a number"
    if rating not in RATINGS:
        return None, "rating must be one of: " + ", ".join(RATINGS)
    return {
        "ticker": ticker,
        "date": date,
        "price": price,
        "change_percent": change_percent,
        "rating": rating,
        "notes": str(data.get("notes") or ""),
    }, None


def upsert_update(db, clean):
    """Insert or update the row for (ticker, date). Returns (update, was_updated)."""
    now = utcnow()
    existing = db.execute(
        "SELECT id FROM daily_updates WHERE stock_ticker = ? AND date = ?",
        (clean["ticker"], clean["date"]),
    ).fetchone()
    if existing:
        db.execute(
            """UPDATE daily_updates
               SET price = ?, change_percent = ?, rating = ?, notes = ?, updated_at = ?
               WHERE id = ?""",
            (clean["price"], clean["change_percent"], clean["rating"], clean["notes"], now, existing["id"]),
        )
        db.commit()
        row = db.execute("SELECT * FROM daily_updates WHERE id = ?", (existing["id"],)).fetchone()
        return row_to_update(row), True
    cur = db.execute(
        """INSERT INTO daily_updates
           (stock_ticker, date, price, change_percent, rating, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (clean["ticker"], clean["date"], clean["price"], clean["change_percent"],
         clean["rating"], clean["notes"], now, now),
    )
    db.commit()
    row = db.execute("SELECT * FROM daily_updates WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row_to_update(row), False


def ticker_exists(db, ticker):
    return db.execute("SELECT 1 FROM stocks WHERE ticker = ?", (ticker,)).fetchone() is not None


# ---------------------------------------------------------------- frontend
@app.route("/")
def index():
    return send_from_directory(ROOT, "index.html")


# ---------------------------------------------------------------- API
@app.route("/api/meta")
def api_meta():
    db = get_db()
    stocks = db.execute("SELECT COUNT(*) AS c FROM stocks").fetchone()["c"]
    watchlist = db.execute("SELECT COUNT(*) AS c FROM stocks WHERE on_watchlist = 1").fetchone()["c"]
    updates = db.execute("SELECT COUNT(*) AS c FROM daily_updates").fetchone()["c"]
    return jsonify({"stocks": stocks, "watchlist": watchlist, "updates": updates})


@app.route("/api/suggest-rating")
def api_suggest_rating():
    """Suggest a rating for a given day % move (auto-rating rule)."""
    raw = (request.args.get("change_percent") or "").strip()
    if raw == "":
        return jsonify({"error": "change_percent is required"}), 400
    rating, rule = suggest_rating(raw)
    if rating is None:
        return jsonify({"error": rule}), 400
    return jsonify({"rating": rating, "rule": rule,
                    "change_percent": float(raw)})


@app.route("/api/stocks")
def api_stocks():
    q = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    db = get_db()
    if not q:
        rows = db.execute(
            "SELECT ticker, company_name, sector, exchange, on_watchlist FROM stocks ORDER BY ticker LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        like = "%" + q + "%"
        rows = db.execute(
            """SELECT ticker, company_name, sector, exchange, on_watchlist FROM stocks
               WHERE ticker LIKE ? OR company_name LIKE ?
               ORDER BY CASE WHEN ticker LIKE ? THEN 0 WHEN ticker LIKE ? THEN 1 ELSE 2 END, ticker
               LIMIT ?""",
            (like, like, q + "%", like, limit),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watchlist")
def api_watchlist():
    db = get_db()
    rows = db.execute(
        """SELECT s.ticker, s.company_name, s.sector, s.exchange,
                  u.id AS uid, u.date, u.price, u.change_percent, u.rating, u.notes
           FROM stocks s
           LEFT JOIN daily_updates u ON u.stock_ticker = s.ticker
               AND u.date = (SELECT MAX(date) FROM daily_updates WHERE stock_ticker = s.ticker)
           WHERE s.on_watchlist = 1
           ORDER BY s.ticker"""
    ).fetchall()
    out = []
    for r in rows:
        item = {
            "ticker": r["ticker"],
            "company_name": r["company_name"],
            "sector": r["sector"],
            "exchange": r["exchange"],
            "latest": None,
        }
        if r["uid"] is not None:
            item["latest"] = {
                "id": r["uid"], "date": r["date"], "price": r["price"],
                "change_percent": r["change_percent"], "rating": r["rating"], "notes": r["notes"],
            }
        out.append(item)
    return jsonify(out)


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    data = request.get_json(force=True, silent=True) or {}
    ticker = str(data.get("ticker") or "").upper().strip()
    db = get_db()
    if not ticker_exists(db, ticker):
        return jsonify({"error": "unknown ticker: " + ticker}), 404
    db.execute("UPDATE stocks SET on_watchlist = 1 WHERE ticker = ?", (ticker,))
    db.commit()
    return jsonify({"ok": True, "ticker": ticker})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def api_watchlist_remove(ticker):
    db = get_db()
    db.execute("UPDATE stocks SET on_watchlist = 0 WHERE ticker = ?", (ticker.upper(),))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/stocks/<ticker>")
def api_stock_detail(ticker):
    ticker = ticker.upper()
    db = get_db()
    s = db.execute(
        "SELECT ticker, company_name, sector, exchange, on_watchlist FROM stocks WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if not s:
        return jsonify({"error": "unknown ticker: " + ticker}), 404
    updates = db.execute(
        "SELECT * FROM daily_updates WHERE stock_ticker = ? ORDER BY date DESC", (ticker,)
    ).fetchall()
    return jsonify({"stock": dict(s), "updates": [row_to_update(r) for r in updates]})


@app.route("/api/stocks/<ticker>/updates")
def api_stock_updates(ticker):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM daily_updates WHERE stock_ticker = ? ORDER BY date DESC", (ticker.upper(),)
    ).fetchall()
    return jsonify([row_to_update(r) for r in rows])


@app.route("/api/updates", methods=["POST"])
def api_update_create():
    data = request.get_json(force=True, silent=True) or {}
    clean, err = validate_update_payload(data)
    if err:
        return jsonify({"error": err}), 400
    db = get_db()
    if not ticker_exists(db, clean["ticker"]):
        return jsonify({"error": "unknown ticker: " + clean["ticker"]}), 400
    upd, was_updated = upsert_update(db, clean)
    return jsonify({"update": upd, "updated": was_updated})


@app.route("/api/updates/<int:uid>", methods=["PUT"])
def api_update_edit(uid):
    data = request.get_json(force=True, silent=True) or {}
    clean, err = validate_update_payload(data)
    if err:
        return jsonify({"error": err}), 400
    db = get_db()
    if not db.execute("SELECT id FROM daily_updates WHERE id = ?", (uid,)).fetchone():
        return jsonify({"error": "update not found"}), 404
    if not ticker_exists(db, clean["ticker"]):
        return jsonify({"error": "unknown ticker: " + clean["ticker"]}), 400
    clash = db.execute(
        "SELECT id FROM daily_updates WHERE stock_ticker = ? AND date = ? AND id <> ?",
        (clean["ticker"], clean["date"], uid),
    ).fetchone()
    now = utcnow()
    if clash:
        # fold into the clashing entry so (ticker, date) stays unique
        db.execute("DELETE FROM daily_updates WHERE id = ?", (uid,))
        db.execute(
            """UPDATE daily_updates
               SET price = ?, change_percent = ?, rating = ?, notes = ?, updated_at = ?
               WHERE id = ?""",
            (clean["price"], clean["change_percent"], clean["rating"], clean["notes"], now, clash["id"]),
        )
        db.commit()
        row = db.execute("SELECT * FROM daily_updates WHERE id = ?", (clash["id"],)).fetchone()
        return jsonify({"update": row_to_update(row), "folded": True})
    db.execute(
        """UPDATE daily_updates
           SET stock_ticker = ?, date = ?, price = ?, change_percent = ?, rating = ?, notes = ?, updated_at = ?
           WHERE id = ?""",
        (clean["ticker"], clean["date"], clean["price"], clean["change_percent"],
         clean["rating"], clean["notes"], now, uid),
    )
    db.commit()
    row = db.execute("SELECT * FROM daily_updates WHERE id = ?", (uid,)).fetchone()
    return jsonify({"update": row_to_update(row)})


@app.route("/api/updates/<int:uid>", methods=["DELETE"])
def api_update_delete(uid):
    db = get_db()
    db.execute("DELETE FROM daily_updates WHERE id = ?", (uid,))
    db.commit()
    return jsonify({"ok": True})


def updates_to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([r["stock_ticker"], r["date"], r["price"], r["change_percent"], r["rating"], r["notes"]])
    return buf.getvalue()


@app.route("/api/export")
def api_export():
    db = get_db()
    rows = db.execute("SELECT * FROM daily_updates ORDER BY stock_ticker, date").fetchall()
    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(
        updates_to_csv(rows),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=stocks-updates-" + stamp + ".csv"},
    )


@app.route("/api/template")
def api_template():
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    w.writerow(["AAPL", datetime.now().strftime("%Y-%m-%d"), "232.50", "1.25", "buy",
                "Example note: strong quarter, holding."])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=stocks-updates-template.csv"},
    )


@app.route("/api/import", methods=["POST"])
def api_import():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded (form field name 'file')"}), 400
    try:
        text = f.read().decode("utf-8-sig")
    except Exception:
        return jsonify({"error": "could not decode file as UTF-8"}), 400
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    start = 0
    if rows and [c.strip().lower() for c in rows[0]] == CSV_COLUMNS:
        start = 1
    db = get_db()
    imported = updated = skipped = 0
    errors = []
    for i in range(start, len(rows)):
        r = rows[i]
        data = {
            "ticker": r[0] if len(r) > 0 else "",
            "date": r[1] if len(r) > 1 else "",
            "price": r[2] if len(r) > 2 else "",
            "change_percent": r[3] if len(r) > 3 else "",
            "rating": r[4] if len(r) > 4 else "",
            "notes": r[5] if len(r) > 5 else "",
        }
        clean, err = validate_update_payload(data)
        if err:
            skipped += 1
            if len(errors) < 8:
                errors.append("Row %d: %s" % (i + 1, err))
            continue
        if not ticker_exists(db, clean["ticker"]):
            skipped += 1
            if len(errors) < 8:
                errors.append("Row %d: unknown ticker: %s" % (i + 1, clean["ticker"]))
            continue
        _, was_updated = upsert_update(db, clean)
        if was_updated:
            updated += 1
        else:
            imported += 1
    return jsonify({"imported": imported, "updated": updated, "skipped": skipped, "errors": errors})


# ---------------------------------------------------------- paper trading
def validate_trade_payload(data, db):
    """Validate a paper-trade payload. Returns (clean_dict, error_message)."""
    ticker = str(data.get("ticker") or "").upper().strip()
    date = str(data.get("date") or "").strip()
    side = str(data.get("side") or "").strip().lower()
    notes = str(data.get("notes") or "")
    if not ticker:
        return None, "ticker is required"
    if not ticker_exists(db, ticker):
        return None, "unknown ticker: " + ticker
    if not DATE_RE.match(date):
        return None, "date must be YYYY-MM-DD"
    if side not in ("buy", "sell"):
        return None, "side must be 'buy' or 'sell'"
    try:
        shares = float(data.get("shares"))
    except (TypeError, ValueError):
        return None, "shares must be a number"
    if shares <= 0:
        return None, "shares must be positive"
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError):
        return None, "price must be a number"
    if price < 0:
        return None, "price must be non-negative"
    return {
        "ticker": ticker,
        "date": date,
        "side": side,
        "shares": shares,
        "price": price,
        "notes": notes,
    }, None


def paper_shares(db, ticker):
    """Net paper shares currently held for a ticker."""
    row = db.execute(
        """SELECT COALESCE(SUM(CASE WHEN side = 'buy' THEN shares ELSE -shares END), 0) AS s
           FROM paper_trades WHERE stock_ticker = ?""",
        (ticker,)).fetchone()
    return row["s"] or 0.0


def paper_portfolio(db):
    """Average-cost positions from paper trades, valued at latest logged prices.

    Returns (positions, totals). Positions with no logged price yet get
    latest_price/market_value/unrealized_* = None.
    """
    trades = db.execute(
        "SELECT * FROM paper_trades ORDER BY date, id").fetchall()
    pos = {}
    for t in trades:
        p = pos.setdefault(t["stock_ticker"],
                           {"shares": 0.0, "cost": 0.0, "realized": 0.0})
        if t["side"] == "buy":
            p["shares"] += t["shares"]
            p["cost"] += t["shares"] * t["price"]
        else:
            avg = p["cost"] / p["shares"] if p["shares"] > 0 else 0.0
            p["realized"] += (t["price"] - avg) * t["shares"]
            p["shares"] -= t["shares"]
            p["cost"] -= avg * t["shares"]
    latest = {r["stock_ticker"]: r["price"] for r in db.execute(
        """SELECT stock_ticker, price FROM daily_updates u
           WHERE date = (SELECT MAX(date) FROM daily_updates
                         WHERE stock_ticker = u.stock_ticker)""").fetchall()}
    positions = []
    for ticker in sorted(pos):
        p = pos[ticker]
        avg_cost = p["cost"] / p["shares"] if p["shares"] > 1e-9 else 0.0
        lp = latest.get(ticker)
        mv = lp * p["shares"] if lp is not None else None
        upl = (lp - avg_cost) * p["shares"] if lp is not None else None
        positions.append({
            "ticker": ticker,
            "shares": round(p["shares"], 6),
            "avg_cost": round(avg_cost, 4),
            "latest_price": lp,
            "market_value": round(mv, 2) if mv is not None else None,
            "unrealized_pl": round(upl, 2) if upl is not None else None,
            "unrealized_pct": (round(upl / p["cost"] * 100, 2)
                               if upl is not None and p["cost"] > 0 else None),
            "realized_pl": round(p["realized"], 2),
        })
    totals = {
        "market_value": round(sum(x["market_value"] for x in positions
                                  if x["market_value"] is not None), 2),
        "unrealized_pl": round(sum(x["unrealized_pl"] for x in positions
                                   if x["unrealized_pl"] is not None), 2),
        "realized_pl": round(sum(x["realized_pl"] for x in positions), 2),
    }
    return positions, totals


@app.route("/api/paper-trades", methods=["GET"])
def api_paper_trades():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM paper_trades ORDER BY date DESC, id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/paper-trades", methods=["POST"])
def api_paper_trade_add():
    db = get_db()
    clean, err = validate_trade_payload(
        request.get_json(force=True, silent=True) or {}, db)
    if err:
        return jsonify({"error": err}), 400
    if clean["side"] == "sell":
        held = paper_shares(db, clean["ticker"])
        if clean["shares"] > held + 1e-9:
            return jsonify({"error": "you only hold %.4g paper shares of %s"
                                     % (held, clean["ticker"])}), 400
    now = utcnow()
    cur = db.execute(
        """INSERT INTO paper_trades
           (stock_ticker, date, side, shares, price, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (clean["ticker"], clean["date"], clean["side"], clean["shares"],
         clean["price"], clean["notes"], now))
    db.commit()
    row = db.execute("SELECT * FROM paper_trades WHERE id = ?",
                     (cur.lastrowid,)).fetchone()
    return jsonify({"trade": dict(row)}), 201


@app.route("/api/paper-trades/<int:tid>", methods=["DELETE"])
def api_paper_trade_delete(tid):
    db = get_db()
    cur = db.execute("DELETE FROM paper_trades WHERE id = ?", (tid,))
    db.commit()
    if not cur.rowcount:
        return jsonify({"error": "trade not found"}), 404
    return jsonify({"deleted": tid})


@app.route("/api/paper-portfolio")
def api_paper_portfolio():
    db = get_db()
    positions, totals = paper_portfolio(db)
    return jsonify({"positions": positions, "totals": totals})


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
