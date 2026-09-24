"""
Eighty-fifth entry: stock-futures OPEN-INTEREST "buildup" signals and stock-OPTIONS put/call signals, cross-sectional, on
two disjoint NSE universes and two independent periods. Pre-registered here BEFORE any signal was scored against a return.
(Scope was widened from futures-only to include option put/call before any data was fetched or scored: the same files hold
both, so it costs no extra fetch.)

Why: "long/short buildup" (price and open interest moving together) is one of the most popular retail signals in India
and has never been tested here; every earlier mechanism used prices, volume or delivery. Stock-futures OI is a different
dimension (leveraged derivative positioning) and the F&O bhavcopy exists back to 2016 in both formats, so unlike delivery
(2019-10+) it gives an independent earlier period. Uses the daily rank-IC framework validated in Entries 82-84 (a positive
control, the known 5-day reversal, is detected at p~0.0005 by the same machinery).

DATA: NSE F&O bhavcopy, one file per ^NSEI trading day 2016-01..today, FUTSTK/OPTSTK (old format) and STF/STO (new
format) rows for the symbols of UNIV_A (`WIDE_UNIVERSE`, 52) and UNIV_B (`UNIVERSE_B`, 54) that have stock derivatives on
that day. Per symbol per day: futures OI summed over all listed expiries (robust to rollover), near-month futures OI and
close, and OPTION open interest and contracts traded summed over all strikes and expiries, separately for calls and puts.
Cache: fo_oi_cache.csv.gz. Prices: Yahoo adjusted closes on the ^NSEI calendar. F&O eligibility changes over time; a name simply
has no signal on days it had no futures. Panel calendar is ^NSEI's (never the fetched files').
SIGNALS at the close of day t (OI is the day's EOD figure, public that evening; direction fixed in advance as
"high => higher future return", the retail 'bullish buildup' reading):
  OI1 = (OI_t - OI_{t-5}) / mean(OI over days t-64..t-5)          positioning build (long or short side unknown)
  OI2 = OI1 x sign(close_t / close_{t-5} - 1)                     price up + OI up = long buildup (bullish), price down +
                                                                  OI up = short buildup (bearish); the classic reading
  PC1 = - ln( (put OI + 1) / (call OI + 1) )                      LOW put/call = bullish call positioning => long
  PC2 = - [ ln(put/call OI)_t - ln(put/call OI)_{t-5} ]           falling put/call over 5 days => long
  (Pan-Poteshman: put-heavy option activity predicts LOWER stock returns. The opposite, contrarian reading was Entry 56's
  hypothesis for the INDEX put/call and was rejected; here the stock-level direction is fixed as the informed-trading one.)
A significant IC of the opposite sign is reported as reversed, NOT adopted post hoc.
Windows tolerate a few missing days (5-day point needs both ends; 60-day baseline needs 55), as fixed in Entry 82's review.
FORWARD RETURN (lag 1): enter at close t+1, exit at close t+1+h, h in {1, 5, 10, 21}.
STATISTIC and NULL: as `probe_delivery_signal.py` (daily cross-sectional rank IC, >= 25 names per row, 4,000 circular
shifts >= 60 days, two-sided (count+1)/(n+1)), per period P1 = 2019-10-01..end, P2 = 2016-01..2019-09.
REGISTERED: 4 signals x 4 horizons x 2 universes x 2 periods = 64 tests.
POST-REVIEW CORRECTIONS (independent code review of THIS probe, after a first scoring; disclosed because the first three change
what is tested): (i) OI2 as first implemented, OI1 x sign(return), scored OI-down days opposite to the classic 'short covering /
long unwinding' reading, so it did not test the pre-registered hypothesis for half the sample. The registered OI2 is now
max(OI1, 0) x sign(return) (the OI-up quadrants the docstring describes); the original is kept as unregistered OI2X. The one
cell that had passed both tests under the original (A/P1/OI2/h=1) was flagged by the reviewer as dependent on that inverted half.
(ii) Option and futures OI collapse at monthly expiry (~-85% for options), so 5-day OI/put-call CHANGES straddling an expiry
measure the roll, not positioning, and roll size is a persistent name fixed effect that the shift null does not control;
unregistered variants OI1E/OI2E/PC2E exclude every row whose window contains a detected roll (~a quarter of days).
(iii) OI is in shares while prices are split-adjusted: 54 split/bonus events produced spurious OI jumps; OI is now put on a
consistent share basis using Yahoo split ratios. (iv) The decision rule was only applied by eye; `ds.apply_rule` implements it.
(v) The universe rule used each name's full-sample share of F&O days (look-ahead, and it cut universe A from 52 to 40); removed,
since a name simply has no signal on days without futures. (vi) The fetch is now crash-safe (atomic writes, save on
interrupt, permanent 404s recorded); the price cache refreshes when the calendar advances.
PRE-SCORING AMENDMENTS (made after Entry 86's independent review, before any OI signal was scored against a return):
  (a) the circular-shift null is not centred for a persistent signal (put/call ratios and OI levels persist), so every
      cell reports BOTH p_shift and p_nw (Newey-West t-test of the mean IC, lag h+5) plus null_z0 (null mean / sd); a cell
      counts as significant only if BOTH are < 0.05, and the registered p is max(p_shift, p_nw);
  (b) put/call is NaN where a name has no listed option OI at all (call + put = 0); otherwise log(1/1) = 0 would pose as a
      perfectly neutral ratio (968 call-side and 1,338 put-side zero rows exist in the cache);
  (c) the F&O file for 2021-03-30 does not exist on NSE (HTTP 404); it is a missing day, tolerated by the 4-of-5 / 55-of-60
      windows. Data audit before scoring: 2,639 of 2,641 ^NSEI days present, no duplicates, no NaN totals.
DECISION RULE (fixed now): a signal/horizon advances only if in BOTH universes and BOTH periods the mean IC > 0 with
uncorrected p < 0.05 (both tests, see (a)), both halves of each period are positive, and the gross top-5 excess over h days exceeds the 0.25%
round-trip cost. Otherwise null or "information without economics". An advance still faces the honest-family Bonferroni
threshold (m ~ 810) and is only a hypothesis for forward tracking. Universe B/A membership is today's constituents
(survivorship), which flatters an earlier period more than a recent one.
"""

