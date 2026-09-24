"""
Eighty-sixth entry: the DIVIDEND MONTH PREMIUM and trailing DIVIDEND YIELD as cross-sectional signals on two disjoint NSE
universes and two independent periods. Pre-registered here BEFORE any signal was scored against a return.

Why: this project has no fundamentals, so no value or yield factor was ever testable. Yahoo does carry per-share dividend
histories (from ~1999-2002, 30-40 payments per name here), which makes two classics testable for the first time:
  * Hartzmark-Solomon (2013) "The dividend month premium": stocks earn abnormal returns in the calendar months in which
    they are PREDICTED to pay a dividend (predicted from the same month a year earlier), a demand/price-pressure effect that
    is not compensation for risk and needs no fundamentals.
  * Trailing dividend yield as a value/yield proxy (high yield => higher return), the simplest quality-free value factor.
Neither has been tested here. Unlike the daily families of Entries 82-85 these are MONTHLY signals (~100-130 decisions per
period), so power is low and the pre-registration is tight (16 tests).

DATA: Yahoo `history(period="max", auto_adjust=False)` per stock: 'Adj Close' (dividend- and split-adjusted, total return)
for returns, 'Close' (split-adjusted, NOT dividend-adjusted, the same basis as the per-share 'Dividends' column) for the
yield, and ex-dividend dates from 'Dividends'. UNIV_A (`WIDE_UNIVERSE`, 52) and UNIV_B (`UNIVERSE_B`, 54); calendar ^NSEI.
Decision dates are the last trading day of each month (the latest, incomplete month has no forward return and drops out).
SIGNALS at decision month d (month-end close; direction fixed in advance, high => higher future return):
  DIV1 = 1 if the stock went ex-dividend in the CALENDAR month of d+1 twelve months earlier (i.e. in month d-11), else 0.
         (Hartzmark-Solomon's simplest predictor; names without a price at d are NaN.)
  DYLD = sum of dividends with ex-date in (ME_d - 365d, ME_d] / Close at ME_d.
A significant IC of the opposite sign is reported as reversed, NOT adopted post hoc.
FORWARD RETURN (lag 1): enter at the close of the first trading day AFTER the decision month-end, exit h month-ends later
(so h=1 is the whole month d+1 minus its first day). DIV1: h=1 only (the premium is a month-of-payment effect). DYLD: h in
{1, 3, 12} months.
STATISTIC and NULL: mean cross-sectional rank IC over decision months (Spearman, ties averaged, >= 25 names per month, >= 60
valid months); two-sided p from 4,000 circular shifts of the signal panel across months (shift >= 12 months, and shifts
that are a whole number of years are EXCLUDED: for a seasonal signal they would reproduce the observed alignment exactly),
(count+1)/(n+1). Reported per period, P1 = decisions from 2016-01, P2 = decisions before 2016-01 (from 2007-09).
Also reported, not tested: first/second-half IC, and the gross monthly excess (DIV1: mean return of predicted payers minus
the rest; DYLD: the 5 highest-yield names minus all names).
REGISTERED: (DIV1 x 1 + DYLD x 3) x 2 universes x 2 periods = 16 tests.
POST-REVIEW CORRECTIONS (independent code review, before the write-up; each disclosed because some touch the procedure):
  (1) SHIFT NULL NOT CENTRED FOR A PERSISTENT SIGNAL. DYLD's yield ranks have ~0.8 twelve-month autocorrelation, so a shifted
      copy is almost the same ranking: the null sat at -2.4 sd (h=12) and -1.9 sd (h=1) around zero and the two-sided
      |null| >= |obs| p (0.98 at A/P1/h=12) was meaningless. For DYLD the registered p is therefore the Newey-West t-test of
      the mean IC (lag = h); DIV1's null is centred (-0.26 sd) and keeps the pre-registered shift p. Both p's are reported for
      every cell, plus null_z0 (null mean / sd). The 12 DYLD shift-null p's are kept as unregistered rows.
  (2) The incomplete current month was used as a month-end (so h=1/3/12 exits for the latest decisions were partial months);
      now dropped. (3) The gross-excess spread used a weaker validity floor than the IC; now the same >= 25 names. It is an
      excess PER HOLD (h months) and is compared with the 0.25% cost per hold (a 12-month hold pays it once), not per month.
  (4) Names without history covering the lookback were scored 0 ("non-payer") instead of NaN; now NaN. (5) Halves are split
      by date, not by count.
DECISION RULE (fixed now): advance only if in BOTH universes and BOTH periods the IC > 0 with uncorrected p < 0.05, both
halves positive, and the gross monthly excess exceeds the 0.25% round-trip cost (monthly rebalancing pays it every month).
Otherwise null or "information without economics". An advance still faces the honest-family Bonferroni threshold
(m ~ 830) and is only a hypothesis for forward tracking. Caveats: today's constituents (survivorship); Yahoo dividend
history may be incomplete for special/interim dividends; ~100 monthly observations per period cannot resolve small effects.
"""

