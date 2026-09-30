"""
Hundredth entry: NSE Bulk Deals / Block Deals as a "follow the large disclosed
trade" signal. Pre-registered here BEFORE any signal was scored against a
return, matching this project's own established convention (Entries 82-99).

WHY THIS IS GENUINELY NEW: every prior signal in this project comes from price,
volume, options open interest, delivery percentage, dividends, or the calendar.
NSE separately publishes two disclosure-driven reports never touched here:
BULK DEALS (a single client trades >= 0.5% of a company's listed shares in one
day, any client) and BLOCK DEALS (a single trade >= 5,00,000 shares or >= Rs 5
crore, executed in the special block window). Both reveal a large, identifiable
trade the day it happens - the retail "follow the smart money" strategy this
project has never tested with real data. Genuinely different mechanic from the
OI/delivery signals (Entries 82/85): those are continuous daily statistics
computed for every stock every day; bulk/block deals are a SPARSE, IRREGULAR
event that hits a different, mostly-small/mid-cap subset of the market on any
given day, so the natural test is an EVENT STUDY (matched-stock random-day
null), not a daily cross-sectional rank IC across a fixed universe (the Eighty-
second/-third/-fourth/-fifth/-sixth entries' design does not fit here: most
stock-days have no bulk/block deal at all, so a cross-sectional panel would be
almost entirely NaN).

DATA: `https://www.nseindia.com/api/historicalOR/bulk-block-short-deals`
(optionType=bulk_deals | block_deals), no paid subscription - confirmed
reachable back to at least 2016 with a plain `requests.Session` warmed up by
one GET to the report page first (sets the cookies the API checks; the direct
nsearchives.nseindia.com CSVs used by this project's delivery/OI/F&O fetchers
only ever serve the CURRENT day for this specific report, confirmed by a live
probe before building anything - the historical archive genuinely needs this
different, session-gated endpoint). TWO RESPONSE MODES FOUND, ONE OF THEM A
TRAP: the plain JSON mode silently caps every response at 70 rows regardless
of the requested date range (confirmed live: a full-year request returned the
SAME 70 rows as a single busy day, covering only the first 3 trading days of
the year) - and by 2025-2026 a single day's real row count is often already
>70 (daily bulk-deal counts grew roughly 3x over the decade: 2016 ~25/day,
2025 ~75/day, confirmed by comparing yearly totals below), so day-by-day JSON
requests would have silently undercounted an increasing share of the recent,
most relevant years. Adding `&csv=true` returns the SAME data uncapped (a
single day returned 217 real rows against the JSON mode's capped 70 for the
identical date) - confirmed by summing full-year CSV pulls (2016: 6,236 bulk
rows; 2019: 7,565; 2022: 12,290; 2025: 19,407 - a smooth, monotonic increase,
not a step change consistent with hitting some other cap). A DATE-RANGE limit
exists on the csv endpoint too (a request spanning more than ~1 year returns
HTTP 500; a full calendar year works reliably) so this fetches ONE CALENDAR
YEAR per request via `&csv=true`, not one day - both faster (~20 requests for
2016-2026 x 2 report types, instead of thousands) and immune to the 70-row
trap.

SIGNALS at the close of day t (the report is public that evening): for a stock
with >=1 bulk (or block) deal on day t, BUY if buy_qty > sell_qty that day,
SELL if sell_qty > buy_qty, tie excluded. Direction fixed in advance, per the
"follow the smart money" thesis this strategy is actually sold as: BUY => higher
forward return, SELL => lower forward return. Reported regardless of sign.
FORWARD RETURN, lag-1 fill (this project's now-standard fill-lag convention,
Fifty-ninth entry onward): enter at the close of t+1, exit at the close of
t+1+h, h in {1, 5, 10, 21} trading days.
UNIVERSE: every distinct NSE symbol with >= N same-direction bulk (or block)
events in the fetched window AND a fetchable Yahoo adjusted-close series - not
the project's existing 52/54-stock large-cap WIDE_UNIVERSE/UNIVERSE_B, because
bulk/block deals concentrate in small/mid caps (a >=0.5%-of-equity single-
client trade is rare in a heavily-traded large cap) - restricting to the
existing universes would mostly test on stocks that almost never trigger the
signal. N=15 for bulk deals (data-rich: 149,132 rows over 10.75y); N=1 for
block deals (data-sparse: only 5-21 stocks EVER reach even 2 same-direction
block events in the whole window, confirmed by a direct count before choosing
this threshold - the per-stock null below is still valid at n=1, it just
draws a single random date for that stock too). Today's constituents only
(yfinance has no historical-membership or delisted-name API, the same
unresolved survivorship gap every prior entry in this project has carried).
STATISTIC: pooled mean forward return across every (stock, event-date) pair,
against a MATCHED-STOCK random-day null (Entry 98's `subset_control`, applied
per stock instead of to one index): for each stock, draw the same NUMBER of
random dates from ITS OWN eligible date range (where a forward return is
computable) as it has real events, pool across every stock, repeat many
times - this controls for "stocks that get bulk deals are structurally
different from the market" by never comparing across stocks, only within each
stock's own timing. Two-sided empirical p = (1 + count(|null| >= |actual|)) /
(1 + draws), mirroring probe_holiday_effect.py exactly.
REGISTERED: {BULK, BLOCK} x {BUY, SELL} x 4 horizons = 16 tests.
DECISION RULE (fixed in advance): a (signal, h) advances only if p < 0.05,
both halves of the window have the same sign as the pooled mean, AND the gross
mean return clears the 0.25% round-trip NSE cost (0.125%/leg, Entry 73) in the
BUY => long / SELL => short direction. A pass is a hypothesis for the honest
multiple-comparisons ledger, not a finding on its own.
"""
import argparse
import io
import os
import time

