"""
Eighty-second entry: NSE delivery-percentage signals. Pre-registered here BEFORE any signal was scored against a return.

Why: 72 mechanisms so far use prices, volume or option open interest. NSE also publishes, per stock per day, the share
of traded quantity that was actually delivered (DELIV_PER, `sec_bhavdata_full_DDMMYYYY.csv`): a positioning measure that
is specific to India and never used in this project. The literature is mixed (delivery as informed accumulation vs. as
noise). Data limit found first: the file exists only from ~2019-09-30 (older MTO archive files 404), so ~7 years, ~1,700
trading days, no earlier decade for replication. That decides the design: a DAILY cross-sectional rank IC (1,700
decisions x ~50 stocks) instead of a monthly rotation (~84 decisions), and TWO disjoint universes as the replication.

DATA: DELIV_PER for EQ-series symbols of UNIV_A (`probe_ibs_rotation_widen.WIDE_UNIVERSE`, 52 names) and UNIV_B
(`probe_reversal_rotation.UNIVERSE_B`, 54 different names), cached in delivery_cache.csv.gz. Returns from Yahoo ADJUSTED
closes (NSE quantities/prices are not split-adjusted; Entry 79 validated Yahoo OHLC against bhavcopy). Only ratios
(delivery %) and adjusted returns are used, never raw delivered quantity.

SIGNALS at the close of day t (the day's file is public that evening; nothing later is used):
  D1 abnormal delivery = mean(DELIV_PER, days t-4..t) - mean(DELIV_PER, days t-64..t-5).   High = long.
  D2 signed abnormal delivery = D1 x sign(close_t / close_{t-5} - 1).  High = a delivery surge on a rising stock
     (accumulation); low = a surge on a falling one (distribution). High = long.
  Direction is fixed in advance as "high => higher future return"; a significant NEGATIVE IC is reported as a
  reversed sign, not adopted post hoc.
FORWARD RETURN, lag 1: enter at close t+1, exit at close t+1+h, h in {1, 5, 10, 21} trading days.
STATISTIC: mean over days of the cross-sectional rank IC (Spearman; ranks taken within each panel's own non-NaN set,
NaN centred to 0, identical for observed and null; rows need >= 25 valid names). Two-sided p from 4,000 circular shifts
of the signal panel against the returns (shift >= 60 days; keeps the signal's autocorrelation and the returns'
regime structure, breaks the timing), (count+1)/(n+1). Also reported, not tested: first-half vs second-half IC,
and the gross excess of the 5 highest-signal names over the universe mean across h days.
REGISTERED: 2 signals x 4 horizons x 2 universes = 16 tests.

DECISION RULE (fixed now): a signal/horizon advances to a strategy test only if BOTH universes have mean IC > 0 with
uncorrected p < 0.05 AND both halves are positive in both universes AND the gross top-5 excess over h days exceeds the
0.25% round-trip cost (0.125%/leg, Entry 73) in both. Anything else is reported as null or as "information without
economics". A pass would still face the honest-family Bonferroni threshold (m ~ 630, ~0.00008), which a 7-year window
cannot realistically reach; a pass is a hypothesis for forward tracking, not a finding.
KNOWN LIMITS: today's constituents (survivorship, smaller than 2007-16 but real); one 7-year window incl. 2020.
POST-REVIEW CORRECTIONS (independent code review, before the write-up; disclosed because two touch the procedure):
  (1) windows tolerate a few missing days (5-day mean needs 4, 60-day baseline needs 55) instead of collapsing on one:
      a single bad Yahoo day (2025-03-18, most names zero volume) had blanked 65 days of signal, and a missing delivery
      file would have done the same; (2) the panel calendar is ^NSEI's, not the delivery cache's (four special weekend
      sessions have no Yahoo bar; 2022-08-08 and the 2025-02-01 Budget Saturday were missing from the cache because the
      fetch skipped weekends and swallowed a non-404 error; both fixed and refetched); (3) `ic_test`'s <200-day early
      return returned an unmasked IC series; (4) the spanning check now scores the raw signal on the same cells as the
      residual; (5) the three entries share one runner/loader. The pre-registered signals, horizons, direction, decision
      rule and null are unchanged.
"""

