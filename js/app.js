'use strict';
/* Stocks Updates — single-page app, no dependencies.
   All data access goes through the `api` client below (Flask backend).
   The Python server is the source of truth; nothing is kept in localStorage. */

const DISCLAIMER = "Recommendations reflect the owner's personal opinions and are for informational purposes only \u2014 not financial advice.";

const RATINGS = {
  strong_buy:  { label: 'Strong Buy',  cls: 'rb-strong-buy',  rank: 5 },
  buy:         { label: 'Buy',         cls: 'rb-buy',         rank: 4 },
  hold:        { label: 'Hold',        cls: 'rb-hold',        rank: 3 },
  sell:        { label: 'Sell',        cls: 'rb-sell',        rank: 2 },
  strong_sell: { label: 'Strong Sell', cls: 'rb-strong-sell', rank: 1 },
};
const RATING_KEYS = Object.keys(RATINGS);

/* ---------------- utils ---------------- */
const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function todayISO() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return d.getFullYear() + '-' + m + '-' + day;
}

function fmtDate(iso) {
  if (!iso) return '\u2014';
  const parts = String(iso).split('-');
  if (parts.length !== 3) return esc(iso);
  const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  if (isNaN(d.getTime())) return esc(iso);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function fmtPrice(p) {
  const n = Number(p);
  if (!isFinite(n)) return '\u2014';
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtChg(c) {
  const n = Number(c);
  if (!isFinite(n)) return '\u2014';
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function chgClass(c) {
  const n = Number(c);
  if (!isFinite(n)) return 'flat';
  return n > 0 ? 'pos' : (n < 0 ? 'neg' : 'flat');
}

function ratingBadge(rating) {
  const r = RATINGS[rating];
  if (!r) return '<span class="badge rb-none">No rating</span>';
  return '<span class="badge ' + r.cls + '">' + esc(r.label) + '</span>';
}

let toastTimer = null;
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

function snippet(text, maxLen) {
  const t = String(text || '').trim();
  if (t.length <= maxLen) return t;
  return t.slice(0, maxLen - 1).trimEnd() + '\u2026';
}

/* ---------------- API client ---------------- */
async function apiFetch(path, opts) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error('Could not reach the server. Is the backend running? (python backend/app.py)');
  }
  const ct = res.headers.get('content-type') || '';
  let data = null;
  try {
    data = ct.includes('application/json') ? await res.json() : await res.text();
  } catch (e) { /* ignore parse errors */ }
  if (!res.ok) {
    if (res.status === 401 && path.startsWith('/api/')) {
      window.location.href = '/login';
      throw new Error('Login required');
    }
    const msg = (data && data.error) || ('Request failed with status ' + res.status);
    throw new Error(msg);
  }
  return data;
}

const api = {
  get: (path) => apiFetch(path),
  post: (path, body) => apiFetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  put: (path, body) => apiFetch(path, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  del: (path) => apiFetch(path, { method: 'DELETE' }),
};

/* ---------------- Store (REST backend) ---------------- */
const Store = {
  stocksCount: 0,
  _wl: new Set(),

  async init() {
    const meta = await api.get('/api/meta');
    this.stocksCount = meta.stocks;
    await this.refreshWatchlist();
  },

  async refreshWatchlist() {
    const rows = await api.get('/api/watchlist');
    this._wl = new Set(rows.map((r) => r.ticker));
    return rows;
  },

  /** Directory search. Returns [{ticker, company_name, sector, exchange, on_watchlist}]. */
  async searchStocks(q, limit) {
    return api.get('/api/stocks?q=' + encodeURIComponent(q) + '&limit=' + (limit || 50));
  },

  async getStock(ticker) {
    const d = await api.get('/api/stocks/' + encodeURIComponent(ticker));
    return d.stock;
  },

  async getWatchlistStocks() {
    const rows = await api.get('/api/watchlist');
    return rows.map((r) => ({ ticker: r.ticker, company_name: r.company_name, sector: r.sector, exchange: r.exchange }));
  },

  /** Watchlist stocks with their latest update joined in: [{ticker, ..., latest|null}]. */
  async getWatchlistDetailed() {
    return api.get('/api/watchlist');
  },

  isOnWatchlist(ticker) { return this._wl.has(ticker); },

  async setOnWatchlist(ticker, on) {
    if (on) await api.post('/api/watchlist', { ticker });
    else await api.del('/api/watchlist/' + encodeURIComponent(ticker));
    await this.refreshWatchlist();
  },

  /** Updates for one ticker, newest first. */
  async getUpdates(ticker) {
    return api.get('/api/stocks/' + encodeURIComponent(ticker) + '/updates');
  },

  async getLatest(ticker) {
    const list = await this.getUpdates(ticker);
    return list[0] || null;
  },

  /** Upsert: one update per stock per date. Resolves {update, updated}. */
  async saveUpdate(data) {
    return api.post('/api/updates', {
      ticker: data.ticker,
      date: data.date,
      price: data.price,
      change_percent: data.change_percent,
      rating: data.rating,
      notes: data.notes,
    });
  },

  /** Update an existing entry by id. Resolves {update}. */
  async updateUpdate(id, data) {
    return api.put('/api/updates/' + encodeURIComponent(id), {
      ticker: data.ticker,
      date: data.date,
      price: data.price,
      change_percent: data.change_percent,
      rating: data.rating,
      notes: data.notes,
    });
  },

  async deleteUpdate(id) {
    await api.del('/api/updates/' + encodeURIComponent(id));
  },

  /** Import a CSV file; resolves {imported, updated, skipped, errors}. */
  async importCSV(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/import', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Import failed');
    return data;
  },

  /** Paper trades, newest first. */
  async getPaperTrades() {
    return api.get('/api/paper-trades');
  },

  /** Paper portfolio: {positions, totals}, valued at latest logged prices. */
  async getPaperPortfolio() {
    return api.get('/api/paper-portfolio');
  },

  /** Log a paper trade; resolves {trade}. */
  async addPaperTrade(data) {
    return api.post('/api/paper-trades', {
      ticker: data.ticker,
      date: data.date,
      side: data.side,
      shares: data.shares,
      price: data.price,
      notes: data.notes,
    });
  },

  async deletePaperTrade(id) {
    await api.del('/api/paper-trades/' + encodeURIComponent(id));
  },
};

/* ---------------- dashboard ---------------- */
const dashState = { sector: '', rating: '', sort: 'rating' };

async function renderDashboard() {
  const view = $('#view');
  const items = await Store.getWatchlistDetailed(); // [{ticker, company_name, sector, exchange, latest|null}]
  const stocks = items.map((i) => ({ ticker: i.ticker, company_name: i.company_name, sector: i.sector, exchange: i.exchange }));

  const sectors = Array.from(new Set(stocks.map((s) => s.sector).filter(Boolean))).sort();

  const counts = { strong_buy: 0, buy: 0, hold: 0, sell: 0, strong_sell: 0, none: 0 };
  for (const i of items) {
    if (i.latest && RATINGS[i.latest.rating]) counts[i.latest.rating]++; else counts.none++;
  }

  let html = ''
    + '<div class="disclaimer-bar">' + esc(DISCLAIMER) + '</div>'
    + '<div class="page-head"><div><h1>Dashboard</h1><p>Your watchlist with the latest logged recommendation per stock.</p></div></div>'
    + '<div class="summary-strip">'
    + statHtml(stocks.length, 'On watchlist')
    + statHtml(counts.strong_buy + counts.buy, 'Buy / Strong Buy')
    + statHtml(counts.hold, 'Hold')
    + statHtml(counts.sell + counts.strong_sell, 'Sell / Strong Sell')
    + statHtml(counts.none, 'No update yet')
    + '</div>';

  if (!stocks.length) {
    html += '<div class="panel"><div class="empty">Your watchlist is empty.<br>Add stocks from the directory to start tracking them.<br><a class="btn primary" href="#/directory">Browse directory</a></div></div>';
    view.innerHTML = html;
    return;
  }

  html += '<div class="filterbar">'
    + '<div class="field"><label for="f-sector">Sector</label><select id="f-sector">'
    + '<option value="">All sectors</option>'
    + sectors.map((s) => '<option value="' + esc(s) + '"' + (dashState.sector === s ? ' selected' : '') + '>' + esc(s) + '</option>').join('')
    + '</select></div>'
    + '<div class="field"><label for="f-rating">Rating</label><select id="f-rating">'
    + '<option value="">All ratings</option>'
    + RATING_KEYS.map((k) => '<option value="' + k + '"' + (dashState.rating === k ? ' selected' : '') + '>' + esc(RATINGS[k].label) + '</option>').join('')
    + '<option value="none"' + (dashState.rating === 'none' ? ' selected' : '') + '>No update yet</option>'
    + '</select></div>'
    + '<div class="field"><label for="f-sort">Sort by</label><select id="f-sort">'
    + '<option value="rating"' + (dashState.sort === 'rating' ? ' selected' : '') + '>Rating</option>'
    + '<option value="change"' + (dashState.sort === 'change' ? ' selected' : '') + '>Change %</option>'
    + '<option value="updated"' + (dashState.sort === 'updated' ? ' selected' : '') + '>Last updated</option>'
    + '</select></div>'
    + '</div>';

  let filtered = items.slice();
  if (dashState.sector) filtered = filtered.filter((i) => i.sector === dashState.sector);
  if (dashState.rating) {
    filtered = filtered.filter((i) => dashState.rating === 'none' ? !i.latest : (i.latest && i.latest.rating === dashState.rating));
  }
  const rankOf = (i) => (i.latest && RATINGS[i.latest.rating] ? RATINGS[i.latest.rating].rank : -1);
  filtered.sort((a, b) => {
    if (dashState.sort === 'change') {
      const ca = a.latest ? a.latest.change_percent : -Infinity, cb = b.latest ? b.latest.change_percent : -Infinity;
      return cb - ca;
    }
    if (dashState.sort === 'updated') {
      const da = a.latest ? a.latest.date : '', db = b.latest ? b.latest.date : '';
      return db < da ? -1 : (db > da ? 1 : 0);
    }
    return rankOf(b) - rankOf(a);
  });

  if (!filtered.length) {
    html += '<div class="panel"><div class="empty">No stocks match these filters.</div></div>';
  } else {
    html += '<div class="cards">';
    for (const item of filtered) {
      const upd = item.latest;
      html += '<article class="card">'
        + '<div class="card-top"><div><div class="ticker">' + esc(item.ticker) + '</div>'
        + '<div class="company">' + esc(item.company_name) + '</div></div>'
        + ratingBadge(upd ? upd.rating : null) + '</div>'
        + (upd
          ? '<div class="price-row"><span class="price">$' + fmtPrice(upd.price) + '</span>'
            + '<span class="chg ' + chgClass(upd.change_percent) + '">' + fmtChg(upd.change_percent) + '</span></div>'
            + '<div class="card-meta"><span>' + fmtDate(upd.date) + '</span><span>\u00b7</span><span>' + esc(item.sector || 'Unknown sector') + '</span></div>'
            + (upd.notes ? '<div class="note-snippet">' + esc(snippet(upd.notes, 110)) + '</div>' : '')
          : '<div class="card-meta"><span>' + esc(item.sector || 'Unknown sector') + '</span><span>\u00b7</span><span>No update logged yet</span></div>')
        + '<div class="card-actions">'
        + '<a class="btn small" href="#/stock/' + encodeURIComponent(item.ticker) + '">Details</a>'
        + '<a class="btn small" href="#/update/' + encodeURIComponent(item.ticker) + '">Log update</a>'
        + '</div></article>';
    }
    html += '</div>';
  }

  view.innerHTML = html;
  $('#f-sector').addEventListener('change', (e) => { dashState.sector = e.target.value; renderDashboard(); });
  $('#f-rating').addEventListener('change', (e) => { dashState.rating = e.target.value; renderDashboard(); });
  $('#f-sort').addEventListener('change', (e) => { dashState.sort = e.target.value; renderDashboard(); });
}

function statHtml(num, label) {
  return '<div class="stat"><div class="stat-num">' + num + '</div><div class="stat-label">' + esc(label) + '</div></div>';
}

/* ---------------- directory ---------------- */
let dirQuery = '';

async function renderDirectory() {
  const view = $('#view');
  view.innerHTML = ''
    + '<div class="page-head"><div><h1>Stock Directory</h1><p>Search all ' + Number(Store.stocksCount).toLocaleString() + ' symbols by ticker or company name.</p></div></div>'
    + '<div class="filterbar"><div class="field" style="flex:1;min-width:220px"><label for="dir-q">Search</label>'
    + '<input id="dir-q" type="search" placeholder="e.g. AAPL or Apple\u2026" value="' + esc(dirQuery) + '"></div></div>'
    + '<div class="dir-count" id="dir-count"></div>'
    + '<div class="dir-list" id="dir-list"></div>';

  const input = $('#dir-q');
  let timer = null;
  const run = async () => {
    dirQuery = input.value;
    const q = dirQuery.trim();
    const listEl = $('#dir-list');
    const countEl = $('#dir-count');
    if (!q) {
      const wl = await Store.getWatchlistStocks();
      countEl.textContent = 'Showing your ' + wl.length + ' watchlist stocks \u2014 type to search the full directory.';
      listEl.innerHTML = wl.map((s) => dirRow(s, true)).join('');
      bindDirRows(listEl);
      return;
    }
    try {
      const results = await Store.searchStocks(q, 200);
      countEl.textContent = results.length
        ? 'Showing ' + results.length + ' result' + (results.length === 1 ? '' : 's') + ' for \u201c' + dirQuery.trim() + '\u201d'
        : 'No matches for \u201c' + dirQuery.trim() + '\u201d';
      listEl.innerHTML = results.map((s) => dirRow(s, Store.isOnWatchlist(s.ticker))).join('');
      bindDirRows(listEl);
    } catch (e) {
      countEl.textContent = '';
      listEl.innerHTML = '<div class="panel"><div class="empty">Search failed: ' + esc(e.message) + '</div></div>';
    }
  };
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 160); });
  await run();
  input.focus();
  if (dirQuery) { input.setSelectionRange(input.value.length, input.value.length); }
}