import argparse
import os

import numpy as np
import pandas as pd

import probe_delivery_signal as ds

_HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = pd.Timestamp("2016-01-01")
PANEL_CACHE = os.path.join(_HERE, "dividend_panel_cache.pkl")  # git-ignored: Yahoo history revises
MIN_ROWS = 60
DRAWS = 4000


# ---------------------------------------------------------------- data
def load_stock_panels(symbols):
    """(adj_close, raw_close, {symbol: dividend Series}, {symbol: first price date}) on the ^NSEI calendar
    (dividends keep their own dates; the first price date is taken BEFORE reindexing, from Yahoo's full history)."""
    import yfinance as yf
    cal = ds.nse_calendar("20y")
    adj, raw, divs, first = {}, {}, {}, {}
    for s in symbols:
        for _ in range(3):
            h = yf.Ticker(s + ".NS").history(period="max", auto_adjust=False)
            if len(h):
                break
        else:
            print(f"{s}: fetch failed, excluded")
            continue
        ix = pd.DatetimeIndex([t.date() for t in h.index])
        first[s] = ix.min()
        adj[s] = pd.Series(h["Adj Close"].to_numpy(), index=ix)
        raw[s] = pd.Series(h["Close"].to_numpy(), index=ix)
        d = h["Dividends"]
        divs[s] = pd.Series(d[d > 0].to_numpy(), index=pd.DatetimeIndex([t.date() for t in d[d > 0].index]))
    A = pd.DataFrame(adj).sort_index()
    R = pd.DataFrame(raw).sort_index()
    A, R = A[~A.index.duplicated()].reindex(cal), R[~R.index.duplicated()].reindex(cal)
    return A, R, divs, first


# ---------------------------------------------------------------- monthly frame
def month_end_positions(cal, today=None):
    """Positions in `cal` of the last trading day of each calendar month. The month containing `today` (default: now) is
    incomplete and is dropped, so a still-forming month is never used as a decision date or an exit (review fix)."""
    s = pd.Series(np.arange(len(cal)), index=cal)
    g = s.groupby([cal.year, cal.month]).last()
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    if (cal[-1].year, cal[-1].month) == (today.year, today.month):
        g = g.iloc[:-1]
    return g.to_numpy()


def forward_monthly(A, me_pos, h):
    """Row d (decision month-end): return from the close of the day AFTER ME_d to the close of ME_{d+h}. NaN if out of range."""
    vals = A.to_numpy()
    out = np.full((len(me_pos), vals.shape[1]), np.nan)
    for d in range(len(me_pos) - h):
        entry, exit_ = me_pos[d] + 1, me_pos[d + h]
        if entry < len(vals):
            out[d] = vals[exit_] / vals[entry] - 1
    return pd.DataFrame(out, index=A.index[me_pos], columns=A.columns)