import argparse
import datetime as dt
import io
import os
import time

import numpy as np
import pandas as pd

import probe_delivery_signal as ds

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_HERE, "fo_oi_cache.csv.gz")
CHECKED = os.path.join(_HERE, "fo_oi_checked.txt")
SPLIT = pd.Timestamp("2019-10-01")
FIRST = pd.Timestamp("2016-01-01")
PANEL_CACHE = os.path.join(_HERE, "oi_panel_cache.pkl")  # git-ignored: Yahoo history revises


# ---------------------------------------------------------------- data
def parse_fo(text, fmt, symbols):
    """One F&O bhavcopy -> per-symbol rows for stock futures and stock options:
    symbol, oi_total/oi_near/near_close/contracts (futures), call_oi/put_oi/call_vol/put_vol (options, all strikes+expiries)."""
    df = pd.read_csv(io.StringIO(text), low_memory=False)
    if fmt == "old":
        kind = df["INSTRUMENT"].map({"FUTSTK": "F", "OPTSTK": "O"})
        f = pd.DataFrame({"kind": kind, "symbol": df["SYMBOL"].str.strip(),
                          "expiry": pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y"), "cp": df["OPTION_TYP"].str.strip(),
                          "close": df["CLOSE"], "oi": df["OPEN_INT"], "vol": df["CONTRACTS"]})
    else:
        kind = df["FinInstrmTp"].map({"STF": "F", "STO": "O"})
        f = pd.DataFrame({"kind": kind, "symbol": df["TckrSymb"].str.strip(), "expiry": pd.to_datetime(df["XpryDt"]),
                          "cp": df["OptnTp"].astype(str).str.strip(), "close": df["ClsPric"], "oi": df["OpnIntrst"],
                          "vol": df["TtlTradgVol"]})
    f = f[f["kind"].notna() & f["symbol"].isin(symbols)]
    out = []
    for sym, g in f.groupby("symbol"):
        r = dict(symbol=sym)
        fut = g[g["kind"] == "F"]
        if len(fut):
            near = fut.loc[fut["expiry"].idxmin()]
            r.update(oi_total=float(fut["oi"].sum()), oi_near=float(near["oi"]), near_close=float(near["close"]),
                     contracts=float(fut["vol"].sum()))
        opt = g[g["kind"] == "O"]
        for cp, name in (("CE", "call"), ("PE", "put")):
            x = opt[opt["cp"] == cp]
            r[f"{name}_oi"] = float(x["oi"].sum()) if len(x) else np.nan
            r[f"{name}_vol"] = float(x["vol"].sum()) if len(x) else np.nan
        out.append(r)
    return pd.DataFrame(out)


