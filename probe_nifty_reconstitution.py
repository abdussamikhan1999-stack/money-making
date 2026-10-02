"""
Entry 101: Nifty 50 index-reconstitution drift - does a stock added to the
Nifty 50 index show abnormal returns around the day the change takes effect,
and a deleted stock the opposite? Pre-registered here BEFORE any signal was
scored against a return, matching this project's own established convention
(Entries 82-100).

WHY THIS IS GENUINELY NEW: every prior signal in this project comes from
price, volume, options open interest, delivery percentage, dividends, bulk/
block deals, or the calendar - all either continuous daily statistics or a
disclosed TRADE. This is a forced-FLOW event: NSE Indices reviews the Nifty
50 semi-annually: an addition forces every fund tracking the index (ETFs,
index funds) to buy the new constituent on the effective date; a deletion
forces the same funds to sell. The mechanism is passive-money rebalancing,
not information, momentum, or mean-reversion - genuinely different from
everything else in this ledger.

DATA: no NSE API serves historical index-membership changes (confirmed
absent from every endpoint this project's other probes already use - the
historicalOR bulk-deals endpoint, the bhavcopy archives, none carry
constituent-change history). A background research pass compiled a dated
list from Wikipedia's "NIFTY 50" article, which individually footnotes each
effective date to an NSE press release or major financial-news report
(Economic Times, Business Standard, Mint, BloombergQuint, etc.) - see
EVENTS below. Two honesty adjustments made BEFORE any signal was scored:
(1) ANCHORED ON THE EFFECTIVE DATE, not the announcement date. The
compiled announcement dates are unreliable - several are a retrospective
news article published years later, or a date that postdates the effective
date outright - so they cannot be trusted as the true announcement day.
The effective date is individually sourced for every row and is also the
economically correct anchor for THIS hypothesis (passive funds must
transact AT this date; that is the forcing mechanism, not the
announcement). (2) EIGHT EVENTS EXCLUDED for having no citation at all in
the compiled research (flagged "no independent citation, just a Wikipedia
table row with no ref tag"): 2005-09-26, 2006-06-27 (x2), 2006-09-01,
2008-03-14 (x2), 2010-04-08, 2011-03-25. Keeping unsourced rows in a
project whose entire standard is "a date you're unsure of, clearly
flagged, is more useful than a wrong date stated as fact" would poison the
test; excluding them costs 8 of the 69 compiled events (61 remain, below
in EVENTS), not the study.
SYMBOL SPELLING: several older/delisted names were flagged uncertain by the
research pass (JETAIRWAYS, RCOM, RPL, TATATEA, SCI, OBC, GLAXO, IPCL,
UNITECH, SESAGOA, JPASSOCIAT, RELINFRA, UNITDSPR, IBULHSGFIN, INFRATEL,
RELCAPITAL, INDIGO, MAXHEALTH, ETERNAL). No pre-filtering attempted - this
project's established convention (Hundredth entry, `load_closes`) is
fetch-and-skip: a wrong or delisted ticker simply fails to fetch from
Yahoo and is dropped, printed, and excluded, never silently mis-scored
against the wrong company's returns.

SIGNAL: ADD => expect a positive abnormal return after the effective date
(forced buying); DELETE => expect negative (forced selling). Direction
fixed in advance, reported regardless of sign - exactly the Hundredth
entry's convention.
FORWARD RETURN, lag-1 fill (this project's standard fill-lag convention,
Fifty-ninth entry onward): enter at the close of effective_date + 1, exit
at close of effective_date + 1 + h, h in {1, 5, 10, 21} trading days -
reusing probe_bulk_deals_signal.fwd_return/eligible_dates/event_test
verbatim rather than re-deriving the same tested logic (RESULTS.md's own
open item #3 already flags this project's duplicated-helper problem; not
adding to it here).
STATISTIC: pooled mean forward return across every (stock, event-date)
pair, against a MATCHED-STOCK random-day null (same per-stock bootstrap as
the Hundredth entry): for each stock, draw the same NUMBER of random dates
from ITS OWN eligible date range as it has real events, pool across every
stock, repeat 3,000 times. At most one event per stock per side in nearly
every case (index reconstitution is rare for a given name), so this
reduces to the same per-stock-n=1 design the Hundredth entry already
validated for block deals.
REGISTERED: {ADD, DELETE} x 4 horizons = 8 tests.
DECISION RULE (fixed in advance, identical form to the Hundredth entry): a
(side, h) advances only if p < 0.05, both halves of the window have the
same sign as the pooled mean, AND the gross mean return clears the 0.25%
round-trip NSE cost (0.125%/leg, Entry 73) in the ADD => long / DELETE =>
short direction. A pass is a hypothesis for the honest multiple-
comparisons ledger, not a finding on its own.
"""
import argparse
import os