import argparse
import datetime as dt
import io
import math
import os
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_HERE, "delivery_cache.csv.gz")
CHECKED = os.path.join(_HERE, "delivery_checked.txt")  # dates already requested (incl. 404 = holiday / no file)
URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{}.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "*/*"}
FIRST = dt.date(2019, 9, 1)
HORIZONS = (1, 5, 10, 21)
MIN_NAMES = 25
DRAWS = 4000
COST_RT = 0.0025  # 0.125% per leg, Entry 73


def universes():
    import probe_ibs_rotation_widen as w
    import probe_reversal_rotation as r
    return {"A": [s.replace(".NS", "") for s in w.WIDE_UNIVERSE], "B": list(r.UNIVERSE_B)}


# ---------------------------------------------------------------- data
def parse_bhav(text, symbols):
    """One sec_bhavdata_full file -> rows (date, symbol, ttl, deliv_per) for EQ-series symbols in `symbols`."""
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df = df[(df["SERIES"].str.strip() == "EQ") & df["SYMBOL"].isin(symbols)].copy()
    df["date"] = pd.to_datetime(df["DATE1"].str.strip(), format="%d-%b-%Y")
    df["deliv_per"] = pd.to_numeric(df["DELIV_PER"], errors="coerce")  # '-' when not reported
    df["ttl"] = pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce")
    return df[["date", "SYMBOL", "ttl", "deliv_per"]].rename(columns={"SYMBOL": "symbol"})


def fetch(start=FIRST, end=None, sleep=0.25, save_every=100):
    end = end or dt.date.today()
    syms = set().union(*universes().values())
    checked = set(open(CHECKED).read().split()) if os.path.exists(CHECKED) else set()
    old = pd.read_csv(CACHE, parse_dates=["date"]) if os.path.exists(CACHE) else pd.DataFrame()
    new, done = [], 0

    def save():
        write_atomic(pd.concat([old] + new).drop_duplicates(["date", "symbol"]), CACHE)
        write_text_atomic("\n".join(sorted(checked)), CHECKED)

    d = start
    while d <= end:
        key = d.isoformat()
        if key not in checked:  # weekends too: NSE holds special Saturday/Sunday sessions (Budget, Muhurat)
            req = urllib.request.Request(URL.format(d.strftime("%d%m%Y")), headers=HEADERS)
            try:
                text = urllib.request.urlopen(req, timeout=30).read().decode("latin1")
                try:
                    new.append(parse_bhav(text, syms))
                except (pd.errors.ParserError, ValueError, KeyError) as e:
                    # 2022-08-08: NSE served a zipped .xlsx at the CSV URL. No data to recover; treat as a missing day.
                    print(f"{key}: unparseable file ({type(e).__name__}), recorded as no data")
                checked.add(key)
            except urllib.error.HTTPError as e:
                if e.code == 404 and (dt.date.today() - d).days > 3:  # recent 404 may just be "not published yet"
                    checked.add(key)
                elif e.code != 404:
                    print(f"{key}: HTTP {e.code}, will retry on rerun")  # review: this used to be swallowed silently
            except Exception as e:  # network blip: leave it unchecked, a rerun retries it
                print(f"{key}: {type(e).__name__}, will retry on rerun")
                time.sleep(3)
            done += 1
            if done % save_every == 0:
                save()
                print(f"{key}: {done} requested", flush=True)
            time.sleep(sleep)
        d += dt.timedelta(days=1)
    save()
    print("fetch done")


def load_delivery():
    df = pd.read_csv(CACHE, parse_dates=["date"])
    df = df[df["date"] >= "2019-10-01"]  # one illiquid symbol's stale 2019-06 DATE1 sits in a September-2019 file
    return df.pivot(index="date", columns="symbol", values="deliv_per").sort_index()


def nse_calendar(period="10y"):
    from data_yfinance import fetch_candles
    return pd.DatetimeIndex([c["date"].date() for c in fetch_candles("^NSEI", "1d", period)])


