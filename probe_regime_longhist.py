"""
Eighty-first entry: which sectors / factors / asset classes did better AFTER past oil-shock (and oil-shock + rising-
yield) regimes? Long-history, monthly, US data 1927-2026. Pre-registered here BEFORE any outcome was computed.

Why: Entry 57 tested whether a nearest-neighbour "analog" forecaster has skill (no) and described S&P/Sensex returns
after 10 Brent-shock episodes 1989-2022. It never asked which *strategies or sectors* did well, and 2007+ NSE data
contains almost no oil-shock-plus-tightening episodes. US history back to 1947 has more. n stays small, so this is
an honest low-power look, not a strategy. Events the rules flag (printed BEFORE any return was computed): A = 1948-01,
1974-01, 1979-07, 1989-01, 1990-08, 1994-06, 1999-04, 2003-02, 2004-10, 2007-11, 2021-01, 2022-03 and the CURRENT
2026-03 onset; B = 1979-10, 1994-06, 1999-04, 2021-02, 2022-03. (2021-01/02 is the post-COVID oil rebound, not a supply
shock; the mechanical rule counts it and it is kept.) Regime B has only 4 events with complete windows.

REGIMES, at month-end t (uses data through t only; forward windows start month t+1):
  SHOCK (A):  WTI monthly avg (FRED WTISPLC) rose >= 30% over the last 3 months AND is the highest of the last 12 months.
  SHOCK+HIKING (B):  A and FRED GS10 rose >= 0.50pp over the last 6 months.
  Events are declustered greedily (>= 12 months after the previous accepted event). An event whose forward window
  is not complete is excluded from the statistics and listed separately as CURRENT.
  Both definitions were fixed from Entry 57's mechanical Brent rule (+40%/60d at a 252d high, monthly analog) and the
  Entry 57 state report (10y up ~45bp/60d) BEFORE looking at any return; they were not tuned.

SERIES (monthly returns; Ken French data library, FRED, datasets/gold-prices):
  MKT (Mkt-RF+RF), SMB, HML, MOM (long-short factor returns), GOLD (from 1971-09, market-priced era),
  BOND10 (APPROXIMATE: y/12 - D*dy from FRED GS10 monthly averages, par-bond duration), OILREL (48-industry "Oil"
  minus MKT), GUNSREL ("Guns" = defense, minus MKT), TREND (hold MKT if its total-return index was above its 10-month
  average at the previous month-end, else T-bill). Plus an exploratory scan: every one of the 48 industries minus MKT.
STATISTIC: mean forward h-month compounded return after regime events MINUS the same series' mean over all eligible
  months, h in {3, 6, 12}. Two-sided p from 20,000 random draws of the same number of eligible months, drawn under the
  same >= 12-month spacing (seeded). REGISTERED: 9 series x 3 horizons x 2 regimes = 54 tests. The industry scan is
  48 x 3 x 2 = 288 further cells, counted into the honest family as unregistered scan cells.
  CODE-REVIEW CORRECTION (after results were seen): GS10 and the gold file are monthly averages, which leak in-flag-month
  drift into a "forward" return. BOND10 was re-registered on month-end DGS10 and GOLD on a window that skips month t+1;
  the original averaged versions (BOND10AVG, GOLDAVG; 12 cells) are reported as unregistered robustness rows.
COSTS: none (these are regime descriptions of indices/factors, not tradable rules). Trend gate is costless too.
n is single digits: only very large effects can clear a corrected bar; the point of the table is direction and dispersion.
"""