function dirRow(s, onWatchlist) {
  return '<div class="dir-row" data-ticker="' + esc(s.ticker) + '">'
    + '<a class="ticker" href="#/stock/' + encodeURIComponent(s.ticker) + '">' + esc(s.ticker) + '</a>'
    + '<span class="dir-name">' + esc(s.company_name) + (s.sector ? ' \u00b7 ' + esc(s.sector) : '') + '</span>'
    + '<span class="dir-ex">' + esc(s.exchange) + '</span>'
    + (onWatchlist
      ? '<button class="btn small" data-act="remove">On watchlist \u2713</button>'
      : '<button class="btn small primary" data-act="add">Add</button>')
    + '</div>';
}

function bindDirRows(listEl) {
  $$('button[data-act]', listEl).forEach((btn) => {
    btn.addEventListener('click', async () => {
      const row = btn.closest('.dir-row');
      const ticker = row.getAttribute('data-ticker');
      try {
        if (btn.getAttribute('data-act') === 'add') {
          await Store.setOnWatchlist(ticker, true);
          toast(ticker + ' added to watchlist.');
        } else {
          await Store.setOnWatchlist(ticker, false);
          toast(ticker + ' removed from watchlist.');
        }
      } catch (e) { toast('Error: ' + e.message); }
      await renderDirectory();
    });
  });
}