def load_fields(symbols, fields, period="10y"):
    """{field: DataFrame(date x symbol)} of Yahoo adjusted candle fields on the ^NSEI calendar. Zero volume -> NaN."""
    from data_yfinance import fetch_candles
    cols = {f: {} for f in fields}
    for sym in symbols:
        for _ in range(3):
            c = fetch_candles(sym + ".NS", "1d", period)
            if c:
                ix = pd.DatetimeIndex([x["date"].date() for x in c])
                for f in fields:
                    cols[f][sym] = pd.Series([x[f] for x in c], index=ix, dtype=float)
                break
        else:
            print(f"{sym}: fetch failed, excluded")
    cal = nse_calendar(period)
    out = {f: pd.DataFrame(cols[f]).sort_index().reindex(cal) for f in fields}
    if "volume" in out:
        out["volume"] = out["volume"].where(out["volume"] > 0)
    return out


# ---------------------------------------------------------------- signals and statistics
def signals(dp, close):
    """dp, close: date x symbol on ONE common calendar. Row t uses data through t only."""
    # min_periods 4 / 55: a missing file or bad Yahoo day must not blank 65 days of signal (Eighty-second review fix)
    d1 = dp.rolling(5, min_periods=4).mean() - dp.shift(5).rolling(60, min_periods=55).mean()
    d2 = d1 * np.sign(close / close.shift(5) - 1)
    return {"D1": d1, "D2": d2}


def fwd(close, h):
    """Return from the close of t+1 to the close of t+1+h (lag-1 entry), aligned to t."""
    return close.shift(-(1 + h)) / close.shift(-1) - 1


def zrank(X):
    R = X.rank(axis=1)
    return R.sub(R.mean(axis=1), axis=0).fillna(0.0).to_numpy(), X.notna().sum(axis=1).to_numpy()


def ic_rows(Sz, Fz):
    den = np.sqrt((Sz ** 2).sum(1) * (Fz ** 2).sum(1))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (Sz * Fz).sum(1) / den


def write_atomic(df, path):
    """Write a CSV via a temp file + rename, so an interrupt can never truncate an existing cache (review fix)."""
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def write_text_atomic(text, path):
    tmp = path + ".tmp"
    open(tmp, "w").write(text)
    os.replace(tmp, path)


def apply_rule(res, cost=COST_RT, alpha=0.05):
    """The pre-registered decision rule, in code (Entry 85 review: it had only been applied by eye).
    res: rows with universe, period, signal, h, ic, p, p_nw, ic_h1, ic_h2 and top5_excess (or gross_excess).
    A (signal, h) ADVANCES only if in EVERY universe x period cell present: IC > 0, max(p, p_nw) < alpha (both tests),
    both halves > 0, and the gross excess exceeds `cost`. Returns one row per (signal, h) with the counts."""
    ex = "top5_excess" if "top5_excess" in res.columns else "gross_excess"
    r = res.copy()
    r["p_reg"] = r[["p", "p_nw"]].max(axis=1)
    r["stat"] = (r.ic > 0) & (r.p_reg < alpha) & (r.ic_h1 > 0) & (r.ic_h2 > 0)
    r["econ"] = r[ex] > cost
    out = r.groupby(["signal", "h"]).agg(cells=("ic", "size"), stat_pass=("stat", "sum"),
                                         econ_pass=("econ", "sum"),
                                         both=("stat", lambda s: int((s & r.loc[s.index, "econ"]).sum())))
    out["advance"] = (out.both == out.cells) & (out.cells > 0)
    return out.reset_index()


def nw_p(ic, lag):
    """Mean IC's t-statistic with a Newey-West (Bartlett) standard error, and a two-sided normal-approximation p.
    Valid for PERSISTENT signals, where the circular-shift null is not centred at zero (Entry 86 review): it tests the level
    of the IC series directly and needs no shift null. lag should cover overlapping forward windows."""
    x = np.asarray(pd.Series(ic).dropna(), dtype=float)
    n = len(x)
    if n < 30:
        return np.nan, np.nan
    e = x - x.mean()
    v = e @ e / n
    for l in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - l / (lag + 1)) * (e[l:] @ e[:-l]) / n
    t = x.mean() / math.sqrt(max(v, 1e-30) / n)
    return t, math.erfc(abs(t) / math.sqrt(2))