import numpy as np
import pandas as pd

from probe_bulk_deals_signal import COST_RT, DRAWS, HORIZONS, event_test, load_closes

_HERE = os.path.dirname(os.path.abspath(__file__))

# (effective_date, ADD symbol, DELETE symbol). Compiled from Wikipedia's "NIFTY 50" article change
# table, each effective date individually footnoted to an NSE press release or major financial-news
# report. The 8 events with NO citation at all in the compiled research are excluded (see docstring):
# 2005-09-26, 2006-06-27 (Siemens/Suzlon pair), 2006-09-01, 2008-03-14 (DLF/Powergrid pair),
# 2010-04-08, 2011-03-25.
EVENTS = [
    ("2005-02-25", "TCS", "INDHOTEL"),
    ("2007-04-04", "RPL", "JETAIRWAYS"),
    ("2007-04-04", "STERLITE", "OBC"),
    ("2007-09-24", "NTPC", "DABUR"),
    ("2007-10-05", "UNITECH", "IPCL"),
    ("2007-12-12", "CAIRN", "HINDPETRO"),
    ("2007-12-12", "IDEA", "MTNL"),
    ("2008-09-10", "RPOWER", "DRREDDY"),
    ("2009-01-12", "RELCAPITAL", "SATYAM"),
    ("2009-03-27", "AXISBANK", "ZEEL"),
    ("2009-06-17", "JINDALSTEL", "RPL"),
    ("2009-10-20", "IDFC", "NALCO"),
    ("2009-10-20", "JPASSOCIAT", "TATACOMM"),
    ("2010-10-01", "BAJAJ-AUTO", "ABB"),
    ("2010-10-01", "DRREDDY", "IDEA"),
    ("2010-10-01", "SESAGOA", "UNITECH"),
    ("2011-10-10", "COALINDIA", "RELCAPITAL"),
    ("2012-04-27", "ASIANPAINT", "RCOM"),
    ("2012-04-27", "BANKBARODA", "RPOWER"),
    ("2012-09-28", "ULTRACEMCO", "SAIL"),
    ("2012-09-28", "LUPIN", "STERLITE"),
    ("2013-04-01", "INDUSINDBK", "SIEMENS"),
    ("2013-04-01", "NMDC", "WIPRO"),
    ("2013-09-27", "WIPRO", "RELINFRA"),
    ("2014-03-28", "TECHM", "JPASSOCIAT"),
    ("2014-03-28", "UNITDSPR", "RANBAXY"),
    ("2014-09-19", "ZEEL", "UNITDSPR"),
    ("2015-03-27", "IDEA", "DLF"),
    ("2015-03-27", "YESBANK", "JINDALSTEL"),
    ("2015-05-29", "BOSCHLTD", "IDFC"),
    ("2015-09-28", "ADANIPORTS", "NMDC"),
    ("2016-04-01", "AUROPHARMA", "CAIRN"),
    ("2016-04-01", "INFRATEL", "PNB"),
    ("2016-04-01", "EICHERMOT", "VEDL"),
    ("2017-03-31", "IBULHSGFIN", "BHEL"),
    ("2017-03-31", "IOC", "IDEA"),
    ("2017-05-26", "VEDL", "GRASIM"),
    ("2017-09-29", "BAJFINANCE", "ACC"),
    ("2017-09-29", "HINDPETRO", "BANKBARODA"),
    ("2017-09-29", "UPL", "TATAPOWER"),
    ("2018-04-02", "BAJAJFINSV", "AMBUJACEM"),
    ("2018-04-02", "GRASIM", "AUROPHARMA"),
    ("2018-04-02", "TITAN", "BOSCHLTD"),
    ("2018-09-28", "JSWSTEEL", "LUPIN"),
    ("2019-03-29", "BRITANNIA", "HINDPETRO"),
    ("2019-09-27", "NESTLEIND", "IBULHSGFIN"),
    ("2020-03-19", "SHREECEM", "YESBANK"),
    ("2020-07-31", "HDFCLIFE", "VEDL"),
    ("2020-09-25", "SBILIFE", "ZEEL"),
    ("2020-09-25", "DIVISLAB", "INFRATEL"),
    ("2021-03-31", "TATACONSUM", "GAIL"),
    ("2022-03-31", "APOLLOHOSP", "IOC"),
    ("2022-09-30", "ADANIENT", "SHREECEM"),
    ("2023-07-13", "LTIM", "HDFC"),
    ("2024-03-28", "SHRIRAMFIN", "UPL"),
    ("2024-09-30", "BEL", "DIVISLAB"),
    ("2024-09-30", "TRENT", "LTIM"),
    ("2025-03-28", "JIOFIN", "BPCL"),
    ("2025-03-28", "ETERNAL", "BRITANNIA"),
    ("2025-09-30", "INDIGO", "HEROMOTOCO"),
    ("2025-09-30", "MAXHEALTH", "INDUSINDBK"),
]


