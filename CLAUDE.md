# CLAUDE.md

Guidance for Claude Code sessions working in this repo.

## What this is

An intraday reversal strategy ("Highest Open / Lowest Open", source: a
ForexFactory thread — full rule text is in `strategy.py`'s module docstring)
wired to Zerodha's Kite Connect API. No framework, no build step — plain
Python, `requirements.txt` only.

## Setup / run

```
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # fill in KITE_API_KEY / KITE_API_SECRET, see README.md for the daily access-token dance
pytest -v               # network-free, safe to run anytime
python backtest.py --token <instrument_token> --from YYYY-MM-DD --to YYYY-MM-DD
python run_live.py --token <instrument_token> --symbol <symbol> --exchange <NSE|NFO>   # paper mode by default
```

## Architecture: strict separation of pure logic from I/O

- `strategy.py` and `risk.py` are **pure** — no imports of `kiteconnect`,
  no datetime.now(), no file/network I/O. This is deliberate and mirrors
  the pattern already established in `~/repos/scraping-practice`: keep
  "decide what to do given this data" separable from "go get the data" so
  it's testable without mocking a broker. `tests/` runs against plain
  floats/dataclasses, no fixtures needed, no network ever.
- `kite_client.py` / `data.py` are the only files that import `kiteconnect`.
- `paper_broker.py` has zero dependency on Kite — it's a pure simulator and
  is the **default** broker in both `backtest.py` and `run_live.py`. Real
  orders only ever come from `kite_client.place_order()`, called from
  exactly one place in `run_live.py`, gated behind `--live` AND
  `MONEYMAKING_LIVE=true`.

## Interpretation decisions baked into strategy.py (verify against intent)

The source is a forum post in prose, not a spec, so some choices were made
turning it into code — check these against what's actually wanted before
trusting backtest results:

- **One-shot-per-level** (`one_shot_per_level=True` default): a given
  highest/lowest-open value only triggers one entry. A later H1 bar that
  pushes the line further resets it (new level, can fire again). The
  source doesn't explicitly address repeat entries at an unchanged level;
  this reads the thread's emphasis on "wait" as implying no re-churn on
  the same line.
- **Units generalised, not forex pips**: the source's "+5 -> breakeven+1,
  +10 -> trail" is in forex pips. `TrailingStopManager`'s thresholds are
  plain price-unit floats — pick numbers that make sense for whatever
  instrument this points at (an index trading in hundreds of points needs
  very different thresholds than a stock trading in tens of rupees).
- **M15 filter is opt-in** (`m15_filter=False` default) — it was a later
  addition to the source thread aimed at traders entering too early, not
  part of the original rule.
- **Stop-loss = day's high/low *at the moment of entry***, not a
  continuously-updating "current daily high/low" that could move against
  an open position before the trailing-stop logic takes over. Re-read the
  source's "stop loss is the current daily high or current daily low" if
  this diverges from intent — it's arguably ambiguous which moment is meant.

## Known gaps (see README's "Known limitations" for the user-facing version)

- `backtest.py` feeds each minute candle's OHLC in open→high→low→close
  order as an approximation of the true tick path — Kite's historical API
  doesn't expose ticks. Don't treat backtest P&L as more precise than that
  approximation allows.
- `run_live.py` polls every 15s rather than streaming via `KiteTicker`.
  Fine for a first cut on a liquid, not-hyper-fast-moving instrument; an
  upgrade path if entries need to be tighter to the true first touch.
- No automated daily Kite login — deliberately not built (Zerodha's 2FA
  login isn't meant to be scripted around by a third-party tool). The
  manual `login_url()` → `generate_access_token()` dance in README.md is
  the intended daily routine.

## Safety rails already in place — don't loosen without being asked

- `run_live.py` requires **both** `--live` and `MONEYMAKING_LIVE=true` to
  place a real order — two independent opt-ins on purpose.
- `RiskManager.trading_halted()` is a hard daily-loss circuit breaker,
  checked before every new entry in both `backtest.py` and `run_live.py`.
- `PaperBroker` is the default broker everywhere; nothing routes to
  `kite_client.place_order()` except the one gated call site in
  `run_live.py`.