import argparse
import io
import os
import urllib.request
import zipfile

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_HERE, "longhist_cache.csv")  # committed: French/FRED revise history, so results must be reproducible
FRENCH = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
GOLD_URL = "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv"
GAP = 12
DRAWS = 20000
HORIZONS = (3, 6, 12)
GOLD_START = "1971-09"  # before this gold was administratively priced
REGISTERED = ["MKT", "SMB", "HML", "MOM", "GOLD", "BOND10", "OILREL", "GUNSREL", "TREND"]
# Code review (Eighty-first): GS10 and the gold file are monthly AVERAGES, so a return measured from month t's average
# already contains drift from inside the flag month (known at the flag date). Registered BOND10 is therefore built from
# month-END DGS10, and GOLD's forward window skips month t+1 (SKIP). The original leaky series are kept as
# unregistered robustness rows so the size of the artifact stays visible.
ROBUSTNESS = ["BOND10AVG", "GOLDAVG"]
SKIP = {"GOLD": 1}  # forward window = averages of months t+1 .. t+1+h, i.e. compounded returns t+2 .. t+h+1


# ---------------------------------------------------------------- data
def french(name):
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(FRENCH + name, timeout=60).read()))
    lines = z.read(z.namelist()[0]).decode("latin1").splitlines()
    rows, cols = [], None
    for i, ln in enumerate(lines):
        f = [x.strip() for x in ln.split(",")]
        if len(f[0]) == 6 and f[0].isdigit():
            if cols is None:
                cols = [c.strip() for c in lines[i - 1].split(",")][1:]
            rows.append(f[: len(cols) + 1])
        elif rows:
            break  # first monthly block only; the annual block follows
    df = pd.DataFrame(rows).set_index(0).astype(float)
    df.columns = cols
    df.index = pd.PeriodIndex(pd.to_datetime(df.index, format="%Y%m"), freq="M")
    return df.where(df > -99) / 100


def fred(series):
    raw = urllib.request.urlopen(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=30).read()
    s = pd.read_csv(io.BytesIO(raw), index_col=0, parse_dates=True).iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = s.index.to_period("M")
    return s


def fred_daily(series):
    raw = urllib.request.urlopen(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=60).read()
    s = pd.read_csv(io.BytesIO(raw), index_col=0, parse_dates=True).iloc[:, 0]
    return pd.to_numeric(s, errors="coerce").dropna()


def bond_return(gs10):
    """Approximate monthly 10y Treasury total return from monthly-average yields (percent): carry y/12 minus
    modified duration of a par bond at the prior yield times the yield change. Monthly averaging smooths dy, so
    this understates bond volatility; direction and size in big bond bear/bull years are right."""
    y = gs10 / 100
    yp = y.shift(1)
    dur = (1 - (1 + yp / 2) ** -20) / yp
    return yp / 12 - dur * (y - yp)


def build():
    f3 = french("F-F_Research_Data_Factors_CSV.zip")
    mom = french("F-F_Momentum_Factor_CSV.zip")
    ind = french("48_Industry_Portfolios_CSV.zip")
    wti, gs10 = fred("WTISPLC"), fred("GS10")
    dgs = fred_daily("DGS10")
    gs10_end = dgs.groupby(dgs.index.to_period("M")).last()  # month-END yield: no averaging autocorrelation
    gs10_end = gs10_end[gs10_end.index <= gs10.index[-1]]     # drop the still-forming current month
    gold = pd.read_csv(GOLD_URL)
    gold.index = pd.PeriodIndex(pd.to_datetime(gold.iloc[:, 0]), freq="M")
    gold = gold.iloc[:, 1]
    gold = gold[~gold.index.duplicated()]
    d = pd.DataFrame({"rf": f3["RF"], "mkt": f3["Mkt-RF"] + f3["RF"], "smb": f3["SMB"], "hml": f3["HML"],
                      "mom": mom.iloc[:, 0], "wti": wti, "gs10": gs10, "bond10": bond_return(gs10_end), "bond10avg": bond_return(gs10),
                      "gold": gold.pct_change().where(gold.index >= pd.Period(GOLD_START, "M"))})
    for c in ind.columns:
        d["ind_" + c] = ind[c]
    return d.sort_index().loc["1926-01":]


def load(refresh=False):
    if refresh or not os.path.exists(CACHE):
        d = build()
        d.to_csv(CACHE)
        return d
    d = pd.read_csv(CACHE, index_col=0)
    d.index = pd.PeriodIndex(d.index, freq="M")
    return d