def ic_test(S, F, rng, draws=DRAWS, min_shift=60, min_rows=200, avoid_mod=None):
    """Mean rank IC of S against F and its circular-shift p. Returns (mean_ic, p, n_days, ic_series).
    min_rows: fewer valid rows -> NaN (monthly panels have ~110 rows). avoid_mod: skip shifts that are multiples of it
    (a seasonal signal shifted by a whole number of years would reproduce the observed alignment exactly)."""
    Sz, ns = zrank(S)
    Fz, nf = zrank(F)
    T = len(Sz)
    ok = (ns >= MIN_NAMES) & (nf >= MIN_NAMES)
    ic = ic_rows(Sz, Fz)
    if ok.sum() < min_rows:
        return np.nan, np.nan, int(ok.sum()), pd.Series(np.where(ok, ic, np.nan), index=S.index)
    obs = np.nanmean(ic[ok])
    null = np.empty(draws)
    shifts = rng.integers(min_shift, T - min_shift, size=draws)
    if avoid_mod:
        bad = shifts % avoid_mod == 0
        while bad.any():
            shifts[bad] = rng.integers(min_shift, T - min_shift, size=int(bad.sum()))
            bad = shifts % avoid_mod == 0
    for i, k in enumerate(shifts):
        oks = (np.roll(ns, k) >= MIN_NAMES) & (nf >= MIN_NAMES)
        v = ic_rows(np.roll(Sz, k, axis=0), Fz)[oks]
        null[i] = np.nanmean(v) if oks.sum() >= min_rows else np.nan
    null = null[~np.isnan(null)]
    p = (1 + (np.abs(null) >= abs(obs) - 1e-15).sum()) / (1 + len(null))
    out = pd.Series(np.where(ok, ic, np.nan), index=S.index)
    out.attrs["null_z0"] = float(null.mean() / null.std()) if len(null) > 1 and null.std() > 0 else np.nan
    return obs, p, int(ok.sum()), out


def residualize(Y, Xs):
    """Each date: OLS of Y's cross-sectional ranks on the ranks of every X in Xs (+ intercept); returns the residual.
    Post-result robustness (unregistered): what is left of a signal once known ones are removed."""
    Yr = Y.rank(axis=1).to_numpy()
    Xr = [X.rank(axis=1).to_numpy() for X in Xs]
    out = np.full(Y.shape, np.nan)
    for i in range(len(Yr)):
        m = ~np.isnan(Yr[i])
        for x in Xr:
            m &= ~np.isnan(x[i])
        if m.sum() < MIN_NAMES:
            continue
        A = np.column_stack([np.ones(m.sum())] + [x[i][m] for x in Xr])
        beta = np.linalg.lstsq(A, Yr[i][m], rcond=None)[0]
        out[i, m] = Yr[i][m] - A @ beta
    return pd.DataFrame(out, index=Y.index, columns=Y.columns)


def spanning(seed=2):
    """D1 with I5 (intraday reversal), 5d reversal and V1 (abnormal volume) regressed out, per universe.
    Needs the Entry 83/84 panel caches (run probe_volume_signal.py and probe_ohlc_signals.py first)."""
    import probe_ohlc_signals as o
    import probe_volume_signal as v
    cal = nse_calendar()[nse_calendar() >= "2019-10-01"]
    dp_all = load_delivery().reindex(cal)
    oh, vp = pd.read_pickle(o.PANEL_CACHE), pd.read_pickle(v.PANEL_CACHE)
    rng = np.random.default_rng(seed)
    rows = []
    for u in oh:
        op, cl = oh[u]
        vol = vp[u][1].reindex(dp_all.index)
        cl, op = cl.reindex(dp_all.index), op.reindex(dp_all.index)
        dp = dp_all.reindex(columns=cl.columns)
        D1 = signals(dp, cl)["D1"]
        ctrl = [o.signals(op, cl)["I5"], -(cl / cl.shift(5) - 1), v.signals(vol, cl)["V1"]]
        R = residualize(D1, ctrl)
        D1 = D1.where(R.notna())  # review: score the raw signal on exactly the cells the residual has
        for h in HORIZONS:
            F = fwd(cl, h)
            raw = ic_test(D1, F, rng, draws=2000)
            res = ic_test(R, F, rng, draws=2000)
            rows.append(dict(universe=u, h=h, ic_raw=raw[0], p_raw=raw[1], ic_resid=res[0], p_resid=res[1]))
    return pd.DataFrame(rows)