/* ---------------- stock detail ---------------- */
let editingUpdate = null;

async function renderDetail(ticker) {
  const view = $('#view');
  editingUpdate = null;
  let stock, updates;
  try {
    const d = await api.get('/api/stocks/' + encodeURIComponent(ticker));
    stock = d.stock;
    updates = d.updates;
  } catch (e) {
    view.innerHTML = '<div class="panel"><div class="empty">Unknown ticker &ldquo;' + esc(ticker) + '&rdquo;.<br><a class="btn" href="#/directory">Back to directory</a></div></div>';
    return;
  }
  const onWl = Store.isOnWatchlist(ticker);
  const latest = updates[0] || null;
  let mlSignal = null;
  try { mlSignal = await api.get('/api/ml-signal/' + encodeURIComponent(ticker)); }
  catch (e) { mlSignal = null; }

  let html = ''
    + '<div class="detail-head"><div>'
    + '<h1>' + esc(stock.ticker) + '</h1>'
    + '<div class="detail-sub">' + esc(stock.company_name) + ' &middot; ' + esc(stock.sector || 'Unknown sector') + ' &middot; ' + esc(stock.exchange) + '</div>'
    + '</div><div style="display:flex;gap:8px;flex-wrap:wrap">'
    + '<a class="btn small" href="#/update/' + encodeURIComponent(ticker) + '">Log update</a>'
    + '<button class="btn small' + (onWl ? '' : ' primary') + '" id="wl-toggle">' + (onWl ? 'Remove from watchlist' : 'Add to watchlist') + '</button>'
    + '</div></div>';

  html += '<div class="panel"><h2>Latest recommendation</h2>';
  if (latest) {
    html += '<div class="price-row"><span class="price">$' + fmtPrice(latest.price) + '</span>'
      + '<span class="chg ' + chgClass(latest.change_percent) + '">' + fmtChg(latest.change_percent) + '</span>'
      + ratingBadge(latest.rating) + '</div>'
      + '<div class="card-meta"><span>' + fmtDate(latest.date) + '</span></div>'
      + (latest.notes ? '<p class="note-snippet">' + esc(latest.notes) + '</p>' : '');
  } else {
    html += '<div class="empty" style="padding:12px">No updates logged yet for ' + esc(ticker) + '.<br><a class="btn primary small" style="margin-top:10px" href="#/update/' + encodeURIComponent(ticker) + '">Log the first one</a></div>';
  }
  html += '</div>';

  html += '<div class="panel"><h2>ML signal</h2>';
  if (mlSignal && mlSignal.trained) {
    const dirCls = mlSignal.direction === 'up' ? 'pos' : 'neg';
    const arrow = mlSignal.direction === 'up' ? '\u25B2' : '\u25BC';
    html += '<div class="price-row"><span class="chg ' + dirCls + '">' + arrow + ' ' + mlSignal.direction + '</span>'
      + '<span class="card-meta">' + Math.round(mlSignal.confidence * 100) + '% confidence</span>'
      + ratingBadge(mlSignal.suggested_rating) + '</div>'
      + '<p class="note-snippet">The model suggests <strong>' + esc(RATINGS[mlSignal.suggested_rating].label) + '</strong>, '
      + 'learned from patterns in your journal. Trained on ' + mlSignal.samples + ' updates'
      + ((mlSignal.accuracy === null || mlSignal.accuracy === undefined)
        ? '' : ' \u00b7 holdout accuracy ' + Math.round(mlSignal.accuracy * 100) + '%')
      + '. Experimental \u2014 your judgment comes first.</p>';
  } else {
    html += '<div class="empty" style="padding:12px">'
      + esc((mlSignal && mlSignal.reason) || 'ML signal unavailable right now.') + '</div>';
  }
  html += '</div>';

  html += '<div class="panel"><h2>Price history</h2><div class="chart-box">'
    + priceChartSVG(updates.slice().reverse())
    + '</div></div>';

  html += '<div class="panel"><h2>History (' + updates.length + ')</h2><div id="edit-panel"></div>';
  if (!updates.length) {
    html += '<div class="empty" style="padding:12px">Nothing here yet.</div>';
  } else {
    html += '<div class="table-wrap"><table><thead><tr>'
      + '<th>Date</th><th class="num">Price</th><th class="num">Change</th><th>Rating</th>'
      + '<th class="hide-m">Notes</th><th><span class="hide-m">Actions</span></th>'
      + '</tr></thead><tbody>'
      + updates.map((u) => '<tr data-id="' + u.id + '">'
        + '<td>' + fmtDate(u.date) + '</td>'
        + '<td class="num">$' + fmtPrice(u.price) + '</td>'
        + '<td class="num"><span class="chg ' + chgClass(u.change_percent) + '">' + fmtChg(u.change_percent) + '</span></td>'
        + '<td>' + ratingBadge(u.rating) + '</td>'
        + '<td class="hide-m">' + esc(snippet(u.notes, 80)) + '</td>'
        + '<td class="row-actions"><button class="btn small" data-act="edit">Edit</button> '
        + '<button class="btn small danger" data-act="del">Delete</button></td>'
        + '</tr>').join('')
      + '</tbody></table></div>';
  }
  html += '</div>';

  view.innerHTML = html;

  $('#wl-toggle').addEventListener('click', async () => {
    try {
      await Store.setOnWatchlist(ticker, !Store.isOnWatchlist(ticker));
      toast(ticker + (Store.isOnWatchlist(ticker) ? ' added to watchlist.' : ' removed from watchlist.'));
    } catch (e) { toast('Error: ' + e.message); }
    await renderDetail(ticker);
  });

  $$('#view button[data-act]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.closest('tr').getAttribute('data-id');
      if (btn.getAttribute('data-act') === 'del') {
        if (!confirm('Delete this entry? This cannot be undone.')) return;
        try {
          await Store.deleteUpdate(id);
          toast('Entry deleted.');
        } catch (e) { toast('Error: ' + e.message); }
        await renderDetail(ticker);
      } else {
        editingUpdate = updates.find((u) => String(u.id) === String(id)) || null;
        renderEditPanel(ticker);
      }
    });
  });
}

