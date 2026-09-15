"""
Weekly Nifty iron condor, backtested against REAL NSE option-chain settlement
prices instead of the Black-Scholes-synthesized premiums used in the earlier
probe (see CLAUDE.md's "Seventh: options premium selling" entry). Real prices
come from NSE's own published F&O bhavcopy files, fetched for free via two
sources:

  - `jugaad-data` (MIT-licensed, https://github.com/jugaad-py/jugaad-data)
    for the old bhavcopy format (through mid-2024)
  - NSE's own new-format UDIFF F&O bhavcopy, fetched directly (jugaad-data's
    exposed `bhavcopy_udiff_raw` only covers the equity/CM segment, not F&O,
    in the installed version — the F&O UDIFF path was found by inspecting
    the CM UDIFF URL pattern and probing the equivalent `/content/fo/`  path)

Both are genuinely free, no API key, no paid Kite Connect "Historical" tier.

Method per weekly cycle:
  1. Pick a Monday (or nearest trading day) as entry.
  2. Fetch that day's real F&O bhavcopy, filter to NIFTY index options
     (OPTIDX/IDO rows) for the nearest expiry >= entry date.
  3. Spot: `UndrlygPric` column (new format) or the FUTIDX NIFTY near-month
     future's close (old format, no underlying column) as a spot proxy.
  4. Short strikes ~2% OTM, wings ~1% further out (matching the parameters
     documented in CLAUDE.md's synthetic iron-condor run), rounded to the
     nearest real Nifty strike (50-point steps). Real market CLOSE price for
     each of the 4 exact strikes = the real net credit received.
  5. At expiry, use yfinance ^NSEI close on the expiry date as the
     settlement spot (already this project's trusted data source elsewhere)
     and compute the condor's defined-risk payoff analytically.

This is NOT a claim of perfect fill realism (no bid-ask spread modeled, only
last-traded/settlement CLOSE; no slippage) — but it replaces the Black-Scholes
flat-IV synthesis with real observed market prices, which is the specific gap
CLAUDE.md flagged as unvalidated.
"""

import datetime
import sys
import time

import yfinance as yf
import jugaad_data.nse as nse

LOT_SIZE = 65  # current Nifty lot size, used uniformly for comparability
COST_PER_CYCLE_RUPEES = 100  # flat estimate: 4 legs, brokerage + STT + exchange charges

SHORT_OTM_PCT = 0.02
WING_EXTRA_PCT = 0.01


def round_to_strike(price, step=50):
    return round(price / step) * step


def fetch_fo_bhavcopy(d):
    """Return raw bhavcopy text for date d, trying the old format first,
    then NSE's new-format F&O UDIFF file directly."""
    try:
        return nse.bhavcopy_fo_raw(d), "old"
    except Exception:
        pass

    import requests

    ymd = d.strftime("%Y%m%d")
    url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"no bhavcopy for {d} (HTTP {r.status_code})")
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(z.namelist()[0]) as f:
        return f.read().decode("utf-8"), "new"


def parse_nifty_options(text, fmt):
    """Return list of dicts: {expiry: date, strike: float, opt_type: 'CE'/'PE', close: float, underlying: float or None}"""
    lines = text.splitlines()
    header = lines[0].split(",")
    rows = []
    if fmt == "old":
        # INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,...
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < 10:
                continue
            if parts[0] == "OPTIDX" and parts[1] == "NIFTY":
                try:
                    expiry = datetime.datetime.strptime(parts[2], "%d-%b-%Y").date()
                    rows.append(
                        {
                            "expiry": expiry,
                            "strike": float(parts[3]),
                            "opt_type": parts[4],
                            "close": float(parts[8]),
                            "underlying": None,
                        }
                    )
                except ValueError:
                    continue
        # underlying proxy: FUTIDX NIFTY near-month close
        fut_closes = []
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < 10:
                continue
            if parts[0] == "FUTIDX" and parts[1] == "NIFTY":
                try:
                    expiry = datetime.datetime.strptime(parts[2], "%d-%b-%Y").date()
                    fut_closes.append((expiry, float(parts[8])))
                except ValueError:
                    continue
        underlying = min(fut_closes, key=lambda x: x[0])[1] if fut_closes else None
        for r in rows:
            r["underlying"] = underlying
    else:
        idx = {name: i for i, name in enumerate(header)}
        for l in lines[1:]:
            parts = l.split(",")
            if len(parts) < len(header) - 4:
                continue
            if parts[idx["TckrSymb"]] != "NIFTY":
                continue
            if parts[idx["FinInstrmTp"]] != "IDO":
                continue
            try:
                expiry = datetime.datetime.strptime(parts[idx["XpryDt"]], "%Y-%m-%d").date()
                rows.append(
                    {
                        "expiry": expiry,
                        "strike": float(parts[idx["StrkPric"]]),
                        "opt_type": parts[idx["OptnTp"]],
                        "close": float(parts[idx["ClsPric"]]),
                        "underlying": float(parts[idx["UndrlygPric"]]),
                    }
                )
            except (ValueError, IndexError):
                continue
    return rows


def find_leg(rows, expiry, strike, opt_type):
    for r in rows:
        if r["expiry"] == expiry and r["strike"] == strike and r["opt_type"] == opt_type:
            return r["close"]
    return None


def condor_expiry_payoff(spot, short_call, long_call, short_put, long_put, net_credit):
    call_loss = max(0.0, min(spot - short_call, long_call - short_call))
    put_loss = max(0.0, min(short_put - spot, short_put - long_put))
    return net_credit - call_loss - put_loss