import numpy as np
import pandas as pd
import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_HERE, "bulk_block_deals_cache.csv.gz")
CHECKED = os.path.join(_HERE, "bulk_block_deals_checked.txt")  # "YYYY:bulk_deals" / ":block_deals" years already fetched
API = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
REFERER = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FIRST_YEAR = 2016
HORIZONS = (1, 5, 10, 21)
MIN_EVENTS = 8  # data-quality floor inside event_test() (enough eligible dates/events to trust a pooled mean) -
                # NOT the per-stock universe-membership threshold, see MIN_EVENTS_BY_TYPE below
DRAWS = 3000
COST_RT = 0.0025  # 0.125%/leg, Entry 73
DEAL_TYPES = ("bulk_deals", "block_deals")
CSV_COLS = ["date", "symbol", "buy_sell", "qty"]


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": REFERER})
    s.get(REFERER, timeout=20)
    return s


def parse_csv(content: bytes, deal_type):
    """The `&csv=true` payload: 'Date ','Symbol ','Security Name ','Client Name ','Buy / Sell ',
    'Quantity Traded ','Trade Price / Wght. Avg. Price ','Remarks ' (trailing spaces in every header, a UTF-8-BOM).
    `Quantity Traded` is Indian-comma-grouped text ("9,08,279", lakh/crore grouping, not thousands) - a naive
    pd.to_numeric on the raw string silently NaNs almost every row (caught before any signal was scored: a first
    smoke-test run of just 2016 returned 22 parsed rows against this same query's own un-parsed count of 6,236)."""
    df = pd.read_csv(io.StringIO(content.decode("utf-8-sig")))
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Date"].str.strip(), format="%d-%b-%Y"),
        "symbol": df["Symbol"].str.strip(),
        "buy_sell": df["Buy / Sell"].str.strip(),
        "qty": pd.to_numeric(df["Quantity Traded"].str.replace(",", "", regex=False), errors="coerce"),
    })
    out["deal_type"] = deal_type
    return out.dropna(subset=["qty"])