/** Auto-rating: when the Change % field moves, ask the server for the
 *  breakout-bucket rating and pre-fill the rating select (still editable). */
async function autoSuggestRating(chgEl, ratingEl, hintEl) {
  const raw = chgEl.value.trim();
  if (raw === '' || isNaN(Number(raw))) { hintEl.textContent = ''; return; }
  try {
    const s = await api.get('/api/suggest-rating?change_percent=' + encodeURIComponent(raw));
    ratingEl.value = s.rating;
    hintEl.textContent = 'Auto: ' + RATINGS[s.rating].label + ' (' + s.rule + ')';
  } catch (e) { hintEl.textContent = ''; }
}

function renderEditPanel(ticker) {
  const u = editingUpdate;
  const panel = $('#edit-panel');
  if (!u) { panel.innerHTML = ''; return; }
  panel.innerHTML = '<div class="panel" style="background:var(--bg2)"><h2>Edit entry</h2>'
    + '<div class="form-grid">'
    + '<div class="field"><label for="e-date">Date</label><input id="e-date" type="date" value="' + esc(u.date) + '"></div>'
    + '<div class="field"><label for="e-price">Price</label><input id="e-price" type="number" step="0.01" min="0" value="' + esc(u.price) + '"></div>'
    + '<div class="field"><label for="e-chg">Change %</label><input id="e-chg" type="number" step="0.01" value="' + esc(u.change_percent) + '"></div>'
    + '<div class="field"><label for="e-rating">Rating</label><select id="e-rating">'
    + RATING_KEYS.map((k) => '<option value="' + k + '"' + (u.rating === k ? ' selected' : '') + '>' + esc(RATINGS[k].label) + '</option>').join('')
    + '</select><small class="auto-hint" id="e-rating-hint"></small></div>'
    + '<div class="field wide"><label for="e-notes">Notes</label><textarea id="e-notes">' + esc(u.notes) + '</textarea></div>'
    + '</div><div class="form-actions">'
    + '<button class="btn primary" id="e-save">Save changes</button>'
    + '<button class="btn" id="e-cancel">Cancel</button>'
    + '</div></div>';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  $('#e-cancel').addEventListener('click', () => { editingUpdate = null; panel.innerHTML = ''; });
  $('#e-chg').addEventListener('input', () => autoSuggestRating($('#e-chg'), $('#e-rating'), $('#e-rating-hint')));
  $('#e-save').addEventListener('click', async () => {
    try {
      await Store.updateUpdate(u.id, {
        ticker,
        date: $('#e-date').value,
        price: $('#e-price').value,
        change_percent: $('#e-chg').value,
        rating: $('#e-rating').value,
        notes: $('#e-notes').value,
      });
      toast('Entry updated.');
      await renderDetail(ticker);
    } catch (e) { toast('Error: ' + e.message); }
  });
}