def fetch(sleep=0.15, save_every=150):
    import probe_iron_condor_real_data as ic
    syms = set().union(*ds.universes().values())
    cal = ds.nse_calendar("20y")
    cal = cal[cal >= FIRST]
    checked = set(open(CHECKED).read().split()) if os.path.exists(CHECKED) else set()
    old = pd.read_csv(CACHE, parse_dates=["date"]) if os.path.exists(CACHE) else pd.DataFrame()
    new, done = [], 0

    def save():  # atomic (temp file + rename): an interrupt can never truncate the cache (review fix)
        ds.write_atomic(pd.concat([old] + new).drop_duplicates(["date", "symbol"]), CACHE)
        ds.write_text_atomic("\n".join(sorted(checked)), CHECKED)

    try:
        for d in cal:
            key = d.date().isoformat()
            if key in checked:
                continue
            try:
                text, fmt = ic.fetch_fo_bhavcopy(d.date())
                rows = parse_fo(text, fmt, syms)
                rows.insert(0, "date", d)
                new.append(rows)
                checked.add(key)
            except Exception as e:  # never swallow silently; a known permanent 404 is recorded so reruns skip it
                print(f"{key}: {type(e).__name__}: {str(e)[:60]}", flush=True)
                if "HTTP 404" in str(e) and (pd.Timestamp.today() - d).days > 3:
                    checked.add(key)  # e.g. 2021-03-30: NSE never published it
                time.sleep(2)
            done += 1
            if done % save_every == 0:
                save()
                print(f"{key}: {done} requested", flush=True)
            time.sleep(sleep)
    finally:
        save()  # also on Ctrl-C / a fatal error
    print("fetch done")


def load_cache():
    """{field: DataFrame(date x symbol)} for oi_total, call_oi, put_oi, near_close."""
    df = pd.read_csv(CACHE, parse_dates=["date"])
    return {k: df.pivot(index="date", columns="symbol", values=k).sort_index()
            for k in ("oi_total", "call_oi", "put_oi", "near_close")}


def load_splits(symbols):
    """{symbol: Series of split/bonus ratios by ex-date} from Yahoo (a 10-for-1 split is 10.0, a 1:1 bonus 2.0)."""
    import yfinance as yf
    out = {}
    for sym in symbols:
        sp = None
        for _ in range(3):
            try:
                sp = yf.Ticker(sym + ".NS").splits
                break
            except Exception:
                time.sleep(1)
        if sp is not None and len(sp):
            sp = sp[sp > 0]
            out[sym] = pd.Series(sp.to_numpy(), index=pd.DatetimeIndex([t.date() for t in sp.index]))
    return out


def adjust_oi(oi, splits):
    """OI is reported in shares while Yahoo prices are split-adjusted, so a k-for-1 split multiplies post-split OI by ~k
    (54 such events in this cache). Rows BEFORE each ex-date are multiplied by the ratio: OI on today's share basis."""
    adj = oi.copy()
    for sym, sp in splits.items():
        if sym not in adj.columns:
            continue
        for d, r in sp.items():
            if r > 0 and r != 1:
                adj.loc[adj.index < d, sym] *= r
    return adj


def expiry_windows(call_oi, window=5, thresh=-0.7):
    """True on day t if the 5-day look-back window (t-4..t) contains a monthly-expiry roll, detected from the data: the
    cross-sectional median of the daily log change in total call OI collapses (~-1.9, about -85%) on expiry day."""
    lc = np.log(call_oi.where(call_oi > 0) / call_oi.shift(1))
    flag = (lc.median(axis=1) < thresh).astype(float)
    return flag.rolling(window, min_periods=1).max().astype(bool)


# ---------------------------------------------------------------- signals
REGISTERED = ("OI1", "OI2", "PC1", "PC2")