def fetch(first_year=FIRST_YEAR, last_year=None, sleep=0.3, save_every=1):
    """One request per (calendar year, deal type) via the uncapped `&csv=true` mode - a >1-year span returns
    HTTP 500 on this endpoint (confirmed live before building this), so calendar years are the natural chunk.
    Resumable: a "YYYY:deal_type" key is only marked checked after a successful parse."""
    import datetime as dt
    last_year = last_year or dt.date.today().year
    checked = set(open(CHECKED).read().split()) if os.path.exists(CHECKED) else set()
    old = pd.read_csv(CACHE, parse_dates=["date"]) if os.path.exists(CACHE) else pd.DataFrame(columns=CSV_COLS + ["deal_type"])
    new, done = [], 0
    try:
        s = new_session()
    except requests.RequestException as e:
        print(f"initial session setup failed: {type(e).__name__}, aborting fetch (nothing checked yet, safe to rerun)")
        return

    def save():
        write_atomic(pd.concat([old] + new).drop_duplicates(["date", "symbol", "deal_type", "buy_sell", "qty"]), CACHE)
        write_text_atomic("\n".join(sorted(checked)), CHECKED)

    for yr in range(first_year, last_year + 1):
        for dtype in DEAL_TYPES:
            key = f"{yr}:{dtype}"
            if key in checked:
                continue
            f, t = f"01-01-{yr}", f"31-12-{yr}" if yr < dt.date.today().year else dt.date.today().strftime("%d-%m-%Y")
            url = f"{API}?optionType={dtype}&from={f}&to={t}&csv=true"
            try:
                r = s.get(url, timeout=60)
                if r.status_code == 200:
                    parsed = parse_csv(r.content, dtype)
                    new.append(parsed)
                    checked.add(key)
                    print(f"{key}: {len(parsed)} rows, {parsed.date.dt.date.nunique()} trading days", flush=True)
                elif r.status_code in (401, 403):
                    s = new_session()  # session likely expired; refresh and retry this key next pass
                else:
                    print(f"{key}: HTTP {r.status_code}, will retry on rerun")
            except (requests.RequestException, ValueError, KeyError) as e:
                # KeyError: a 200-OK response whose CSV/columns don't match parse_csv()'s expectations (e.g. a
                # transient NSE error page served with a 200 status) - same "log and retry next rerun" handling
                # as the other exception types already caught here, not a crash.
                print(f"{key}: {type(e).__name__}, will retry on rerun")
                time.sleep(2)
            done += 1
            if done % save_every == 0:
                save()
            time.sleep(sleep)
    save()
    print("fetch done")


def write_atomic(df, path):
    """Write via a temp file + rename so an interrupted fetch can never truncate an existing cache. The temp name's
    extension ends in .tmp, not .gz, so pandas' compression inference (which looks only at the final suffix) would
    silently write PLAIN text into a file later renamed to .csv.gz - forced explicit here instead."""
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, path)


def write_text_atomic(text, path):
    tmp = path + ".tmp"
    open(tmp, "w").write(text)
    os.replace(tmp, path)