/** Hand-drawn SVG line chart of logged prices (oldest -> newest). */
function priceChartSVG(asc) {
  if (!asc.length) return '<div class="chart-empty">No price data yet \u2014 log an update to start the chart.</div>';
  const W = 680, H = 240, P = { l: 54, r: 14, t: 14, b: 30 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const prices = asc.map((u) => u.price);
  let min = Math.min.apply(null, prices), max = Math.max.apply(null, prices);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1;
  min -= pad; max += pad;
  const x = (i) => P.l + (asc.length === 1 ? iw / 2 : (i / (asc.length - 1)) * iw);
  const y = (p) => P.t + ih - ((p - min) / (max - min)) * ih;

  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Price chart">';
  for (let g = 0; g <= 4; g++) {
    const v = min + ((max - min) * g) / 4;
    const gy = y(v);
    s += '<line x1="' + P.l + '" y1="' + gy.toFixed(1) + '" x2="' + (W - P.r) + '" y2="' + gy.toFixed(1) + '" stroke="#1b2436" stroke-width="1"/>'
      + '<text x="' + (P.l - 6) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end" font-size="10" fill="#5d6a84">' + fmtPrice(v) + '</text>';
  }
  const pts = asc.map((u, i) => x(i).toFixed(1) + ',' + y(u.price).toFixed(1)).join(' ');
  s += '<polyline points="' + pts + '" fill="none" stroke="#5b9bff" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>';
  asc.forEach((u, i) => {
    s += '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(u.price).toFixed(1) + '" r="3.4" fill="#0b0f17" stroke="#5b9bff" stroke-width="2">'
      + '<title>' + esc(u.date) + ': $' + fmtPrice(u.price) + '</title></circle>';
  });
  const short = (iso) => { const p = String(iso).split('-'); return p[1] + '/' + p[2] + '/' + String(p[0]).slice(2); };
  const idxs = asc.length === 1 ? [0] : [0, Math.floor((asc.length - 1) / 2), asc.length - 1];
  idxs.forEach((i) => {
    s += '<text x="' + x(i).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="#5d6a84">' + esc(short(asc[i].date)) + '</text>';
  });
  return s + '</svg>';
}

/* ---------------- daily update editor ---------------- */
let editorIdx = 0;

async function renderEditor(preselect) {
  const view = $('#view');
  const wl = await Store.getWatchlistStocks();
  if (!wl.length) {
    view.innerHTML = '<div class="page-head"><div><h1>Log Daily Update</h1></div></div>'
      + '<div class="panel"><div class="empty">Your watchlist is empty, so there is nothing to log yet.<br><a class="btn primary" href="#/directory">Add stocks from the directory</a></div></div>';
    return;
  }
  let idx = 0;
  if (preselect) {
    const found = wl.findIndex((s) => s.ticker === preselect.toUpperCase());
    if (found >= 0) idx = found;
  } else {
    idx = Math.min(editorIdx, wl.length - 1);
  }
  editorIdx = idx;
  const stock = wl[idx];
  const latest = await Store.getLatest(stock.ticker);

  let html = ''
    + '<div class="page-head"><div><h1>Log Daily Update</h1><p>Step through your watchlist one by one, or pick a stock below.</p></div></div>'
    + '<div class="panel"><div class="stepper">'
    + '<button class="btn small" id="step-prev"' + (wl.length < 2 ? ' disabled' : '') + '>\u2039 Prev</button>'
    + '<div class="field" style="flex:1;min-width:180px"><label for="ed-stock">Stock</label><select id="ed-stock">'
    + wl.map((s, i) => '<option value="' + i + '"' + (i === idx ? ' selected' : '') + '>' + esc(s.ticker) + ' \u2014 ' + esc(s.company_name) + '</option>').join('')
    + '</select></div>'
    + '<button class="btn small" id="step-next"' + (wl.length < 2 ? ' disabled' : '') + '>Next \u203a</button>'
    + '<span class="pos">' + (idx + 1) + ' of ' + wl.length + '</span>'
    + '</div>'
    + (latest ? '<p style="color:var(--muted);font-size:0.88rem;margin:0 0 4px">Latest: $' + fmtPrice(latest.price) + ' (' + fmtChg(latest.change_percent) + ') on ' + fmtDate(latest.date) + ' \u2014 ' + ratingBadge(latest.rating) + '</p>' : '<p style="color:var(--muted);font-size:0.88rem;margin:0 0 4px">No updates logged yet for ' + esc(stock.ticker) + '.</p>')
    + '<div class="form-grid" style="margin-top:10px">'
    + '<div class="field"><label for="ed-date">Date</label><input id="ed-date" type="date" value="' + todayISO() + '"></div>'
    + '<div class="field"><label for="ed-price">Price</label><input id="ed-price" type="number" step="0.01" min="0" placeholder="e.g. 232.50"></div>'
    + '<div class="field"><label for="ed-chg">Change %</label><input id="ed-chg" type="number" step="0.01" placeholder="e.g. 1.25"></div>'
    + '<div class="field"><label for="ed-rating">Rating</label><select id="ed-rating">'
    + RATING_KEYS.map((k) => '<option value="' + k + '">' + esc(RATINGS[k].label) + '</option>').join('')
    + '</select><small class="auto-hint" id="ed-rating-hint"></small><small class="auto-hint" id="ed-ml-hint"></small></div>'
    + '<div class="field wide"><label for="ed-notes">Notes</label><textarea id="ed-notes" placeholder="Why this rating? Key observations\u2026"></textarea></div>'
    + '</div><div class="form-actions">'
    + '<button class="btn primary" id="ed-save">Save update</button>'
    + '<a class="btn" href="#/stock/' + encodeURIComponent(stock.ticker) + '">View history</a>'
    + '</div></div>';

  html += '<div class="panel"><h2>Bulk data (CSV)</h2>'
    + '<p style="color:var(--muted);font-size:0.9rem;margin-top:0">Import many updates at once, or export everything you have logged. Columns: <code>ticker,date,price,change_percent,rating,notes</code>. Ratings: ' + RATING_KEYS.join(', ') + '.</p>'
    + '<div class="form-actions" style="margin-top:0">'
    + '<button class="btn" id="csv-template">Download template</button>'
    + '<button class="btn" id="csv-export">Export updates</button>'
    + '<button class="btn" id="csv-import-btn">Import CSV\u2026</button>'
    + '<input type="file" id="csv-import" accept=".csv,text/csv" hidden>'
    + '</div></div>';

  view.innerHTML = html;

  const go = (d) => {
    editorIdx = (idx + d + wl.length) % wl.length;
    location.hash = '#/update';
    renderEditor(null);
  };
  $('#step-prev').addEventListener('click', () => go(-1));
  $('#step-next').addEventListener('click', () => go(1));
  $('#ed-stock').addEventListener('change', (e) => { editorIdx = Number(e.target.value); renderEditor(null); });
  $('#ed-chg').addEventListener('input', () => autoSuggestRating($('#ed-chg'), $('#ed-rating'), $('#ed-rating-hint')));

  try {
    const sig = await api.get('/api/ml-signal/' + encodeURIComponent(stock.ticker));
    const mh = $('#ed-ml-hint');
    if (mh) {
      mh.textContent = sig.trained
        ? 'ML: predicts ' + sig.direction + ' (' + Math.round(sig.confidence * 100) + '%) \u2192 suggests '
          + RATINGS[sig.suggested_rating].label + ' \u00b7 trained on ' + sig.samples + ' updates'
        : 'ML: ' + sig.reason;
    }
  } catch (e) { /* leave the ML hint blank */ }

  $('#ed-save').addEventListener('click', async () => {
    try {
      const saved = await Store.saveUpdate({
        ticker: stock.ticker,
        date: $('#ed-date').value,
        price: $('#ed-price').value,
        change_percent: $('#ed-chg').value,
        rating: $('#ed-rating').value,
        notes: $('#ed-notes').value,
      });
      toast(saved.updated
        ? 'Updated entry for ' + stock.ticker + ' on ' + saved.update.date + '.'
        : 'Saved update for ' + stock.ticker + '.');
      editorIdx = (idx + 1) % wl.length; // step to the next stock
      await renderEditor(null);
    } catch (e) { toast('Error: ' + e.message); }
  });

  $('#csv-template').addEventListener('click', () => { window.location.href = '/api/template'; });
  $('#csv-export').addEventListener('click', async () => {
    try {
      const meta = await api.get('/api/meta');
      if (!meta.updates) { toast('Nothing to export yet.'); return; }
      window.location.href = '/api/export';
    } catch (e) { toast('Error: ' + e.message); }
  });
  $('#csv-import-btn').addEventListener('click', () => $('#csv-import').click());
  $('#csv-import').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const r = await Store.importCSV(f);
      let msg = 'Imported ' + r.imported + ' new, updated ' + r.updated + ', skipped ' + r.skipped + '.';
      if (r.errors.length) msg += ' First issue: ' + r.errors[0];
      toast(msg);
    } catch (err) { toast('Error: ' + err.message); }
    e.target.value = '';
    await renderEditor(null);
  });
}

