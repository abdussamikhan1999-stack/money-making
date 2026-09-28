"""
Ninety-ninth entry: CROSS-SECTIONAL RETURN SEASONALITY (Heston & Sadka 2008, "Seasonality in the cross-section of stock
returns", Journal of Financial Economics) on two disjoint NSE universes and two independent periods. Pre-registered here
BEFORE any signal was scored against a return.

Why: a stock's return in a given calendar month predicts that same stock's return in the same calendar month in later years
(annual lags 12, 24, 36, ... months), while other lags carry nothing. It is not the dividend month premium (Entry 86: a
0/1 predicted-payer flag) nor a market-wide calendar effect (Entries 92/94-98: one index, one day-type): it ranks STOCKS
against each other on their own history of that month, so any market-wide month effect (a January premium) cancels in the
cross-section. Never tested here. Monthly decisions, so power is low; the pre-registration is small (12 tests).

DATA: Yahoo `history(period="max", auto_adjust=False)` 'Adj Close' (dividend- and split-adjusted total return), UNIV_A
(`WIDE_UNIVERSE`, 52) and UNIV_B (`UNIVERSE_B`, 54); calendar ^NSEI `period="max"`, intended to reach past the 20 years the other probes use
(POST-RUN CORRECTION: Yahoo's ^NSEI history begins 2007-09-17, the same start as `20y`, so this gained nothing; SEAS5's P2
cells have only 40 valid months and SEAS3's 64, and the two SEAS5 P2 cells fall under the 60-month floor and are untestable.
The docstring originally claimed 1997+, which was wrong). Decision dates are the last trading day of each month (the
incomplete current month is dropped). Month-i return M_i = P(ME_i) / P(ME_{i-1}) - 1 on the same month-end grid.
SIGNALS at decision month-end d (target month = d+1; direction fixed in advance, high => higher future return; a
significant IC of the opposite sign is reported as reversed, NOT adopted post hoc):
  SEAS1 = M_{d-11}                                  (same calendar month, one year ago)
  SEAS3 = mean(M_{d-11}, M_{d-23}, M_{d-35})        (same calendar month, average of the last 3 years)
  SEAS5 = mean of the last 5 same-calendar-month returns (lags 12, 24, 36, 48, 60 months)
A name with any missing component return is NaN (never 0). Only data through ME_d is used.
FORWARD RETURN (lag 1, identical to Entry 86): enter at the close of the first trading day AFTER ME_d, exit at ME_{d+1}.
h = 1 month only (seasonality is a month-of-year effect).
STATISTIC and NULL: mean cross-sectional rank IC over decision months (Spearman, >= 25 names/month, >= 60 valid months);
two-sided p from 4,000 circular shifts of the signal panel across months (shift >= 12, shifts that are a whole number of
years EXCLUDED, since they reproduce the seasonal alignment exactly; with ~100-150 months only ~100 distinct shifts remain,
so the shift p cannot go below ~0.01 whatever the effect, and the Newey-West p is what resolves anything smaller: a planted
IC of 0.30 in the unit test gets p_shift 0.011, p_nw < 1e-6). The retained shifts pair a stock's history of calendar
month m with the return of month m+k, k not 0 mod 12: that IS Heston-Sadka's "other lags" control, so the null tests exactly
the annual-lag claim. Also a Newey-West t-test (lag 1). REGISTERED p = max(p_shift, p_nw) (Entry 85's conservative rule).
Periods: P1 = decisions from 2016-01, P2 = decisions before 2016-01 (first valid decision is data-limited and reported).
REGISTERED: 3 signals x 2 universes x 2 periods = 12 tests.
Also reported, not tested: first/second-half IC (by DATE), the gross top-5 excess per month (5 highest-signal names minus
all names), the standard error of the mean IC (so the minimum effect this test could detect, ~2.8 x SE, is on the page).
DECISION RULE (fixed now, `ds.apply_rule`): a signal advances only if in BOTH universes and BOTH periods IC > 0 with
max(p_shift, p_nw) < 0.05, both halves positive, and the gross top-5 monthly excess exceeds the 0.25% round-trip cost
(monthly rebalancing pays it every month). Otherwise null or "information without economics". An advance still faces the
honest-family Bonferroni threshold and is only a hypothesis for forward tracking.
CAVEATS stated up front: today's constituents (survivorship, worse in the early period, and it favours losers that
recovered); Yahoo's pre-2007 NSE history is less reliable (split/bonus adjustment gaps); ~100-150 monthly observations
cannot resolve small effects. The US effect is a few tenths of a percent a month per unit of seasonal return, i.e. a
rank IC near 0.01-0.03, at or below what this design can detect: a null here is "no effect large enough to see", not
"no effect".
"""

import argparse
import os

import numpy as np
import pandas as pd

import probe_delivery_signal as ds
import probe_dividend_signal as dv

_HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = pd.Timestamp("2016-01-01")
PANEL_CACHE = os.path.join(_HERE, "seasonality_panel_cache.pkl")  # git-ignored: Yahoo history revises
MIN_ROWS = 60
DRAWS = 4000
YEARS = {"SEAS1": 1, "SEAS3": 3, "SEAS5": 5}


