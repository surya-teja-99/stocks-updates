"""ML signal engine: learns from the user's own logged journal entries.

Two models share one feature set, trained on updates from ALL tickers:
  1. direction model -> will the next logged move be up or down? (binary)
  2. rating model    -> which rating bucket will the next move land in? (5-class)

Features per update use only trailing data (no lookahead):
  - today's change %
  - mean of the previous 5 changes (momentum)
  - price vs trailing 5-update mean price (relative position)
  - day of week
  - current rating encoded -2..+2

Labels come from the NEXT logged update of the same ticker.

Models train lazily on first request, are cached, and retrain automatically
when new updates are logged. Needs MIN_SAMPLES labeled samples; until then
signal_for() reports trained=False with a plain-English reason.
scikit-learn is imported lazily so the app still boots without it.
"""

from datetime import datetime

MIN_SAMPLES = 30

RATING_TO_INT = {"strong_sell": -2, "sell": -1, "hold": 0, "buy": 1,
                 "strong_buy": 2}
INT_TO_RATING = {v: k for k, v in RATING_TO_INT.items()}

_cache = {"key": None, "bundle": None}


def _bucket(change_pct):
    if change_pct >= 5:
        return 2
    if change_pct >= 2:
        return 1
    if change_pct <= -5:
        return -2
    if change_pct <= -2:
        return -1
    return 0


def _features(hist, current):
    """hist: previous 5 (price, change) tuples; current: (price, change, rating, date)."""
    price, change, rating, date = current
    mom = sum(h[1] for h in hist) / len(hist)
    ma = sum(h[0] for h in hist) / len(hist)
    rel = (price - ma) / ma if ma else 0.0
    try:
        dow = datetime.strptime(date, "%Y-%m-%d").weekday()
    except (TypeError, ValueError):
        dow = 0
    return [change, mom, rel, float(dow),
            float(RATING_TO_INT.get(rating, 0))]


def _build_dataset(db):
    rows = db.execute(
        "SELECT stock_ticker, date, price, change_percent, rating "
        "FROM daily_updates ORDER BY stock_ticker, date").fetchall()
    samples = []  # (date, features, y_dir, y_rating)
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["stock_ticker"], []).append(r)
    for ticker, ups in by_ticker.items():
        if len(ups) < 7:  # need 5 trailing + current + next
            continue
        for i in range(5, len(ups) - 1):
            hist = [(u["price"], u["change_percent"]) for u in ups[i - 5:i]]
            cur = ups[i]
            nxt = ups[i + 1]
            feats = _features(hist, (cur["price"], cur["change_percent"],
                                     cur["rating"], cur["date"]))
            next_chg = ((nxt["price"] - cur["price"]) / cur["price"] * 100
                        if cur["price"] else 0.0)
            samples.append((cur["date"], feats,
                            1 if next_chg > 0 else 0, _bucket(next_chg)))
    samples.sort(key=lambda s: s[0])
    return samples


def _train(db):
    """Train both models. Returns bundle dict or None if not enough data."""
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return {"error": "scikit-learn is not installed "
                         "(run: pip install -r requirements.txt)"}
    samples = _build_dataset(db)
    if len(samples) < MIN_SAMPLES:
        return None
    X = [s[1] for s in samples]
    y_dir = [s[2] for s in samples]
    y_rate = [s[3] for s in samples]
    cut = max(1, int(len(X) * 0.8))
    dir_model = LogisticRegression(max_iter=1000).fit(X[:cut], y_dir[:cut])
    rate_model = LogisticRegression(max_iter=1000).fit(X[:cut], y_rate[:cut])
    accuracy = None
    if len(X) - cut >= 10:
        accuracy = float(dir_model.score(X[cut:], y_dir[cut:]))
    return {"dir_model": dir_model, "rate_model": rate_model,
            "samples": len(samples), "accuracy": accuracy}


def _ticker_features(db, ticker):
    """Trailing features for one ticker's latest update, or None."""
    ups = db.execute(
        "SELECT date, price, change_percent, rating FROM daily_updates "
        "WHERE stock_ticker = ? ORDER BY date DESC LIMIT 6",
        (ticker,)).fetchall()
    if len(ups) < 6:
        return None
    latest = ups[0]
    hist = [(u["price"], u["change_percent"]) for u in ups[1:6]]
    hist.reverse()
    return _features(hist, (latest["price"], latest["change_percent"],
                            latest["rating"], latest["date"]))


def _data_key(db):
    row = db.execute(
        "SELECT COUNT(*) AS c, MAX(id) AS m FROM daily_updates").fetchone()
    return (row["c"] or 0, row["m"] or 0)


def signal_for(db, ticker):
    """ML signal for one ticker. Always returns a JSON-serializable dict."""
    ticker = (ticker or "").upper().strip()
    key = _data_key(db)
    count = key[0]
    if _cache["key"] != key:
        _cache["key"] = key
        _cache["bundle"] = _train(db) if count >= MIN_SAMPLES else None
    bundle = _cache["bundle"]
    if bundle is None:
        if count >= MIN_SAMPLES:
            reason = ("Not enough usable history yet: each ticker needs 7+ "
                      "logged updates to form training sequences.")
        else:
            reason = ("Log at least %d daily updates to unlock ML predictions "
                      "(you have %d)." % (MIN_SAMPLES, count))
        return {"trained": False, "reason": reason,
                "have": count, "need": MIN_SAMPLES}
    if "error" in bundle:
        return {"trained": False, "reason": bundle["error"]}
    feats = _ticker_features(db, ticker)
    if feats is None:
        return {"trained": False,
                "reason": "Not enough history for %s yet (needs 6 logged "
                          "updates)." % ticker,
                "samples": bundle["samples"]}
    p_up = float(bundle["dir_model"].predict_proba([feats])[0][1])
    direction = "up" if p_up >= 0.5 else "down"
    confidence = p_up if direction == "up" else 1 - p_up
    rating = INT_TO_RATING[int(bundle["rate_model"].predict([feats])[0])]
    return {"trained": True, "ticker": ticker, "direction": direction,
            "confidence": round(confidence, 3),
            "suggested_rating": rating,
            "samples": bundle["samples"], "accuracy": bundle["accuracy"]}