/* ---------------- header search ---------------- */
function initSearch() {
  const input = $('#global-search');
  const box = $('#search-results');
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (!q) { box.hidden = true; box.innerHTML = ''; return; }
      try {
        const res = await Store.searchStocks(q, 8);
        box.innerHTML = res.length
          ? res.map((s) => '<a href="#/stock/' + encodeURIComponent(s.ticker) + '"><span><span class="sr-ticker">' + esc(s.ticker) + '</span> <span class="sr-name">' + esc(s.company_name) + '</span></span><span class="sr-name">' + esc(s.exchange) + '</span></a>').join('')
          : '<a href="#/directory"><span class="sr-ticker">No matches</span><span class="sr-name">try the directory</span></a>';
        box.hidden = false;
      } catch (e) { box.hidden = true; }
    }, 140);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      dirQuery = input.value;
      box.hidden = true;
      input.blur();
      location.hash = '#/directory';
      if ((location.hash || '') === '#/directory') renderDirectory();
    } else if (e.key === 'Escape') {
      box.hidden = true;
      input.blur();
    }
  });
  box.addEventListener('click', () => { box.hidden = true; input.value = ''; });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrap')) { box.hidden = true; }
  });
}

/* ---------------- paper trading ---------------- */
function fmtSignedMoney(v) {
  if (v === null || v === undefined || !isFinite(Number(v))) return '\u2014';
  const n = Number(v);
  const cls = n > 0 ? 'pos' : (n < 0 ? 'neg' : 'flat');
  const sign = n > 0 ? '+' : (n < 0 ? '\u2212' : '');
  return '<span class="chg ' + cls + '">' + sign + '$'
    + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    + '</span>';
}

