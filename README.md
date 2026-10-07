# Stocks Updates

A personal stock-opinion journal: a private, single-user web app where the owner
maintains a watchlist of stocks and logs a daily price, day change, recommendation
rating, and free-text notes per stock. No live market data, no automation — every
data point is entered by hand.

> Recommendations reflect the owner's personal opinions and are for informational
> purposes only — not financial advice.

**Stack:** Python backend (Flask + SQLite via stdlib `sqlite3`, no ORM) serving a
plain HTML/CSS/JS frontend (no frameworks, no build step, no CDN dependencies).

## Features

- **Dashboard** — watchlist cards with each stock's latest update (price, color-coded
  day change, color-coded rating badge, last-updated date, note snippet). Sortable by
  rating / change % / last updated; filterable by sector and rating.
- **Stock directory** — searchable across ~13,000 US symbols by ticker or company
  name; add/remove any stock to/from the watchlist.
- **Stock detail** — company info, latest recommendation summary, editable history
  table (edit/delete per entry), and a hand-drawn SVG price chart.
- **Daily update editor** — fast entry form; step through the whole watchlist one by
  one. Saving upserts: one update per stock per date.
- **CSV** — import/export of daily updates, plus a downloadable template.
- **Global search bar** in the header searches every stock by ticker or company name.
- Dark, professional finance-workspace design; responsive on desktop and phone.

## Run it

```bash
cd stocks-updates

# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed the database (~13k symbols + 40-stock starter watchlist)
python backend/seed.py

# 4. Start the server
python backend/app.py
```

Open **http://localhost:5000** in your browser.

In VS Code: open this folder, run the commands above in the integrated terminal
(`Ctrl+`` `), then open the URL. No extensions required.

## Where entries are stored

Everything lives in **`stocks.db`** (SQLite) in the project root — created
automatically on first run, loaded with seed data by `backend/seed.py`. It is
gitignored (`*.db`), so it never gets committed. Back up your journal with the
**CSV export** in the app (Log Update → Bulk data).

## Project structure

```
stocks-updates/
├── backend/
│   ├── app.py            # Flask app: serves the frontend + JSON API (run: python backend/app.py)
│   └── seed.py           # loads data/*.json into stocks.db (run: python backend/seed.py)
├── index.html            # single page, hash routing (#/dashboard, #/directory, #/stock/TICKER, #/update)
├── css/styles.css        # dark theme, responsive
├── js/app.js             # frontend logic; all data access via fetch() to /api/*
├── data/
│   ├── stocks.json           # ~13k symbols: {ticker, company_name, sector, exchange}
│   ├── watchlist.json        # 40-stock starter watchlist (canonical names/sectors)
│   └── raw_nasdaqlisted.txt / raw_otherlisted.txt   # upstream symbol files (for re-seeding)
├── scripts/seed.py       # rebuilds data/stocks.json from the raw symbol files
├── requirements.txt      # flask
├── .gitignore            # venv/, *.db, __pycache__/
├── stocks.db             # SQLite database (created at runtime, gitignored)
└── README.md
```

## API reference

Base URL: `http://localhost:5000`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/meta` | Counts: `{stocks, watchlist, updates}` |
| GET | `/api/stocks?q=...&limit=50` | Search directory by ticker or company name (case-insensitive LIKE) |
| GET | `/api/watchlist` | Watchlist stocks with their latest update joined in |
| POST | `/api/watchlist` | Body `{"ticker": "AAPL"}` — add to watchlist |
| DELETE | `/api/watchlist/<ticker>` | Remove from watchlist |
| GET | `/api/stocks/<ticker>` | Stock info + full update history (newest first) |
| GET | `/api/stocks/<ticker>/updates` | Update history for one ticker (newest first) |
| POST | `/api/updates` | Create/update: `{ticker, date, price, change_percent, rating, notes}` — upserts on `(ticker, date)` |
| PUT | `/api/updates/<id>` | Edit an entry by id |
| DELETE | `/api/updates/<id>` | Delete an entry by id |
| GET | `/api/export` | CSV download of all daily updates |
| POST | `/api/import` | Multipart form (`file` field) CSV upload; upserts rows, returns `{imported, updated, skipped, errors}` |
| GET | `/api/template` | CSV template download |

Ratings: `strong_buy`, `buy`, `hold`, `sell`, `strong_sell`. Dates are `YYYY-MM-DD`.

## Re-seeding the symbol directory

```bash
cd stocks-updates
curl -sSL -o data/raw_nasdaqlisted.txt https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
curl -sSL -o data/raw_otherlisted.txt https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
python3 scripts/seed.py     # rebuilds data/stocks.json
python backend/seed.py      # reloads the database (updates and watchlist flags are preserved)
```

`scripts/seed.py` skips test issues, normalizes `/` in tickers to `.` (e.g. `BRK/B` →
`BRK.B`), and maps exchange codes (`N`→NYSE, `A`→AMEX, `P`→NYSE Arca, `Z`→BATS).

## Notes

- The frontend is plain static files served by Flask (`static_folder` = project root),
  so the UI works with no build step. All data flows through the `/api/*` endpoints;
  the browser keeps no journal state itself.
- For production use behind a real web server, run with a WSGI server
  (e.g. `gunicorn backend.app:app`) instead of the Flask dev server, and point
  `DB_PATH` at a persistent volume.