# ---------------------------------------------------------------- data
def load_adj(symbols, cal):
    """Adj Close per symbol on the ^NSEI calendar `cal`. A failed fetch (3 tries) excludes the symbol, loudly.
    Documented debt (code review): near-copy of probe_dividend_signal.load_stock_panels, which hard-codes a 20y calendar and
    also fetches dividends, so it could not be reused as is."""
    import yfinance as yf
    adj = {}
    for s in symbols:
        for _ in range(3):
            try:
                h = yf.Ticker(s + ".NS").history(period="max", auto_adjust=False)
            except Exception as e:  # transient Yahoo/rate-limit error: retry, then exclude loudly (code review)
                print(f"{s}: {type(e).__name__}")
                continue
            if len(h):
                break
        else:
            print(f"{s}: fetch failed, excluded")
            continue
        adj[s] = pd.Series(h["Adj Close"].to_numpy(), index=pd.DatetimeIndex([t.date() for t in h.index]))
    A = pd.DataFrame(adj).sort_index()
    return A[~A.index.duplicated()].reindex(cal)


# ---------------------------------------------------------------- signals
def monthly_returns(A, me_pos):
    """Row i = return over calendar month i (ME_{i-1} close to ME_i close); row 0 is NaN."""
    P = A.to_numpy()[me_pos]
    M = np.full(P.shape, np.nan)
    M[1:] = P[1:] / P[:-1] - 1
    return M


def seasonal_signals(A, me_pos):
    """SEAS1/3/5 at each decision month-end d. Row d uses M_{d-11-12j}, all <= d: no data after ME_d."""
    dates = A.index[me_pos]
    ym = dates.year * 12 + dates.month
    assert (np.diff(ym) == 1).all(), "month-end grid has a gap; positional lags would not be calendar lags"
    M = monthly_returns(A, me_pos)
    out = {}
    for name, k in YEARS.items():
        S = np.full(M.shape, np.nan)
        for d in range(len(M)):
            idx = [d - 11 - 12 * j for j in range(k)]
            if idx[-1] >= 1:
                S[d] = M[idx].mean(axis=0)  # NaN if any component is NaN
        out[name] = pd.DataFrame(S, index=dates, columns=A.columns)
    return out


# ---------------------------------------------------------------- run
def run(A, seed=1):
    me_pos = dv.month_end_positions(A.index)
    S = seasonal_signals(A, me_pos)
    F = dv.forward_monthly(A, me_pos, 1)
    rng = np.random.default_rng(seed)
    rows = []
    for label, sel in (("P1", F.index >= SPLIT), ("P2", F.index < SPLIT)):
        for sig in YEARS:
            Xp, Fp = S[sig][sel], F[sel]
            mic, p_shift, n, ic = ds.ic_test(Xp, Fp, rng, draws=DRAWS, min_shift=12, min_rows=MIN_ROWS, avoid_mod=12)
            t_nw, p_nw = ds.nw_p(ic, 1)
            v = ic.dropna()
            mid = v.index[0] + (v.index[-1] - v.index[0]) / 2 if len(v) else None
            rows.append(dict(period=label, signal=sig, h=1, months=n, first=v.index[0].date() if len(v) else None,
                             ic=mic, ic_se=v.std() / np.sqrt(len(v)) if len(v) > 1 else np.nan,
                             p=np.nanmax([p_shift, p_nw]) if n >= MIN_ROWS else np.nan, p_shift=p_shift, p_nw=p_nw,
                             null_z0=ic.attrs.get("null_z0", np.nan),
                             ic_h1=v[v.index <= mid].mean() if len(v) else np.nan,
                             ic_h2=v[v.index > mid].mean() if len(v) else np.nan,
                             gross_excess=ds.top_excess(Xp, Fp),
                             # post-hoc diagnostic (added after the first run, NOT registered, no p-value): the 5 LOWEST-signal
                             # names' excess. Both tails hold the extreme, i.e. volatile, names; if both earn ~ the same the
                             # "gross excess" is a volatility artifact, not a signal.
                             bottom_excess=ds.top_excess(-Xp, Fp)))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore the local panel cache")
    a = ap.parse_args()
    pd.set_option("display.width", 220, "display.float_format", lambda v: f"{v:9.4f}")
    panels = pd.read_pickle(PANEL_CACHE) if os.path.exists(PANEL_CACHE) and not a.refresh else {}
    cal = ds.nse_calendar("max")
    outs = []
    for name, syms in ds.universes().items():
        if name not in panels:
            panels[name] = load_adj(syms, cal)
            pd.to_pickle(panels, PANEL_CACHE)
        A = panels[name]
        print(f"\nuniverse {name}: {A.shape[1]} symbols, {A.index[0].date()}..{A.index[-1].date()}")
        res = run(A)
        res.insert(0, "universe", name)
        print(res.to_string(index=False))
        outs.append(res)
    allr = pd.concat(outs)
    allr.to_csv(os.path.join(_HERE, "seasonality_signal_results.csv"), index=False)
    print("\nDECISION RULE (ds.apply_rule; p_nw column needed):")
    print(ds.apply_rule(allr).to_string(index=False))


if __name__ == "__main__":
    main()
