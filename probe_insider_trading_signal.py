"""
Hundred-and-third entry: NSE insider trading (SEBI PIT) disclosures as a
"follow the insider" signal. Pre-registered here BEFORE any signal was
scored against a return, matching this project's own established
convention (Entries 82-102).

WHY THIS IS GENUINELY NEW: every prior disclosure-driven signal in this
project (bulk/block deals, Hundredth entry) comes from a CLIENT's large
trade - an anonymous large buyer/seller, not necessarily connected to the
company. SEBI's Prohibition of Insider Trading (PIT) regulations instead
force a company's own PROMOTERS, DIRECTORS and KEY MANAGERIAL PERSONNEL to
publicly disclose every trade in their own company's stock. This is the
classic "informed insider" literature (Seyhun 1986; Jeng, Metrick &
Zeckhauser 2003): insider purchases are repeatedly found to predict higher
subsequent returns; insider sales are a weaker, often null, predictor
(routine diversification/liquidity, not necessarily a negative signal) -
an asymmetry this entry pre-registers as a named prediction, not something
discovered after looking at the data.

DATA: `https://www.nseindia.com/api/corporates-pit?index=equities&from_
date=DD-MM-YYYY&to_date=DD-MM-YYYY`, no paid subscription - reachable with
the identical plain `requests.Session` + one warm-up GET to the report
page this project's bulk-deals fetcher already established (Hundredth
entry). Confirmed live before building anything: JSON mode has NO per-call
row cap (a 7-year span returned 138,143 full rows, not silently capped at
some round number the way the bulk-deals JSON mode was capped at 70) but
DOES have a response-size ceiling - an 11-year single request (2015-2026)
returned a 193MB payload that truncated mid-string and failed to parse, so
this fetches ONE CALENDAR YEAR per request (mirroring the bulk-deals
fetcher's own 1-year chunking, for the same "stay under an unknown
response-size wall" reason, not because of any PER-REQUEST cap this
specific endpoint turned out to have). Also confirmed live: this
environment's available data for 2026 stops at the "02-May-2026" broadcast
- a request for Jun-Sep 2026 returns a genuinely empty result (checked, not
assumed), so the usable window ends there, not at today's system date.

A REAL DATA-QUALITY TRAP, found and disposed of before any signal touched
a return (the same kind of due-diligence check Entry 100 did for Indian
comma-grouping in CSV quantities): 2016 alone returns 45,609 rows, with
HDFCBANK and HDFC contributing 9,699 and 8,644 of them - two to three
orders of magnitude more than any other large-cap in the same year. These
are not informed trades: `tdpTransactionType` for the flood is dominated
by thousands of individually-disclosed ESOP exercises under the OLD
(pre-2019-amendment), much lower PIT disclosure threshold, filed under
`personCategory == "Employees/Designated Employees"`. Routine, scheduled
option exercises by rank-and-file staff carry none of the information
Seyhun's literature is about, and would swamp any real signal with
compensation noise. FILTER, decided before any return was computed: keep
only `tdpTransactionType in {"Buy", "Sell"}` (drops Pledge/Pledge Revoke/
Pledge Invoke/ESOP-labelled-as-other/"-", none of which are open-market
trades) and `personCategory` restricted to two disjoint, named insider
types - PROMOTER = {"Promoters", "Promoter Group"}; OFFICER = {"Director",
"Key Managerial Personnel"} - deliberately EXCLUDING "Employees/Designated
Employees" (the ESOP-flood category just found), "Immediate relative"
(ambiguous whose informational position it reflects) and "Other"/"-"
(unlabelled). This costs some real insider trades (a director's spouse's
disclosed trade, for instance) to keep the two kept buckets clean of the
specific noise source actually found in this data, the same "a clearly
disclosed exclusion beats a silently contaminated signal" principle the
Nifty-reconstitution entry's 8 excluded events and the bulk-deals entry's
comma-parsing fix both already established.

EVENT DATE: the `date` field ("BROADCASTE DATE AND TIME") - when NSE
actually makes the disclosure public, not `intimDt` (intimation to the
company, which the market does not see) or `acqfromDt`/`acqtoDt` (the
underlying transaction date, which can predate public disclosure by the
law's own permitted window and would be a look-ahead if used as the
signal date here).

SIGNAL: for a stock with >=1 Buy-or-Sell PIT disclosure of a given insider
type on day t, net by VALUE (`secVal`, rupee transaction value - a more
economically meaningful magnitude across differently-priced stocks than
raw share count, unlike bulk deals' own raw-quantity netting, itself a
free, pre-registered, deliberately-different choice since this entry's
signal isn't identical to that one): BUY if buy value > sell value that
day, SELL if sell value > buy value, tie excluded. Direction fixed in
advance, per the "follow the insider" thesis this strategy is actually
sold as: BUY => higher forward return, SELL => lower forward return.
Reported regardless of sign, including the asymmetry the literature itself
predicts (buys more informative than sells).
FORWARD RETURN: lag-1 fill (this project's now-standard convention, Fifty-
ninth entry onward) - enter at the close of t+1, exit at the close of
t+1+h, h in {1, 5, 10, 21} trading days. Every piece of this - fwd_return,
eligible_dates, nse_calendar, load_closes, event_test, apply_rule,
write_atomic/write_text_atomic - is REUSED UNCHANGED from
probe_bulk_deals_signal.py, not reimplemented, per this project's own
established practice (the Hundred-and-second entry already did the same
reuse for a different new signal).
UNIVERSE: every distinct NSE symbol with >= MIN_EVENTS same-direction,
same-insider-type disclosures in the fetched window AND a fetchable Yahoo
adjusted-close series. Today's constituents only (yfinance has no
historical-membership or delisted-name API - the same unresolved
survivorship gap every prior entry in this project has carried).
STATISTIC: pooled mean forward return across every (stock, event-date)
pair, against event_test()'s matched-stock random-day null (Entry 98's
subset_control, applied per stock, pooled across stocks) - identical
methodology to the Hundredth entry's bulk/block-deals test.
REGISTERED: {PROMOTER, OFFICER} x {BUY, SELL} x 4 horizons = 16 tests.
DECISION RULE (fixed in advance, apply_rule() unchanged): a (type, side, h)
cell advances only if p < 0.05, both halves of the window agree in sign
with the pooled mean, AND the gross mean return clears the 0.25% round-trip
NSE cost (0.125%/leg, Entry 73) in the BUY => long / SELL => short
direction. A pass is a hypothesis for the honest multiple-comparisons
ledger, not a finding on its own.
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import requests

from probe_bulk_deals_signal import (
    COST_RT,
    DRAWS,
    HORIZONS,
    MIN_EVENTS,  # event_test()'s own internal data-quality floor (8) - unchanged, not this entry's universe threshold
    apply_rule,
    event_test,
    load_closes,
    net_events,
    write_atomic,
    write_text_atomic,
)

# Universe-membership threshold (how many same-direction disclosures a symbol needs to enter the test) -
# 15, matching the bulk-deals entry's own choice for its "data-rich" bucket (its MIN_EVENTS_BY_TYPE["bulk_deals"]),
# not a new number invented for this entry. At the imported MIN_EVENTS=8 floor the union universe is 962 distinct
# symbols, most of them obscure small/mid-caps with only a handful of lifetime disclosures (and often delisted,
# confirmed live: a first full run spent its first several minutes almost entirely on yfinance "possibly delisted"
# retries working through the alphabet) - raising to 15 drops the union to 604 while keeping every cell's n_events
# comfortably in the thousands (promoter BUY alone: 16,056 events across 391 symbols).
UNIVERSE_MIN_EVENTS = 15

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_HERE, "insider_trading_cache.csv.gz")
CHECKED = os.path.join(_HERE, "insider_trading_checked.txt")  # "YYYY" years already fetched
API = "https://www.nseindia.com/api/corporates-pit"
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FIRST_YEAR = 2016

PROMOTER_CATS = {"Promoters", "Promoter Group"}
OFFICER_CATS = {"Director", "Key Managerial Personnel"}
INSIDER_TYPES = ("promoter", "officer")
CSV_COLS = ["date", "symbol", "buy_sell", "qty", "deal_type"]


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": REFERER})
    s.get(REFERER, timeout=20)
    return s


def _category_to_type(cat):
    if cat in PROMOTER_CATS:
        return "promoter"
    if cat in OFFICER_CATS:
        return "officer"
    return None


MARKET_MODE = {"Buy": "Market Purchase", "Sell": "Market Sale"}  # a second filter, see parse_rows()


def parse_rows(rows):
    """Raw `data` list from the JSON response -> the bulk-deals schema (date, symbol, buy_sell, qty, deal_type),
    filtered to real OPEN-MARKET Buy/Sell disclosures from the two named insider categories. `secVal` is the
    transaction's rupee value, already comma-free in this endpoint's JSON mode (unlike the bulk-deals CSV mode's
    Indian-comma-grouped quantities) - confirmed on live sample rows before trusting a plain pd.to_numeric here.

    SECOND FILTER, added after the first real backtest run showed a candidate worth scrutinizing: `acqMode`
    ("Market Purchase" / "Market Sale" / "Preferential Offer" / "Gift" / "Inter-se-Transfer" / "ESOP" / "Conversion
    of security" / "Bonus" / "Scheme of Amalgamation..." / "Off Market" / "Public Right" / etc) is NOT the same
    thing as `tdpTransactionType` (Buy/Sell) - a "Buy" transaction type can still be a Preferential Offer, a Gift,
    an ESOP exercise, or a related-party Inter-se-Transfer, none of which reflect an insider's own discretionary
    decision to go into the market and buy at today's price the way academic literature (Seyhun) means by "insider
    purchase". Checked live on a sample quarter before trusting the first result: only 1,416/1,683 (84%) of
    promoter BUY rows were actually `acqMode == "Market Purchase"`; the 16% leftover mode mix (Preferential Offer,
    Off Market, Inter-se-Transfer, Conversion, Gift, Public Right, Bonus, Scheme of Amalgamation) would have diluted
    a genuine market-timing signal with non-discretionary noise the same way the Employees/Designated Employees
    ESOP flood would have. Restricting to `acqMode in {"Market Purchase"}` for Buy and `{"Market Sale"}` for Sell
    keeps only genuine open-market trades."""
    out = []
    for r in rows:
        itype = _category_to_type(r.get("personCategory"))
        side = r.get("tdpTransactionType")
        if itype is None or side not in ("Buy", "Sell") or r.get("acqMode") != MARKET_MODE[side]:
            continue
        out.append(dict(
            date=(r.get("date") or "")[:11],  # "02-May-2026 16:46" -> "02-May-2026"
            symbol=r.get("symbol"),
            buy_sell=side.upper(),
            qty=r.get("secVal"),
            deal_type=itype,
        ))
    df = pd.DataFrame(out, columns=CSV_COLS)
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    return df.dropna(subset=["date", "symbol", "qty"])


def fetch(first_year=FIRST_YEAR, last_year=None, sleep=0.3, save_every=1):
    """One request per calendar year via the JSON endpoint - an 11-year single request was confirmed live to
    truncate (see module docstring), so this chunks the same way the bulk-deals fetcher chunks its own >1-year-
    capped CSV endpoint, for an analogous (but not identical) reason. Resumable: a "YYYY" key is only marked
    checked after a successful parse."""
    import datetime as dt
    last_year = last_year or dt.date.today().year
    checked = set(open(CHECKED).read().split()) if os.path.exists(CHECKED) else set()
    old = pd.read_csv(CACHE, parse_dates=["date"]) if os.path.exists(CACHE) else pd.DataFrame(columns=CSV_COLS)
    new, done = [], 0
    try:
        s = new_session()
    except requests.RequestException as e:
        print(f"initial session setup failed: {type(e).__name__}, aborting fetch (nothing checked yet, safe to rerun)")
        return

    def save():
        write_atomic(pd.concat([old] + new).drop_duplicates(), CACHE)
        write_text_atomic("\n".join(sorted(checked)), CHECKED)

    for yr in range(first_year, last_year + 1):
        key = str(yr)
        if key in checked:
            continue
        f, t = f"01-01-{yr}", f"31-12-{yr}" if yr < dt.date.today().year else dt.date.today().strftime("%d-%m-%Y")
        url = f"{API}?index=equities&from_date={f}&to_date={t}"
        try:
            r = s.get(url, timeout=60)
            if r.status_code == 200:
                parsed = parse_rows(json.loads(r.text)["data"])
                new.append(parsed)
                checked.add(key)
                print(f"{key}: {len(parsed)} rows (promoter/officer Buy/Sell only), "
                      f"{parsed.date.dt.date.nunique()} trading days", flush=True)
            elif r.status_code in (401, 403):
                s = new_session()  # session likely expired; refresh and retry this key next pass
            else:
                print(f"{key}: HTTP {r.status_code}, will retry on rerun")
        except (requests.RequestException, ValueError, KeyError) as e:
            # KeyError: a 200-OK response whose JSON doesn't have a "data" key (e.g. a transient NSE error page
            # served with a 200 status) - same log-and-retry-on-rerun handling as every other exception type here.
            print(f"{key}: {type(e).__name__}, will retry on rerun")
            time.sleep(2)
        done += 1
        if done % save_every == 0:
            save()
        time.sleep(sleep)
    save()
    print("fetch done")


def load_deals():
    df = pd.read_csv(CACHE, parse_dates=["date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    return df.dropna(subset=["qty"])


def build_universe(deals, insider_type, side, min_events=UNIVERSE_MIN_EVENTS):
    ev = net_events(deals, insider_type)
    ev = ev[ev == side]
    counts = ev.groupby(level="symbol").size()
    syms = sorted(counts[counts >= min_events].index)
    events_by_symbol = {sym: list(ev.xs(sym, level="symbol").index) for sym in syms}
    return syms, events_by_symbol


def run(period="max", seed=1, min_events=UNIVERSE_MIN_EVENTS):
    deals = load_deals()
    rng = np.random.default_rng(seed)
    cells = {}
    all_syms = set()
    for itype in INSIDER_TYPES:
        for side in ("BUY", "SELL"):
            syms, events_by_symbol = build_universe(deals, itype, side, min_events)
            cells[(itype, side)] = (syms, events_by_symbol)
            all_syms |= set(syms)
    print(f"fetching adjusted closes for {len(all_syms)} distinct symbols (union across all 4 cells)...")
    closes = load_closes(sorted(all_syms), period)
    rows = []
    for (itype, side), (syms, events_by_symbol) in cells.items():
        if not syms:
            print(f"{itype:9s} {side:4s}: no symbol reaches min_events={min_events}, skipped")
            continue
        for h in HORIZONS:
            res = event_test(closes, events_by_symbol, h, rng)
            rows.append(dict(deal_type=itype, side=side, h=h, **res))
            h1, h2 = res["halves"]
            print(f"{itype:9s} {side:4s} h={h:2d}  n_events={res['n_events']:4d} n_stocks={res['n_stocks']:3d} "
                  f"actual={res['actual']:+.4%} p={res['p']:.4f} halves=({h1:+.4%},{h2:+.4%})")
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--period", default="max")
    ap.add_argument("--min-events", type=int, default=UNIVERSE_MIN_EVENTS)
    a = ap.parse_args()
    if a.fetch:
        fetch()
        return
    pd.set_option("display.width", 200)
    res = run(period=a.period, min_events=a.min_events)
    res.to_csv(os.path.join(_HERE, "insider_trading_signal_results.csv"), index=False)
    ruled = apply_rule(res, cost=COST_RT)
    print("\n=== decision rule ===")
    print(ruled[["deal_type", "side", "h", "n_events", "n_stocks", "actual", "p", "advance"]].to_string(index=False))


if __name__ == "__main__":
    main()