def load_deals():
    df = pd.read_csv(CACHE, parse_dates=["date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    return df.dropna(subset=["qty"])


def net_events(df, deal_type):
    """(date, symbol) -> 'BUY' / 'SELL' / None(tie), from that day's net bulk/block qty for one deal type."""
    d = df[df.deal_type == deal_type]
    g = d.groupby(["date", "symbol", "buy_sell"])["qty"].sum().unstack("buy_sell", fill_value=0.0)
    for col in ("BUY", "SELL"):
        if col not in g.columns:
            g[col] = 0.0
    side = pd.Series(np.where(g["BUY"] > g["SELL"], "BUY", np.where(g["SELL"] > g["BUY"], "SELL", None)),
                      index=g.index)
    return side[side.notna()]


def nse_calendar(period="max"):
    from data_yfinance import fetch_candles
    return pd.DatetimeIndex([c["date"].date() for c in fetch_candles("^NSEI", "1d", period)])


def load_closes(symbols, period="max"):
    """{symbol: Series(date -> adjusted close)} on the shared NSE calendar; symbols with a fetch failure are skipped
    (yfinance has no historical-membership or delisted-symbol API - the same unresolved survivorship gap every prior
    entry in this project has flagged)."""
    from data_yfinance import fetch_candles
    cal = nse_calendar(period)
    out = {}
    for sym in symbols:
        for _ in range(3):
            c = fetch_candles(sym + ".NS", "1d", period)
            if c:
                ix = pd.DatetimeIndex([x["date"].date() for x in c])
                s = pd.Series([x["close"] for x in c], index=ix, dtype=float).sort_index()
                out[sym] = s.reindex(cal)
                break
        else:
            print(f"{sym}: fetch failed, excluded")
    return out


def fwd_return(close: pd.Series, event_date, h):
    """close/prev on the shared calendar; None if the entry (t+1) or exit (t+1+h) bar doesn't exist. Both sides of
    the date comparison are wrapped in pd.Timestamp() before comparing - `close.index` is always a real DatetimeIndex
    in production (built via pd.DatetimeIndex(...) in nse_calendar()), but a caller-supplied Series with a plain
    Index of datetime.date objects would otherwise silently compare unequal to a Timestamp on every row (Python
    date/datetime equality is type-strict) and this function would return None for every date - caught by a
    synthetic test using exactly that index shape before it could bite a real caller."""
    idx = close.index
    pos = idx.searchsorted(event_date)
    if pos >= len(idx) or pd.Timestamp(idx[pos]) != pd.Timestamp(event_date):
        return None
    entry_pos, exit_pos = pos + 1, pos + 1 + h
    if exit_pos >= len(idx):
        return None
    p0, p1 = close.iloc[entry_pos], close.iloc[exit_pos]
    if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
        return None
    return p1 / p0 - 1


def eligible_dates(close: pd.Series, h):
    """Every calendar date where fwd_return(close, date, h) is computable - the population the matched-stock
    random-day null draws from. Must check EXACTLY the same bars fwd_return() checks (entry = i+1, exit = i+1+h) -
    an earlier version also required good[i] (the event day's own close), which fwd_return never looks at, silently
    excluding some genuinely-computable dates from the null population; caught by a test asserting the two functions
    agree on every date of a series with one bad bar. Also checks p0>0 (fwd_return additionally rejects a
    non-positive entry price, e.g. a bad/zero data point) - low priority (self-correcting downstream: fwd_return
    itself would just return None for such a date and event_test's ok_rets filter drops the resulting NaN), but
    matches fwd_return's validity criteria exactly rather than approximately."""
    idx = close.index
    n = len(idx)
    vals = close.to_numpy()
    good = close.notna().to_numpy()
    out = []
    for i in range(n - 1 - h):
        j = i + 1
        if good[j] and good[i + 1 + h] and vals[j] > 0:
            out.append(idx[i])
    return out


def event_test(closes: dict, events_by_symbol: dict, h, rng, draws=DRAWS):
    """events_by_symbol: {symbol: [event_date, ...]}. Pooled mean forward return of the real events vs a
    matched-stock random-day null (Entry 98's subset_control, per stock, pooled across stocks).

    "halves" is a TEMPORAL split (first/second half of the sample PERIOD, matching what every other entry in
    this project means by "both halves") - an independent code review caught that an earlier version built
    `real_returns` by iterating events_by_symbol.items() symbol-by-symbol (alphabetical, per build_universe's
    sorted(syms)) and sliced that list in two, which is a split by STOCK ALPHABET, not by time; real_(date,
    return) pairs are now sorted chronologically before splitting."""
    real = []  # (date, return) pairs, pooled across all stocks
    per_stock_pool = {}  # symbol -> (eligible_returns_array, real_event_count)
    for sym, dates in events_by_symbol.items():
        close = closes.get(sym)
        if close is None:
            continue
        elig = eligible_dates(close, h)
        if len(elig) < MIN_EVENTS:
            continue
        elig_rets = np.array([fwd_return(close, d, h) for d in elig], dtype=float)
        ok_rets = elig_rets[~np.isnan(elig_rets)]
        if len(ok_rets) < MIN_EVENTS:
            continue
        n_events = 0
        for d in dates:
            r = fwd_return(close, d, h)
            if r is not None:
                real.append((d, r))
                n_events += 1
        if n_events == 0:
            continue
        per_stock_pool[sym] = (ok_rets, n_events)
    if len(real) < MIN_EVENTS:
        return dict(n_events=len(real), n_stocks=0, actual=np.nan, p=np.nan, halves=(np.nan, np.nan))
    real.sort(key=lambda t: pd.Timestamp(t[0]))
    real_returns = np.array([r for _, r in real])
    actual = real_returns.mean()
    null = np.empty(draws)
    stocks = list(per_stock_pool.items())
    for i in range(draws):
        acc = []
        for sym, (pool, n) in stocks:
            take = min(n, len(pool))
            idx = rng.choice(len(pool), size=take, replace=False)
            acc.append(pool[idx])
        null[i] = np.concatenate(acc).mean() if acc else np.nan
    null = null[~np.isnan(null)]
    p = (1 + (np.abs(null) >= abs(actual) - 1e-15).sum()) / (1 + len(null))
    half = len(real_returns) // 2
    h1, h2 = real_returns[:half].mean(), real_returns[half:].mean()
    return dict(n_events=len(real_returns), n_stocks=len(per_stock_pool), actual=actual, p=p,
                null_mean=null.mean() if len(null) else np.nan, halves=(h1, h2))


def build_universe(deals, deal_type, side, min_events=MIN_EVENTS):
    ev = net_events(deals, deal_type)
    ev = ev[ev == side]
    counts = ev.groupby(level="symbol").size()
    syms = sorted(counts[counts >= min_events].index)
    events_by_symbol = {sym: list(ev.xs(sym, level="symbol").index) for sym in syms}
    return syms, events_by_symbol


MIN_EVENTS_BY_TYPE = {"bulk_deals": 15, "block_deals": 1}  # block deals rarely repeat per stock (Hundredth entry)


def run(period="max", seed=1, min_events_by_type=None):
    min_events_by_type = min_events_by_type or MIN_EVENTS_BY_TYPE
    deals = load_deals()
    rng = np.random.default_rng(seed)
    cells = {}
    all_syms = set()
    for dtype in DEAL_TYPES:
        for side in ("BUY", "SELL"):
            syms, events_by_symbol = build_universe(deals, dtype, side, min_events_by_type[dtype])
            cells[(dtype, side)] = (syms, events_by_symbol)
            all_syms |= set(syms)
    print(f"fetching adjusted closes for {len(all_syms)} distinct symbols (union across all 4 cells)...")
    closes = load_closes(sorted(all_syms), period)
    rows = []
    for (dtype, side), (syms, events_by_symbol) in cells.items():
        if not syms:
            continue
        for h in HORIZONS:
            res = event_test(closes, events_by_symbol, h, rng)
            rows.append(dict(deal_type=dtype, side=side, h=h, **res))
            h1, h2 = res["halves"]
            print(f"{dtype:11s} {side:4s} h={h:2d}  n_events={res['n_events']:4d} n_stocks={res['n_stocks']:3d} "
                  f"actual={res['actual']:+.4%} p={res['p']:.4f} halves=({h1:+.4%},{h2:+.4%})")
    return pd.DataFrame(rows)


def apply_rule(res, cost=COST_RT, alpha=0.05):
    """The pre-registered decision rule, in code. Advances only if p<alpha, both halves agree in sign with the
    pooled mean, and (BUY: actual, SELL: -actual, i.e. shorting the SELL signal) clears the round-trip cost."""
    r = res.copy()
    r["dir"] = np.where(r.side == "BUY", 1.0, -1.0)
    r["econ_return"] = r["actual"] * r["dir"]
    h1 = r["halves"].apply(lambda t: t[0])
    h2 = r["halves"].apply(lambda t: t[1])
    same_sign = (np.sign(h1) == np.sign(r["actual"])) & (np.sign(h2) == np.sign(r["actual"]))
    r["advance"] = (r["p"] < alpha) & same_sign & (r["econ_return"] > cost)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--period", default="max")
    a = ap.parse_args()
    if a.fetch:
        fetch()
        return
    pd.set_option("display.width", 200)
    res = run(period=a.period)
    res.to_csv(os.path.join(_HERE, "bulk_deals_signal_results.csv"), index=False)
    ruled = apply_rule(res)
    print("\n=== decision rule ===")
    print(ruled[["deal_type", "side", "h", "n_events", "n_stocks", "actual", "p", "advance"]].to_string(index=False))


if __name__ == "__main__":
    main()
