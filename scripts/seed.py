#!/usr/bin/env python3
"""Build data/stocks.json from the official Nasdaq Trader symbol files.

Usage:
    python3 scripts/seed.py [nasdaqlisted.txt] [otherlisted.txt]

Inputs are the pipe-delimited files published at:
    https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
    https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
(Download them with curl first; see README.md.)

Output: data/stocks.json — an array of
    {"ticker": ..., "company_name": ..., "sector": "", "exchange": ...}

Notes:
- Test issues are skipped.
- "/" in symbols is normalized to "." (e.g. BRK/B -> BRK.B) so tickers
  match common data-provider conventions.
- otherlisted "Exchange" codes are mapped: N -> NYSE, A -> AMEX, P -> ARCA,
  Z -> BATS; anything else is kept as-is.
- The files carry no sector data, so sector is "" (unknown) for these rows.
  Canonical names/sectors for the starter watchlist live in
  data/watchlist.json and are merged in at app runtime.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Trailing security-type descriptors to strip from company names, e.g.
# "Agilent Technologies, Inc. Common Stock" -> "Agilent Technologies, Inc."
# Applied repeatedly so stacked suffixes ("... Inc. New Common Stock") collapse.
SUFFIX_RE = re.compile(
    r"(?:[\s.,()])+(?:"
    r"Common Stock"
    r"|Ordinary Shares?"
    r"|Class [A-Z0-9]+(?: [A-Z0-9]+)?"
    r"|American Depositary Shares?"
    r"|ADS"
    r"|Warrants?"
    r"|Units?"
    r"|Rights?"
    r"|Preferred(?: Stock)?(?: Series [A-Z0-9]+)?"
    r"|Depositary Shares?"
    r"|New"
    r"|-"
    r")$",
    re.IGNORECASE,
)

EXCHANGE_MAP = {"N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS"}


def clean_name(name: str) -> str:
    name = name.strip()
    prev = None
    while prev != name:
        prev = name
        name = SUFFIX_RE.sub("", name).strip()
    # drop a dangling "(" left behind by suffix stripping, e.g.
    # "Oshkosh Corporation (Holding Company)Common Stock"
    if name.count("(") > name.count(")"):
        name = name.rsplit("(", 1)[0].strip()
    return name or prev


def norm_ticker(sym: str) -> str:
    return sym.strip().replace("/", ".")


def parse_nasdaqlisted(path: Path, out: dict):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[1:]:  # skip header
        if not line.strip() or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        symbol, name, _cat, test, _fin, _lot, _etf, _next = parts[:8]
        if test.strip().upper() == "Y":
            continue
        ticker = norm_ticker(symbol)
        if not ticker or ticker in out:
            continue
        out[ticker] = {
            "ticker": ticker,
            "company_name": clean_name(name),
            "sector": "",
            "exchange": "NASDAQ",
        }


def parse_otherlisted(path: Path, out: dict):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[1:]:  # skip header
        if not line.strip() or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        symbol, name, exch, _cqs, _etf, _lot, test, _nasdaq = parts[:8]
        if test.strip().upper() == "Y":
            continue
        ticker = norm_ticker(symbol)
        if not ticker or ticker in out:
            continue
        out[ticker] = {
            "ticker": ticker,
            "company_name": clean_name(name),
            "sector": "",
            "exchange": EXCHANGE_MAP.get(exch.strip().upper(), exch.strip().upper() or "OTHER"),
        }


def main() -> None:
    nasdaq_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "raw_nasdaqlisted.txt"
    other_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA / "raw_otherlisted.txt"
    stocks: dict = {}
    parse_nasdaqlisted(nasdaq_path, stocks)
    parse_otherlisted(other_path, stocks)
    ordered = [stocks[k] for k in sorted(stocks)]
    out_path = DATA / "stocks.json"
    out_path.write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(ordered)} symbols -> {out_path}")


if __name__ == "__main__":
    main()