async function renderPaper() {
  const view = $('#view');
  const { positions, totals } = await Store.getPaperPortfolio();
  const trades = await Store.getPaperTrades();

  let html = '<div class="page-head"><div><h1>Paper Trading</h1>'
    + '<p>Practice with fake money. Log paper buys and sells; positions are valued '
    + 'at the latest price you logged for each stock. Nothing here is real money.</p></div></div>';

  html += '<div class="stats">'
    + statHtml('$' + fmtPrice(totals.market_value), 'Market value')
    + statHtml(fmtSignedMoney(totals.unrealized_pl), 'Unrealized P&L')
    + statHtml(fmtSignedMoney(totals.realized_pl), 'Realized P&L')
    + '</div>';

  html += '<div class="panel"><h2>Positions</h2>';
  if (!positions.length) {
    html += '<div class="empty" style="padding:12px">No paper trades yet \u2014 log your first trade below.</div>';
  } else {
    html += '<div class="table-wrap"><table><thead><tr>'
      + '<th>Ticker</th><th class="num">Shares</th><th class="num">Avg cost</th>'
      + '<th class="num">Latest price</th><th class="num">Market value</th>'
      + '<th class="num">Unrealized</th><th class="num">Realized</th>'
      + '</tr></thead><tbody>'
      + positions.map((p) => '<tr>'
        + '<td><a href="#/stock/' + encodeURIComponent(p.ticker) + '"><strong>' + esc(p.ticker) + '</strong></a></td>'
        + '<td class="num">' + esc(String(p.shares)) + '</td>'
        + '<td class="num">$' + fmtPrice(p.avg_cost) + '</td>'
        + '<td class="num">' + (p.latest_price === null ? '\u2014 (no price logged)' : '$' + fmtPrice(p.latest_price)) + '</td>'
        + '<td class="num">' + (p.market_value === null ? '\u2014' : '$' + fmtPrice(p.market_value)) + '</td>'
        + '<td class="num">' + fmtSignedMoney(p.unrealized_pl)
        + (p.unrealized_pct === null ? '' : ' <span class="chg ' + chgClass(p.unrealized_pct) + '">(' + fmtChg(p.unrealized_pct) + ')</span>') + '</td>'
        + '<td class="num">' + fmtSignedMoney(p.realized_pl) + '</td>'
        + '</tr>').join('')
      + '</tbody></table></div>';
  }
  html += '</div>';

  html += '<div class="panel"><h2>Log paper trade</h2>'
    + '<div class="form-grid">'
    + '<div class="field"><label for="pt-ticker">Ticker</label><input id="pt-ticker" type="text" placeholder="e.g. AAPL" style="text-transform:uppercase"></div>'
    + '<div class="field"><label for="pt-date">Date</label><input id="pt-date" type="date" value="' + todayISO() + '"></div>'
    + '<div class="field"><label for="pt-side">Side</label><select id="pt-side"><option value="buy">Buy</option><option value="sell">Sell</option></select></div>'
    + '<div class="field"><label for="pt-shares">Shares</label><input id="pt-shares" type="number" step="any" min="0" placeholder="e.g. 10"></div>'
    + '<div class="field"><label for="pt-price">Price</label><input id="pt-price" type="number" step="0.01" min="0" placeholder="e.g. 232.50"></div>'
    + '<div class="field wide"><label for="pt-notes">Notes</label><textarea id="pt-notes" placeholder="Why this paper trade?\u2026"></textarea></div>'
    + '</div><div class="form-actions">'
    + '<button class="btn primary" id="pt-save">Save paper trade</button>'
    + '</div></div>';

  html += '<div class="panel"><h2>Trade history (' + trades.length + ')</h2>';
  if (!trades.length) {
    html += '<div class="empty" style="padding:12px">Nothing here yet.</div>';
  } else {
    html += '<div class="table-wrap"><table><thead><tr>'
      + '<th>Date</th><th>Ticker</th><th>Side</th><th class="num">Shares</th>'
      + '<th class="num">Price</th><th class="hide-m">Notes</th><th><span class="hide-m">Actions</span></th>'
      + '</tr></thead><tbody>'
      + trades.map((t) => '<tr data-id="' + t.id + '">'
        + '<td>' + fmtDate(t.date) + '</td>'
        + '<td><strong>' + esc(t.stock_ticker) + '</strong></td>'
        + '<td>' + (t.side === 'buy'
            ? '<span class="chg pos">Buy</span>' : '<span class="chg neg">Sell</span>') + '</td>'
        + '<td class="num">' + esc(String(t.shares)) + '</td>'
        + '<td class="num">$' + fmtPrice(t.price) + '</td>'
        + '<td class="hide-m">' + esc(snippet(t.notes, 60)) + '</td>'
        + '<td class="row-actions"><button class="btn small danger" data-act="del">Delete</button></td>'
        + '</tr>').join('')
      + '</tbody></table></div>';
  }
  html += '</div>';

  view.innerHTML = html;

  $('#pt-save').addEventListener('click', async () => {
    try {
      await Store.addPaperTrade({
        ticker: $('#pt-ticker').value,
        date: $('#pt-date').value,
        side: $('#pt-side').value,
        shares: $('#pt-shares').value,
        price: $('#pt-price').value,
        notes: $('#pt-notes').value,
      });
      toast('Paper trade saved.');
      await renderPaper();
    } catch (e) { toast('Error: ' + e.message); }
  });

  view.querySelectorAll('tr[data-id] [data-act="del"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this paper trade? Positions will be recalculated.')) return;
      try {
        await Store.deletePaperTrade(btn.closest('tr').getAttribute('data-id'));
        toast('Paper trade deleted.');
        await renderPaper();
      } catch (e) { toast('Error: ' + e.message); }
    });
  });
}

