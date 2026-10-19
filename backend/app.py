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
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
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
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    recovery_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL REFERENCES stocks(ticker),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);
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


# ------------------------------------------------------------ user accounts
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _read_secret_file(name, generator):
    """Read a gitignored secret file, creating it with generator() if missing."""
    path = os.path.join(BACKEND_DIR, name)
    try:
        with open(path) as f:
            value = f.read().strip()
            if value:
                return value
    except OSError:
        pass
    value = generator()
    with open(path, "w") as f:
        f.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


app.secret_key = os.environ.get("SECRET_KEY") or _read_secret_file(
    ".secret_key", lambda: secrets.token_hex(32))


def current_user_id():
    return session.get("user_id")


@app.before_request
def require_login():
    # Auth pages, login-status endpoint and static assets are always reachable.
    if request.path in ("/login", "/logout", "/signup", "/forgot", "/api/me") or request.path.startswith(
            ("/js/", "/css/", "/favicon.ico")):
        return None
    if current_user_id():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "login required"}), 401
    return redirect("/login")


AUTH_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ \u2014 Stocks Updates</title>
<style>
body{background:#060a13;color:#f1f5fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:20px;box-sizing:border-box;
background-image:radial-gradient(900px 480px at 85% -10%,rgba(91,155,255,.1),transparent 60%),radial-gradient(700px 420px at 10% 0%,rgba(143,107,255,.08),transparent 60%)}
.box{background:linear-gradient(180deg,rgba(26,36,64,.6),rgba(14,21,36,.95));border:1px solid rgba(148,163,184,.16);
border-radius:16px;padding:36px;width:min(380px,92vw);box-shadow:0 18px 60px rgba(0,0,0,.5);animation:rise .5s cubic-bezier(.22,.68,.32,1)}
@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.mark{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#4f8cff,#7a5cff);
display:flex;align-items:center;justify-content:center;margin-bottom:18px;box-shadow:0 8px 24px rgba(99,132,255,.4)}
h1{margin:0 0 6px;font-size:1.35rem;font-weight:800;letter-spacing:-.01em}
p{color:#93a0b8;font-size:.9rem;margin:0 0 20px;line-height:1.5}
input{width:100%;box-sizing:border-box;background:rgba(6,10,19,.8);border:1px solid #26314d;color:#f1f5fb;
border-radius:10px;padding:11px 13px;margin-bottom:12px;font-size:1rem;transition:border-color .18s,box-shadow .18s}
input:focus{outline:none;border-color:rgba(91,155,255,.6);box-shadow:0 0 0 3px rgba(91,155,255,.16)}
button,.abtn{width:100%;box-sizing:border-box;background:linear-gradient(135deg,#4f8cff,#7a5cff);border:0;color:#fff;font-weight:700;border-radius:10px;
padding:12px;font-size:1rem;cursor:pointer;box-shadow:0 6px 22px rgba(99,132,255,.38);transition:transform .16s,box-shadow .18s,filter .18s;
display:block;text-align:center;text-decoration:none;font-family:inherit}
button:hover,.abtn:hover{transform:translateY(-1px);filter:brightness(1.06);box-shadow:0 10px 30px rgba(99,132,255,.5);color:#fff}
button:active,.abtn:active{transform:translateY(1px)}
.err{color:#fda4af;font-size:.88rem;margin-bottom:12px;min-height:1.2em}
.links{margin-top:18px;font-size:.88rem;color:#93a0b8;text-align:center;line-height:2}
.links a{color:#5b9bff;text-decoration:none}
.links a:hover{text-decoration:underline}
.recovery{background:rgba(6,10,19,.85);border:1px dashed rgba(91,155,255,.55);border-radius:10px;
padding:15px;font-size:1.3rem;font-weight:800;letter-spacing:.14em;text-align:center;margin:6px 0 18px;color:#cfe1ff}
.warn{color:#f3cd6a;font-size:.86rem;line-height:1.55;margin-bottom:14px}
.ok{color:#4ade80;font-size:.88rem;margin-bottom:12px;min-height:1.2em}
</style></head><body><div class="box">
<div class="mark"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17 L8 12 L12 14 L17 7 L21 9"/><path d="M17 7 L21 9 L19 13"/></svg></div>
<h1>__HEADING__</h1><p>__SUB__</p>
<div class="err">__ERROR__</div>
__BODY__
<div class="links">__LINKS__</div>
</div></body></html>"""


def render_auth(title, heading, sub, error="", body="", links=""):
    html = AUTH_PAGE
    for token, val in (("__TITLE__", title), ("__HEADING__", heading),
                       ("__SUB__", sub), ("__ERROR__", error),
                       ("__BODY__", body), ("__LINKS__", links)):
        html = html.replace(token, val)
    return html


def _login_form():
    return ('<form method="post">'
            '<input type="email" name="email" placeholder="Email" autocomplete="username" autofocus>'
            '<input type="password" name="password" placeholder="Password" autocomplete="current-password">'
            '<button type="submit">Log in</button></form>')


def _signup_form():
    return ('<form method="post">'
            '<input type="email" name="email" placeholder="Email" autocomplete="username" autofocus>'
            '<input type="password" name="password" placeholder="Password (min 8 characters)" autocomplete="new-password">'
            '<input type="password" name="password2" placeholder="Confirm password" autocomplete="new-password">'
            '<button type="submit">Sign up</button></form>')


def _forgot_form():
    return ('<form method="post"><input type="hidden" name="stage" value="identify">'
            '<input type="email" name="email" placeholder="Email" autocomplete="username" autofocus>'
            '<input type="text" name="code" placeholder="Recovery code (xxxx-xxxx-xxxx)" autocomplete="off">'
            '<button type="submit">Verify</button></form>')


def _reset_form():
    return ('<form method="post"><input type="hidden" name="stage" value="reset">'
            '<input type="password" name="password" placeholder="New password (min 8 characters)" autocomplete="new-password" autofocus>'
            '<input type="password" name="password2" placeholder="Confirm new password" autocomplete="new-password">'
            '<button type="submit">Set new password</button></form>')


def _new_recovery_code():
    raw = secrets.token_hex(6)
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def _adopt_legacy_data(db, user_id):
    """The first registered user inherits the pre-accounts journal data."""
    if db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] != 1:
        return
    now = utcnow()
    db.execute("UPDATE daily_updates SET user_id = ? WHERE user_id IS NULL", (user_id,))
    db.execute("UPDATE paper_trades SET user_id = ? WHERE user_id IS NULL", (user_id,))
    db.execute("""INSERT OR IGNORE INTO user_watchlist (user_id, ticker, created_at)
                  SELECT ?, ticker, ? FROM stocks WHERE on_watchlist = 1""",
               (user_id, now))


LOGIN_LINKS = '<a href="/signup">Sign up</a> &nbsp;&middot;&nbsp; <a href="/forgot">Forgot password?</a>'
BACK_LINKS = '<a href="/login">Back to log in</a>'


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user_id():
        return redirect("/")
    if request.method == "POST":
        db = get_db()
        email = request.form.get("email", "").strip().lower()
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
            session["user_id"] = row["id"]
            return redirect("/")
        return render_auth("Log in", "Welcome back", "Log in to your Stocks Updates journal.",
                           "Wrong email or password. Try again.", _login_form(), LOGIN_LINKS)
    return render_auth("Log in", "Welcome back", "Log in to your Stocks Updates journal.",
                       "", _login_form(), LOGIN_LINKS)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user_id():
        return redirect("/")
    if request.method == "POST":
        db = get_db()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if not EMAIL_RE.match(email):
            error = "Enter a valid email address."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif pw != pw2:
            error = "Passwords do not match."
        elif db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            error = "An account with this email already exists. Try logging in."
        else:
            error = None
        if error:
            return render_auth("Sign up", "Create your account",
                               "Your journal is private to you.",
                               error, _signup_form(), BACK_LINKS)
        code = _new_recovery_code()
        cur = db.execute(
            "INSERT INTO users (email, password_hash, recovery_hash, created_at)"
            " VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(pw), generate_password_hash(code), utcnow()))
        _adopt_legacy_data(db, cur.lastrowid)
        db.commit()
        session["user_id"] = cur.lastrowid
        body = ('<div class="warn">Save this recovery code somewhere safe &mdash; '
                'it is the <strong>only</strong> way to reset your password if you forget it.</div>'
                '<div class="recovery">' + code + '</div>'
                '<a class="abtn" href="/">Continue to your journal</a>')
        return render_auth("Account created", "You\u2019re in!",
                           "Your private journal is ready.", "", body, "")
    return render_auth("Sign up", "Create your account",
                       "Your journal is private to you.", "", _signup_form(), BACK_LINKS)


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if current_user_id():
        return redirect("/")
    if request.method == "POST":
        db = get_db()
        if request.form.get("stage", "identify") == "identify":
            email = request.form.get("email", "").strip().lower()
            code = request.form.get("code", "").strip()
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row and check_password_hash(row["recovery_hash"], code):
                session["pw_reset_user"] = row["id"]
                return render_auth("Reset password", "Set a new password",
                                   "Choose a new password for your account.",
                                   "", _reset_form(), BACK_LINKS)
            return render_auth("Forgot password", "Reset your password",
                               "Enter your email and the recovery code shown when you signed up.",
                               "Email or recovery code didn\u2019t match.", _forgot_form(), BACK_LINKS)
        user_id = session.get("pw_reset_user")
        if not user_id:
            return redirect("/forgot")
        pw = request.form.get("password", "")
        if len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif pw != request.form.get("password2", ""):
            error = "Passwords do not match."
        else:
            error = None
        if error:
            return render_auth("Reset password", "Set a new password", "",
                               error, _reset_form(), BACK_LINKS)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(pw), user_id))
        db.commit()
        session.pop("pw_reset_user", None)
        session["user_id"] = user_id
        return redirect("/")
    return render_auth("Forgot password", "Reset your password",
                       "Enter your email and the recovery code shown when you signed up.",
                       "", _forgot_form(), BACK_LINKS)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/me")
def api_me():
    return jsonify({"logged_in": bool(current_user_id())})


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path=DB_PATH):
    """Create tables if they don't exist."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        _migrate_accounts(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_accounts(conn):
    """Bring pre-accounts databases up to the multi-user schema.

    - daily_updates gains user_id; its UNIQUE(stock_ticker, date) becomes
      UNIQUE(user_id, stock_ticker, date) so each user has their own entries.
    - paper_trades gains user_id.
    Existing rows keep user_id NULL until the first signup adopts them.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_updates)")]
    if "user_id" not in cols:
        conn.executescript("""
        CREATE TABLE daily_updates_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            stock_ticker TEXT NOT NULL REFERENCES stocks(ticker),
            date TEXT NOT NULL,
            price REAL NOT NULL,
            change_percent REAL NOT NULL DEFAULT 0,
            rating TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (user_id, stock_ticker, date)
        );
        INSERT INTO daily_updates_new
            (id, user_id, stock_ticker, date, price, change_percent, rating, notes, created_at, updated_at)
            SELECT id, NULL, stock_ticker, date, price, change_percent, rating, notes, created_at, updated_at
            FROM daily_updates;
        DROP TABLE daily_updates;
        ALTER TABLE daily_updates_new RENAME TO daily_updates;
        CREATE INDEX IF NOT EXISTS idx_updates_ticker_date ON daily_updates (stock_ticker, date);
        CREATE INDEX IF NOT EXISTS idx_updates_user ON daily_updates (user_id, stock_ticker, date);
        """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")]
    if "user_id" not in cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN user_id INTEGER REFERENCES users(id)")


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


def upsert_update(db, clean, user_id):
    """Insert or update the row for (user, ticker, date). Returns (update, was_updated)."""
    now = utcnow()
    existing = db.execute(
        "SELECT id FROM daily_updates WHERE user_id = ? AND stock_ticker = ? AND date = ?",
        (user_id, clean["ticker"], clean["date"]),
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
           (user_id, stock_ticker, date, price, change_percent, rating, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, clean["ticker"], clean["date"], clean["price"], clean["change_percent"],
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
    u = current_user_id()
    stocks = db.execute("SELECT COUNT(*) AS c FROM stocks").fetchone()["c"]
    watchlist = db.execute(
        "SELECT COUNT(*) AS c FROM user_watchlist WHERE user_id = ?", (u,)).fetchone()["c"]
    updates = db.execute(
        "SELECT COUNT(*) AS c FROM daily_updates WHERE user_id = ?", (u,)).fetchone()["c"]
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


@app.route("/api/ml-signal/<ticker>")
def api_ml_signal(ticker):
    """ML signal for one ticker: predicted direction + learned rating suggestion."""
    from backend.ml_signals import signal_for
    return jsonify(signal_for(get_db(), ticker, current_user_id()))


@app.route("/api/stocks")
def api_stocks():
    q = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    db = get_db()
    u = current_user_id()
    wl = "EXISTS(SELECT 1 FROM user_watchlist w WHERE w.user_id = ? AND w.ticker = s.ticker)"
    if not q:
        rows = db.execute(
            "SELECT s.ticker, s.company_name, s.sector, s.exchange, " + wl + " AS on_watchlist"
            " FROM stocks s ORDER BY s.ticker LIMIT ?",
            (u, limit),
        ).fetchall()
    else:
        like = "%" + q + "%"
        rows = db.execute(
            """SELECT s.ticker, s.company_name, s.sector, s.exchange, """ + wl + """ AS on_watchlist
               FROM stocks s
               WHERE s.ticker LIKE ? OR s.company_name LIKE ?
               ORDER BY CASE WHEN s.ticker LIKE ? THEN 0 WHEN s.ticker LIKE ? THEN 1 ELSE 2 END, s.ticker
               LIMIT ?""",
            (u, like, like, q + "%", like, limit),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watchlist")
def api_watchlist():
    db = get_db()
    u = current_user_id()
    rows = db.execute(
        """SELECT s.ticker, s.company_name, s.sector, s.exchange,
                  u.id AS uid, u.date, u.price, u.change_percent, u.rating, u.notes
           FROM user_watchlist w
           JOIN stocks s ON s.ticker = w.ticker
           LEFT JOIN daily_updates u ON u.stock_ticker = s.ticker AND u.user_id = w.user_id
               AND u.date = (SELECT MAX(date) FROM daily_updates
                             WHERE stock_ticker = s.ticker AND user_id = w.user_id)
           WHERE w.user_id = ?
           ORDER BY s.ticker""",
        (u,),
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
    db.execute(
        "INSERT OR IGNORE INTO user_watchlist (user_id, ticker, created_at) VALUES (?, ?, ?)",
        (current_user_id(), ticker, utcnow()),
    )
    db.commit()
    return jsonify({"ok": True, "ticker": ticker})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def api_watchlist_remove(ticker):
    db = get_db()
    db.execute("DELETE FROM user_watchlist WHERE user_id = ? AND ticker = ?",
               (current_user_id(), ticker.upper()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/stocks/<ticker>")
def api_stock_detail(ticker):
    ticker = ticker.upper()
    db = get_db()
    u = current_user_id()
    s = db.execute(
        "SELECT ticker, company_name, sector, exchange FROM stocks WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if not s:
        return jsonify({"error": "unknown ticker: " + ticker}), 404
    on_watchlist = db.execute(
        "SELECT 1 FROM user_watchlist WHERE user_id = ? AND ticker = ?", (u, ticker)
    ).fetchone() is not None
    updates = db.execute(
        "SELECT * FROM daily_updates WHERE user_id = ? AND stock_ticker = ? ORDER BY date DESC",
        (u, ticker),
    ).fetchall()
    stock = dict(s)
    stock["on_watchlist"] = on_watchlist
    return jsonify({"stock": stock, "updates": [row_to_update(r) for r in updates]})


@app.route("/api/stocks/<ticker>/updates")
def api_stock_updates(ticker):
    db = get_db()
    u = current_user_id()
    rows = db.execute(
        "SELECT * FROM daily_updates WHERE user_id = ? AND stock_ticker = ? ORDER BY date DESC",
        (u, ticker.upper()),
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
    upd, was_updated = upsert_update(db, clean, current_user_id())
    return jsonify({"update": upd, "updated": was_updated})


@app.route("/api/updates/<int:uid>", methods=["PUT"])
def api_update_edit(uid):
    data = request.get_json(force=True, silent=True) or {}
    clean, err = validate_update_payload(data)
    if err:
        return jsonify({"error": err}), 400
    db = get_db()
    u = current_user_id()
    if not db.execute("SELECT id FROM daily_updates WHERE id = ? AND user_id = ?",
                      (uid, u)).fetchone():
        return jsonify({"error": "update not found"}), 404
    if not ticker_exists(db, clean["ticker"]):
        return jsonify({"error": "unknown ticker: " + clean["ticker"]}), 400
    clash = db.execute(
        "SELECT id FROM daily_updates WHERE user_id = ? AND stock_ticker = ? AND date = ? AND id <> ?",
        (u, clean["ticker"], clean["date"], uid),
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
    db.execute("DELETE FROM daily_updates WHERE id = ? AND user_id = ?",
               (uid, current_user_id()))
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
    rows = db.execute(
        "SELECT * FROM daily_updates WHERE user_id = ? ORDER BY stock_ticker, date",
        (current_user_id(),)).fetchall()
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
    u = current_user_id()
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
        _, was_updated = upsert_update(db, clean, u)
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


def paper_shares(db, ticker, user_id):
    """Net paper shares currently held for a ticker."""
    row = db.execute(
        """SELECT COALESCE(SUM(CASE WHEN side = 'buy' THEN shares ELSE -shares END), 0) AS s
           FROM paper_trades WHERE user_id = ? AND stock_ticker = ?""",
        (user_id, ticker)).fetchone()
    return row["s"] or 0.0


def paper_portfolio(db, user_id):
    """Average-cost positions from paper trades, valued at latest logged prices.

    Returns (positions, totals). Positions with no logged price yet get
    latest_price/market_value/unrealized_* = None.
    """
    trades = db.execute(
        "SELECT * FROM paper_trades WHERE user_id = ? ORDER BY date, id",
        (user_id,)).fetchall()
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
           WHERE user_id = ?
             AND date = (SELECT MAX(date) FROM daily_updates
                         WHERE user_id = ? AND stock_ticker = u.stock_ticker)""",
        (user_id, user_id)).fetchall()}
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
        "SELECT * FROM paper_trades WHERE user_id = ? ORDER BY date DESC, id DESC",
        (current_user_id(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/paper-trades", methods=["POST"])
def api_paper_trade_add():
    db = get_db()
    u = current_user_id()
    clean, err = validate_trade_payload(
        request.get_json(force=True, silent=True) or {}, db)
    if err:
        return jsonify({"error": err}), 400
    if clean["side"] == "sell":
        held = paper_shares(db, clean["ticker"], u)
        if clean["shares"] > held + 1e-9:
            return jsonify({"error": "you only hold %.4g paper shares of %s"
                                     % (held, clean["ticker"])}), 400
    now = utcnow()
    cur = db.execute(
        """INSERT INTO paper_trades
           (user_id, stock_ticker, date, side, shares, price, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (u, clean["ticker"], clean["date"], clean["side"], clean["shares"],
         clean["price"], clean["notes"], now))
    db.commit()
    row = db.execute("SELECT * FROM paper_trades WHERE id = ?",
                     (cur.lastrowid,)).fetchone()
    return jsonify({"trade": dict(row)}), 201


@app.route("/api/paper-trades/<int:tid>", methods=["DELETE"])
def api_paper_trade_delete(tid):
    db = get_db()
    cur = db.execute("DELETE FROM paper_trades WHERE id = ? AND user_id = ?",
                     (tid, current_user_id()))
    db.commit()
    if not cur.rowcount:
        return jsonify({"error": "trade not found"}), 404
    return jsonify({"deleted": tid})


@app.route("/api/paper-portfolio")
def api_paper_portfolio():
    db = get_db()
    positions, totals = paper_portfolio(db, current_user_id())
    return jsonify({"positions": positions, "totals": totals})


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