def signals(oi, close, call_oi=None, put_oi=None, roll=None):
    """All frames date x symbol on the ^NSEI calendar. Row t uses data through t only.
    OI2 (registered) = max(OI1, 0) x sign(5d return): the classic reading covers only the OI-UP quadrants (long buildup =
    price up + OI up, short buildup = price down + OI up). OI2X = OI1 x sign(return), the original implementation, which also
    scored OI-down days with the opposite sign to the retail 'short covering / long unwinding' reading (review finding);
    kept as an unregistered variant. `roll` (a boolean row mask from expiry_windows) adds unregistered variants OI1E/OI2E/PC2E
    with every row whose 5-day window straddles a monthly expiry set to NaN."""
    base = oi.shift(5).rolling(60, min_periods=55).mean()
    oi1 = (oi - oi.shift(5)) / base
    sgn = np.sign(close / close.shift(5) - 1)
    out = {"OI1": oi1, "OI2": oi1.clip(lower=0) * sgn, "OI2X": oi1 * sgn}
    if call_oi is not None:
        none_listed = (call_oi + put_oi) == 0        # no option OI at all: NaN, not a fake neutral log(1/1) = 0
        lpc = np.log((put_oi + 1) / (call_oi + 1)).where(~none_listed)
        out["PC1"] = -lpc
        out["PC2"] = -(lpc - lpc.shift(5))
    if roll is not None:
        keep = ~roll.reindex(oi.index).fillna(False)
        for k in ("OI1", "OI2") + (("PC2",) if call_oi is not None else ()):
            out[k + "E"] = out[k].where(keep, axis=0)
    return out


def run(oi, close, call_oi=None, put_oi=None, roll=None, seed=1):
    periods = [("P1", close.index >= SPLIT), ("P2", close.index < SPLIT)]
    res = ds.run_signals(signals(oi, close, call_oi, put_oi, roll), close, ds.HORIZONS, periods, np.random.default_rng(seed))
    res["registered"] = res["signal"].isin(REGISTERED)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download/append F&O bhavcopies (resumable, crash-safe)")
    ap.add_argument("--refresh", action="store_true", help="ignore the local price/split panel cache")
    a = ap.parse_args()
    if a.fetch:
        fetch()
        return
    pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:9.4f}")
    P = load_cache()
    oi_all, call_all = P["oi_total"], P["call_oi"]
    print(f"F&O cache: {oi_all.index[0].date()}..{oi_all.index[-1].date()}, {len(oi_all)} days, {oi_all.shape[1]} symbols with futures")
    cal = ds.nse_calendar("20y")
    cal = cal[cal >= FIRST]
    store = pd.read_pickle(PANEL_CACHE) if os.path.exists(PANEL_CACHE) and not a.refresh else {}
    if "splits" not in store:
        store["splits"] = load_splits(oi_all.columns)
    roll = expiry_windows(call_all.reindex(cal))
    print(f"expiry-roll windows flagged: {int(roll.sum())} of {len(roll)} days ({roll.mean():.0%})")
    outs = []
    for name, syms in ds.universes().items():
        # eligibility is per day (a name has a signal only on days it had futures), so NO full-sample share-of-days filter
        # (that used future F&O status: review finding); only require a year of OI data for the name to be usable at all.
        have = [s for s in syms if s in oi_all.columns and oi_all[s].notna().sum() >= 250]
        cl = store.get(name)
        stale = cl is None or store.get("_end") is None or store["_end"] < cal[-1] or bool(set(have) - set(cl.columns))
        if stale:
            cl = ds.load_fields(have, ["close"], "20y")["close"]
            store[name] = cl
            store["_end"] = cal[-1]
            pd.to_pickle(store, PANEL_CACHE)
        have = [s for s in have if s in cl.columns]  # a symbol whose price fetch failed is dropped, never a KeyError
        print(f"\nuniverse {name}: {len(have)}/{len(syms)} names with >= 250 days of futures data")
        cl = cl[have].reindex(cal)
        oi = adjust_oi(oi_all[have].reindex(cal), store["splits"])
        call, put = call_all.reindex(columns=have).reindex(cal), P["put_oi"].reindex(columns=have).reindex(cal)
        res = run(oi, cl, call, put, roll)
        res.insert(0, "universe", name)
        outs.append(res)
    res = pd.concat(outs)
    res.to_csv(os.path.join(_HERE, "oi_signal_results.csv"), index=False)
    for label, sub in (("REGISTERED (64 tests)", res[res.registered]), ("UNREGISTERED robustness", res[~res.registered])):
        print(f"\n=== {label}")
        print(sub.drop(columns=["registered"]).to_string(index=False))
        rule = ds.apply_rule(sub)
        print(f"\ndecision rule ({label}): advancing signals = {rule[rule.advance][['signal', 'h']].values.tolist() or 'none'}")
        print(rule[rule.stat_pass > 0].sort_values(["stat_pass", "both"], ascending=False).head(8).to_string(index=False))


if __name__ == "__main__":
    main()