/* ---------------- research: external analyst-rating sources ---------------- */
const RESEARCH_LINKS = [
  { name: 'Yahoo Finance', desc: 'Analyst buy/hold/sell breakdowns, upgrades and downgrades, market news.', url: 'https://finance.yahoo.com/' },
  { name: 'MarketBeat', desc: 'Daily feed of analyst rating changes and price targets.', url: 'https://www.marketbeat.com/ratings/' },
  { name: 'TipRanks', desc: 'Analyst ratings plus each analyst\u2019s track record.', url: 'https://www.tipranks.com/' },
  { name: 'Nasdaq', desc: 'Market activity, analyst research and stock news.', url: 'https://www.nasdaq.com/' },
  { name: 'MarketWatch', desc: 'Market news, analysis and investing insight.', url: 'https://www.marketwatch.com/' },
  { name: 'Finviz', desc: 'Stock screener, heat maps and charts.', url: 'https://finviz.com/' },
];

async function renderResearch() {
  const view = $('#view');
  view.innerHTML = '<div class="page-head"><div><h1>Stock Recommendations According to News</h1>'
    + '<p>Analyst ratings and market news from external sources. Links open in a new tab \u2014 '
    + 'read what the analysts say, then log your own rating in your journal.</p></div></div>'
    + '<div class="panel">'
    + RESEARCH_LINKS.map((l) => '<div class="dir-row">'
      + '<div><div class="dir-ticker">' + esc(l.name) + '</div>'
      + '<div class="company">' + esc(l.desc) + '</div></div>'
      + '<a class="btn small" href="' + esc(l.url) + '" target="_blank" rel="noopener">Open \u2197</a>'
      + '</div>').join('')
    + '</div>';
}

/* ---------------- router ---------------- */
function setActiveNav(page) {
  $$('[data-nav]').forEach((a) => a.classList.toggle('active', a.getAttribute('data-nav') === page));
}

async function router() {
  const hash = location.hash || '#/dashboard';
  const parts = hash.replace(/^#\/?/, '').split('/');
  const page = parts[0] || 'dashboard';
  const param = parts[1] ? decodeURIComponent(parts[1]).toUpperCase() : null;
  const box = $('#search-results');
  if (box) box.hidden = true;

  try {
    if (page === 'dashboard') { setActiveNav('dashboard'); await renderDashboard(); }
    else if (page === 'directory') { setActiveNav('directory'); await renderDirectory(); }
    else if (page === 'stock' && param) { setActiveNav(''); await renderDetail(param); }
    else if (page === 'update') { setActiveNav('update'); editorIdx = 0; await renderEditor(param); }
    else if (page === 'research') { setActiveNav('research'); renderResearch(); }
    else if (page === 'paper') { setActiveNav('paper'); await renderPaper(); }
    else { setActiveNav('dashboard'); await renderDashboard(); }
  } catch (e) {
    $('#view').innerHTML = '<div class="panel"><div class="empty">Something went wrong:<br>' + esc(e.message) + '</div></div>';
  }
  window.scrollTo(0, 0);
}

/* ---------------- boot ---------------- */
async function boot() {
  try {
    await Store.init();
  } catch (e) {
    $('#view').innerHTML = '<div class="panel"><div class="empty"><strong>Could not start.</strong><br>' + esc(e.message) + '</div></div>';
    return;
  }
  initSearch();
  window.addEventListener('hashchange', router);
  await router();
}

document.addEventListener('DOMContentLoaded', boot);