def dividend_signals(R, divs, cal, me_pos, first=None):
    """DIV1 and DYLD at each decision month-end. Row d uses only data through ME_d. `first` = {symbol: first price date}:
    a name is NaN (unknown, not "non-payer") where its history does not cover the lookback (DIV1: the year-ago month;
    DYLD: the trailing 365 days), so recent listings never enter as true zeros (review fix)."""
    dates = cal[me_pos]
    Rm = R.to_numpy()[me_pos]
    div1 = np.full(Rm.shape, np.nan)
    dyld = np.full(Rm.shape, np.nan)
    ym = dates.year * 12 + dates.month
    for j, s in enumerate(R.columns):
        dv = divs.get(s)
        dv = pd.Series(dtype=float, index=pd.DatetimeIndex([])) if dv is None or len(dv) == 0 else dv  # never-paid / missing
        ex_ym = set((dv.index.year * 12 + dv.index.month).tolist())
        for d in range(len(dates)):
            if np.isnan(Rm[d, j]):
                continue
            f0 = first.get(s) if first else None
            yago = ym[d] + 1 - 12
            if f0 is None or f0 <= pd.Timestamp(year=(yago - 1) // 12, month=(yago - 1) % 12 + 1, day=1):
                div1[d, j] = 1.0 if yago in ex_ym else 0.0
            if f0 is None or f0 <= dates[d] - pd.Timedelta(days=365):
                w = dv[(dv.index > dates[d] - pd.Timedelta(days=365)) & (dv.index <= dates[d])]
                dyld[d, j] = float(w.sum()) / Rm[d, j]
    return {"DIV1": pd.DataFrame(div1, index=dates, columns=R.columns),
            "DYLD": pd.DataFrame(dyld, index=dates, columns=R.columns)}


def binary_spread(S, F):
    """Mean over months of (mean fwd return of S==1 names) - (mean of S==0 names); months need >= 5 in each group."""
    out = []
    for s, f in zip(S.to_numpy(), F.to_numpy()):
        m = ~np.isnan(s) & ~np.isnan(f)
        a, b = f[m & (s == 1)], f[m & (s == 0)]
        if m.sum() >= ds.MIN_NAMES and len(a) >= 5 and len(b) >= 5:  # same >= 25-name floor as the IC (review fix)
            out.append(a.mean() - b.mean())
    return float(np.mean(out)) if out else np.nan


def run(A, R, divs, first=None, seed=1):
    cal = A.index
    me_pos = month_end_positions(cal)
    S = dividend_signals(R, divs, cal, me_pos, first)
    rng = np.random.default_rng(seed)
    rows = []
    for label, sel in (("P1", S["DIV1"].index >= SPLIT), ("P2", S["DIV1"].index < SPLIT)):
        for sig, hs in (("DIV1", (1,)), ("DYLD", (1, 3, 12))):
            for h in hs:
                F = forward_monthly(A, me_pos, h)
                Xp, Fp = S[sig][sel], F[sel]
                mic, p_shift, n, ic = ds.ic_test(Xp, Fp, rng, draws=DRAWS, min_shift=12, min_rows=MIN_ROWS, avoid_mod=12)
                t_nw, p_nw = ds.nw_p(ic, h)
                v = ic.dropna()
                mid = v.index[0] + (v.index[-1] - v.index[0]) / 2 if len(v) else None  # halves by DATE, not position
                gross = binary_spread(Xp, Fp) if sig == "DIV1" else ds.top_excess(Xp, Fp)
                # DYLD is a persistent ranking (12-month rank autocorrelation ~0.8): the shift null is not centred at zero
                # for it (review), so its registered p is the Newey-West one; DIV1's null is centred, its registered p is the shift p.
                rows.append(dict(period=label, signal=sig, h=h, months=n, ic=mic, p=(p_nw if sig == "DYLD" else p_shift),
                                 p_basis=("NW" if sig == "DYLD" else "shift"), p_shift=p_shift, p_nw=p_nw, t_nw=t_nw,
                                 null_z0=ic.attrs.get("null_z0", np.nan),
                                 ic_h1=v[v.index <= mid].mean() if len(v) else np.nan,
                                 ic_h2=v[v.index > mid].mean() if len(v) else np.nan, gross_excess=gross))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore the local panel cache")
    a = ap.parse_args()
    pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:9.4f}")
    panels = pd.read_pickle(PANEL_CACHE) if os.path.exists(PANEL_CACHE) and not a.refresh else {}
    outs = []
    for name, syms in ds.universes().items():
        if name not in panels:
            panels[name] = load_stock_panels(syms)
            pd.to_pickle(panels, PANEL_CACHE)
        if len(panels[name]) < 4:  # cache from before the review fix (no first-listing dates)
            panels[name] = load_stock_panels(syms)
            pd.to_pickle(panels, PANEL_CACHE)
        A, R, divs, first = panels[name]
        n_div = sum(1 for s in A.columns if len(divs.get(s, [])))
        print(f"\nuniverse {name}: {A.shape[1]} symbols, {n_div} with dividend history, {A.index[0].date()}..{A.index[-1].date()}")
        res = run(A, R, divs, first)
        res.insert(0, "universe", name)
        print(res.to_string(index=False))
        outs.append(res)
    pd.concat(outs).to_csv(os.path.join(_HERE, "dividend_signal_results.csv"), index=False)


if __name__ == "__main__":
    main()