# ---------------------------------------------------------------- regimes and forward windows
def decluster(flags, gap=GAP):
    """Greedy, in time order: accept a flagged row only if >= gap rows after the last accepted one."""
    out, last = [], -10 ** 9
    for i in np.flatnonzero(flags):
        if i - last >= gap:
            out.append(i)
            last = i
    return np.array(out, dtype=int)


def regime_flags(wti, gs10):
    w, g = pd.Series(wti), pd.Series(gs10)
    shock = ((w / w.shift(3) - 1) >= 0.30) & (w >= w.rolling(12, min_periods=12).max())
    hike = (g - g.shift(6)) >= 0.50
    return {"A": shock.fillna(False).to_numpy(), "B": (shock & hike).fillna(False).to_numpy()}


def forward(x, h):
    """Compounded return over months t+1..t+h, aligned to t. NaN if any month in the window is missing."""
    lg = np.log1p(pd.Series(x))
    return np.expm1(lg.rolling(h, min_periods=h).sum().shift(-h)).to_numpy()


def fwd_for(name, x, h):
    """Forward-return vector for a named series, applying that series' SKIP (in months) if it has one."""
    f = forward(x, h)
    k = SKIP.get(name, 0)
    return f if k == 0 else np.r_[f[k:], np.full(k, np.nan)]


def trend_series(mkt, rf, n=10):
    """Month-s return = mkt if the total-return index at s-1 was above its n-month mean (known at end of s-1).
    Months without a full n-month mean at s-1 are NaN (no signal), never silently risk-off."""
    idx = (1 + pd.Series(mkt)).cumprod()
    ma = idx.rolling(n, min_periods=n).mean()
    ok = np.r_[False, ma.notna().to_numpy()[:-1]]
    on = np.r_[False, (idx > ma).to_numpy()[:-1]]
    out = np.where(on, mkt, rf)
    out[~ok] = np.nan
    return out


def random_spaced(valid_idx, n, draws, rng, gap=GAP):
    """`draws` random sets of n indices from valid_idx with all pairwise gaps >= gap (rejection sampling)."""
    got = []
    while sum(len(g) for g in got) < draws:
        c = np.sort(rng.choice(valid_idx, size=(draws, n)), axis=1)
        ok = (np.diff(c, axis=1) >= gap).all(axis=1) & (np.diff(c, axis=1) > 0).all(axis=1)
        got.append(c[ok])
    return np.concatenate(got)[:draws]


def regime_test(F, events, eligible, rng, draws=DRAWS):
    """F: forward-return vector for one series/horizon; eligible: months the regime could have been flagged in
    (the null draws only from these, so it never samples a month before the inputs existed).
    Returns (n, mean_after, mean_all, diff, two-sided p)."""
    valid = np.flatnonzero(~np.isnan(F) & eligible)
    ev = events[np.isin(events, valid)]
    if len(ev) < 2 or len(valid) < 4 * GAP * len(ev):
        return len(ev), np.nan, np.nan, np.nan, np.nan
    base = F[valid].mean()
    obs = F[ev].mean() - base
    null = F[random_spaced(valid, len(ev), draws, rng)].mean(axis=1) - base
    p = (1 + (np.abs(null) >= abs(obs) - 1e-15).sum()) / (1 + len(null))
    return len(ev), F[ev].mean(), base, obs, p


def series_matrix(d):
    s = {"MKT": d["mkt"].to_numpy(), "SMB": d["smb"].to_numpy(), "HML": d["hml"].to_numpy(),
         "MOM": d["mom"].to_numpy(), "GOLD": d["gold"].to_numpy(), "BOND10": d["bond10"].to_numpy(), "BOND10AVG": d["bond10avg"].to_numpy(),
         "GOLDAVG": d["gold"].to_numpy(),  # robustness only: monthly-average gold, unskipped (leaky)
         "OILREL": (d["ind_Oil"] - d["mkt"]).to_numpy(), "GUNSREL": (d["ind_Guns"] - d["mkt"]).to_numpy(),
         "TREND": trend_series(d["mkt"].to_numpy(), d["rf"].to_numpy())}
    return s