def events_by_symbol(side):
    """side: 'ADD' (col 1) or 'DELETE' (col 2). {symbol: [event_date, ...]} - a symbol appearing
    more than once (e.g. added, later deleted, later re-added) keeps every occurrence."""
    col = 1 if side == "ADD" else 2
    out: dict[str, list] = {}
    for row in EVENTS:
        date, sym = pd.Timestamp(row[0]), row[col]
        out.setdefault(sym, []).append(date)
    return out


def run(period="max", seed=1, draws=DRAWS):
    rng = np.random.default_rng(seed)
    cells = {side: events_by_symbol(side) for side in ("ADD", "DELETE")}
    all_syms = sorted(set().union(*[set(d) for d in cells.values()]))
    print(f"fetching adjusted closes for {len(all_syms)} distinct symbols (union of ADD+DELETE)...")
    closes = load_closes(all_syms, period)
    print(f"resolved {len(closes)}/{len(all_syms)} symbols on Yahoo "
          f"({len(all_syms) - len(closes)} excluded: delisted, renamed, or a wrong guessed ticker)")
    rows = []
    for side, by_sym in cells.items():
        for h in HORIZONS:
            res = event_test(closes, by_sym, h, rng, draws=draws)
            rows.append(dict(side=side, h=h, **res))
            h1, h2 = res["halves"]
            print(f"{side:6s} h={h:2d}  n_events={res['n_events']:3d} n_stocks={res['n_stocks']:3d} "
                  f"actual={res['actual']:+.4%} p={res['p']:.4f} halves=({h1:+.4%},{h2:+.4%})")
    return pd.DataFrame(rows)


def apply_rule(res, cost=COST_RT, alpha=0.05):
    """The pre-registered decision rule, in code: ADD is scored long, DELETE is scored short (mirrors
    probe_bulk_deals_signal.apply_rule's BUY/SELL convention exactly)."""
    r = res.copy()
    r["dir"] = np.where(r.side == "ADD", 1.0, -1.0)
    r["econ_return"] = r["actual"] * r["dir"]
    h1 = r["halves"].apply(lambda t: t[0])
    h2 = r["halves"].apply(lambda t: t[1])
    same_sign = (np.sign(h1) == np.sign(r["actual"])) & (np.sign(h2) == np.sign(r["actual"]))
    r["advance"] = (r["p"] < alpha) & same_sign & (r["econ_return"] > cost)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="max")
    a = ap.parse_args()
    pd.set_option("display.width", 200)
    res = run(period=a.period)
    res.to_csv(os.path.join(_HERE, "nifty_reconstitution_results.csv"), index=False)
    ruled = apply_rule(res)
    print("\n=== decision rule ===")
    print(ruled[["side", "h", "n_events", "n_stocks", "actual", "p", "advance"]].to_string(index=False))


if __name__ == "__main__":
    main()