def top_excess(S, F, k=5):
    """Mean over days of (mean fwd return of the k highest-signal names) - (mean fwd return of all names)."""
    out = []
    for s, f in zip(S.to_numpy(), F.to_numpy()):
        m = ~np.isnan(s) & ~np.isnan(f)
        if m.sum() < MIN_NAMES:
            continue
        idx = np.flatnonzero(m)
        top = idx[np.argsort(s[idx])[-k:]]
        out.append(f[top].mean() - f[idx].mean())
    return float(np.mean(out)) if out else np.nan


def run_signals(S, close, horizons, periods, rng):
    """S: {name: signal frame}; periods: [(label, boolean row mask or None)]. One row per (period, signal, horizon).
    Shared by the Eighty-second/-third/-fourth entries (review: three copies had to be kept in sync)."""
    rows = []
    for label, sel in periods:
        for sig, X in S.items():
            for h in horizons:
                F = fwd(close, h)
                Xp, Fp = (X, F) if sel is None else (X[sel], F[sel])
                mic, p, n, ic = ic_test(Xp, Fp, rng)
                v = ic.dropna()
                half = len(v) // 2
                t_nw, p_nw = nw_p(ic, h + 5)  # daily: forward windows overlap h days, signals use 5
                rows.append(dict(period=label, signal=sig, h=h, days=n, ic=mic, p=p, p_nw=p_nw, t_nw=t_nw,
                                 null_z0=ic.attrs.get("null_z0", np.nan),
                                 ic_h1=v.iloc[:half].mean() if len(v) else np.nan,
                                 ic_h2=v.iloc[half:].mean() if len(v) else np.nan,
                                 top5_excess=top_excess(Xp, Fp)))
    return pd.DataFrame(rows)


def run(dp, close, seed=1):
    return run_signals(signals(dp, close), close, HORIZONS, [("all", None)], np.random.default_rng(seed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download/append NSE delivery files (resumable)")
    ap.add_argument("--spanning", action="store_true", help="D1 after removing intraday/5d reversal and abnormal volume")
    a = ap.parse_args()
    if a.fetch:
        fetch()
        return
    if a.spanning:
        pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:9.4f}")
        print(spanning().to_string(index=False))
        return
    dp_all = load_delivery()
    cal = nse_calendar()[nse_calendar() >= "2019-10-01"]
    dp_all = dp_all.reindex(cal)  # Yahoo/NSE trading days only; days without a delivery file are NaN rows, tolerated
    print(f"delivery cache: {dp_all.index[0].date()}..{dp_all.index[-1].date()}, {len(dp_all)} days, {dp_all.shape[1]} symbols")
    pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:9.4f}")
    outs = []
    for name, syms in universes().items():
        have = [s for s in syms if s in dp_all.columns]
        print(f"\nuniverse {name}: {len(have)}/{len(syms)} symbols with delivery data")
        close = load_fields(have, ["close"])["close"].reindex(dp_all.index)
        dp = dp_all[have].reindex(columns=close.columns)
        res = run(dp, close)
        res.insert(0, "universe", name)
        print(res.to_string(index=False))
        outs.append(res)
    pd.concat(outs).to_csv(os.path.join(_HERE, "delivery_signal_results.csv"), index=False)


if __name__ == "__main__":
    main()