def run(d, names, series, seed=1):
    rng = np.random.default_rng(seed)
    flags = regime_flags(d["wti"].to_numpy(), d["gs10"].to_numpy())
    w, g = d["wti"], d["gs10"]
    okA = w.rolling(12, min_periods=12).min().notna().to_numpy()          # WTI has a full 12-month window
    okB = okA & (g - g.shift(6)).notna().to_numpy()                        # ...and GS10 has its 6-month change
    elig = {"A": okA, "B": okB}
    rows = []
    for reg in "AB":
        ev = decluster(flags[reg])
        for name in names:
            x = series[name]
            for h in HORIZONS:
                n, after, base, diff, p = regime_test(fwd_for(name, x, h), ev, elig[reg], rng)
                rows.append(dict(regime=reg, series=name, h=h, n=n, after=after, base=base, diff=diff, p=p))
    return pd.DataFrame(rows), {r: decluster(flags[r]) for r in "AB"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download and overwrite the committed cache")
    ap.add_argument("--scan", action="store_true", help="also run the 48-industry exploratory scan")
    a = ap.parse_args()
    d = load(a.refresh)
    print(f"data: {d.index[0]}..{d.index[-1]}  (WTI to {d['wti'].dropna().index[-1]}, GS10 to {d['gs10'].dropna().index[-1]}, "
          f"French to {d['mkt'].dropna().index[-1]})")
    series = series_matrix(d)
    res, events = run(d, REGISTERED + ROBUSTNESS, series)
    pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:8.4f}")
    for reg in "AB":
        print(f"\n=== regime {reg}: events {[str(d.index[i]) for i in events[reg]]}")
        r = res[res.regime == reg]
        print(r[["series", "h", "n", "after", "base", "diff", "p"]].to_string(index=False))
    show = ["MKT", "MOM", "GOLD", "BOND10", "OILREL"]
    for reg in "AB":
        print(f"\n=== regime {reg} per episode: forward 3m / 12m compounded (blank = window incomplete or no data)")
        rows = []
        for i in events[reg]:
            row = {"event": str(d.index[i])}
            for nm in show:
                for h in (3, 12):
                    v = fwd_for(nm, series[nm], h)[i]
                    row[f"{nm}{h}"] = "" if np.isnan(v) else f"{v * 100:+.1f}%"
            rows.append(row)
        print(pd.DataFrame(rows).to_string(index=False))
    if a.scan:
        names = [c[4:] for c in d.columns if c.startswith("ind_")]
        sc = {n: (d["ind_" + n] - d["mkt"]).to_numpy() for n in names}
        sres, _ = run(d, names, sc, seed=2)
        sres.to_csv(os.path.join(_HERE, "regime_longhist_scan.csv"), index=False)
        ok = sres.dropna(subset=["p"])
        import multiple_comparisons as mc
        m = len(mc.ALL_SIGNIFICANCE_TESTS_PVALUES) + mc.UNREGISTERED_SCAN_CELLS
        print(f"\nscan: {len(ok)} cells with a p-value, {(ok.p < 0.05).sum()} under 0.05 uncorrected "
              f"(chance alone gives ~{0.05 * len(ok):.0f}), min p {ok.p.min():.4f}, "
              f"{(ok.p < 0.05 / m).sum()} under the honest-family (m={m}) Bonferroni threshold {0.05 / m:.6f}")
        print("\n=== industry scan (each minus MKT), top/bottom 8 by |diff| among h=12, uncorrected p:")
        s12 = sres[sres.h == 12].sort_values("diff")
        print(s12.head(8).to_string(index=False))
        print(s12.tail(8).to_string(index=False))
    res.to_csv(os.path.join(_HERE, "regime_longhist_results.csv"), index=False)


if __name__ == "__main__":
    main()