def run_cycle(entry_date, nifty_daily):
    try:
        text, fmt = fetch_fo_bhavcopy(entry_date)
    except Exception as e:
        return None, f"no bhavcopy: {e}"

    rows = parse_nifty_options(text, fmt)
    if not rows:
        return None, "no NIFTY option rows"

    expiries = sorted({r["expiry"] for r in rows if r["expiry"] >= entry_date})
    if not expiries:
        return None, "no future expiry found"
    expiry = expiries[0]

    spot = next((r["underlying"] for r in rows if r["expiry"] == expiry and r["underlying"]), None)
    if not spot:
        return None, "no spot/underlying found"

    short_call = round_to_strike(spot * (1 + SHORT_OTM_PCT))
    long_call = round_to_strike(spot * (1 + SHORT_OTM_PCT + WING_EXTRA_PCT))
    short_put = round_to_strike(spot * (1 - SHORT_OTM_PCT))
    long_put = round_to_strike(spot * (1 - SHORT_OTM_PCT - WING_EXTRA_PCT))

    sc_px = find_leg(rows, expiry, short_call, "CE")
    lc_px = find_leg(rows, expiry, long_call, "CE")
    sp_px = find_leg(rows, expiry, short_put, "PE")
    lp_px = find_leg(rows, expiry, long_put, "PE")
    if None in (sc_px, lc_px, sp_px, lp_px):
        return None, f"missing leg price(s) for expiry {expiry}, spot {spot}"

    net_credit = (sc_px - lc_px) + (sp_px - lp_px)

    # settlement spot via yfinance close on/near expiry date
    settle_row = nifty_daily.loc[nifty_daily.index.date <= expiry]
    if settle_row.empty:
        return None, "no settlement price"
    settle_spot = float(settle_row.iloc[-1]["Close"])

    pnl_points = condor_expiry_payoff(settle_spot, short_call, long_call, short_put, long_put, net_credit)
    pnl_rupees = pnl_points * LOT_SIZE - COST_PER_CYCLE_RUPEES

    return {
        "entry_date": entry_date,
        "expiry": expiry,
        "spot": spot,
        "settle_spot": settle_spot,
        "strikes": (short_put, long_put, short_call, long_call),
        "net_credit_points": net_credit,
        "pnl_points": pnl_points,
        "pnl_rupees": pnl_rupees,
    }, None


def weekly_mondays(start, end):
    d = start
    while d.weekday() != 0:
        d += datetime.timedelta(days=1)
    while d <= end:
        yield d
        d += datetime.timedelta(days=7)


def main():
    start = datetime.date(2024, 1, 1)
    end = datetime.date(2026, 8, 31)

    print(f"Fetching yfinance ^NSEI daily data {start} to {end}...")
    nifty = yf.Ticker("^NSEI").history(start=start - datetime.timedelta(days=10), end=end + datetime.timedelta(days=10))
    if nifty.empty:
        print("FATAL: could not fetch ^NSEI data")
        sys.exit(1)

    results = []
    errors = []
    for i, monday in enumerate(weekly_mondays(start, end)):
        entry_date = monday
        # skip if not a trading day-ish; try Monday, Tuesday, Wednesday
        result, err = None, None
        for offset in range(0, 4):
            d = monday + datetime.timedelta(days=offset)
            if d > end:
                break
            result, err = run_cycle(d, nifty)
            if result is not None:
                break
        if result is not None:
            results.append(result)
        else:
            errors.append((entry_date, err))
        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1} weeks processed, {len(results)} cycles OK, {len(errors)} errors")
        time.sleep(0.3)  # be polite to NSE

    print(f"\nTotal cycles: {len(results)} OK, {len(errors)} errors")
    if errors[:5]:
        print("Sample errors:", errors[:5])

    if not results:
        print("FATAL: no successful cycles, cannot backtest")
        sys.exit(1)

    total_pnl = sum(r["pnl_rupees"] for r in results)
    wins = sum(1 for r in results if r["pnl_rupees"] > 0)
    print(f"\nTotal P&L over {len(results)} cycles: Rs {total_pnl:,.0f}")
    print(f"Win rate: {wins}/{len(results)} ({100 * wins / len(results):.1f}%)")

    n = len(results)
    half = n // 2
    first_half_pnl = sum(r["pnl_rupees"] for r in results[:half])
    second_half_pnl = sum(r["pnl_rupees"] for r in results[half:])
    print(f"\nWalk-forward: first half Rs {first_half_pnl:,.0f} ({half} cycles), second half Rs {second_half_pnl:,.0f} ({n - half} cycles)")

    quarters = {}
    for r in results:
        q = (r["entry_date"].year, (r["entry_date"].month - 1) // 3 + 1)
        quarters.setdefault(q, []).append(r["pnl_rupees"])
    print("\nQuarter-split:")
    for q in sorted(quarters):
        pnls = quarters[q]
        print(f"  {q[0]} Q{q[1]}: Rs {sum(pnls):,.0f} ({len(pnls)} cycles)")

    print("\nSample cycles:")
    for r in results[:5] + results[-5:]:
        print(
            f"  {r['entry_date']} -> expiry {r['expiry']}, spot {r['spot']:.0f}, "
            f"strikes {r['strikes']}, credit {r['net_credit_points']:.1f} pts, "
            f"settle {r['settle_spot']:.0f}, pnl Rs {r['pnl_rupees']:,.0f}"
        )


if __name__ == "__main__":
    main()
