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
- `data_yfinance.py` is a free alternative data source (Yahoo Finance, no
  API key) — used by `backtest.py --source yfinance`, the default, since
  the free Kite "Personal" app tier has no historical-data access at all.
  It returns the exact same candle shape as `data.py`'s Kite wrapper
  (`{date, open, high, low, close, volume}` dicts), so `backtest.py`'s
  `simulate()` function is fully source-agnostic and doesn't know or care
  which one fed it. `run_backtest_yfinance()` / `run_backtest_kite()` are
  the only two places that import a specific source.
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

## Verified against real data (not just unit tests)

`python backtest.py --source yfinance --symbol RELIANCE.NS --period 30d` and
`--symbol '^NSEI' --period 60d` were both actually run against live Yahoo
Finance data during development (not just imagined to work) — confirmed the
full pipeline executes end-to-end and produces plausible trade logs. This
surfaced a real finding, not a hypothetical one: the source thread's +5/+10
trailing-stop thresholds are forex pips and are essentially noise on the
Nifty index (24,000+ points) — 146 trades over 60 days with defaults, vs 77
with `--breakeven-trigger 30 --trail-trigger 60`. Don't assume the defaults
are reasonable for whatever instrument is used next; check trade frequency
in the output.

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
- `RiskManager.trading_halted()` combines TWO breakers: a same-day loss
  limit (`max_daily_loss_pct`, resets via `reset_day()`) AND a cumulative
  drawdown-from-peak limit (`max_drawdown_pct`, does **not** reset via
  `reset_day()` — only `reset_capital()`, which must be called
  deliberately, never automatically from a runner). Both are checked
  before every new entry in both `backtest.py` and `run_live.py`.
- `PaperBroker` is the default broker everywhere; nothing routes to
  `kite_client.place_order()` except the one gated call site in
  `run_live.py`.

## Backtest rigor tools (added after a systematic 9-instrument check)

Testing this strategy across 9 instruments x 60 days x {with, without}
M15-filter (18 backtests, ~970 trades) found **no consistent edge** —
aggregate P&L was negative both ways, and every single-period "win" flipped
sign under either perturbation. Two tools exist specifically to catch this
kind of false positive before trusting a result:

- **`backtest.py --walk-forward`**: splits the sample into two independent
  halves (own fresh capital/risk state each) and reports both. A real edge
  should look broadly similar on both. Every previously "profitable"
  single-period result checked this way (INFY.NS, HDFCBANK.NS, ITC.NS) came
  back **inconsistent** across halves — profitable on one, losing on the
  other. Treat any new single-period result the same way before trusting it.
- **`RiskManager.drawdown_from_peak_pct` / `.drawdown_halted`**: surfaced
  directly in `backtest.py`'s per-run report (`_report()`). A backtest can
  show misleadingly large P&L swings if drawdown isn't checked alongside
  the raw number — see HDFCBANK.NS's in-sample half: +36,657 P&L *and*
  drawdown-halted, because it gave back >10% of capital from a much higher
  peak before the run ended.

Do not report a bare P&L number as "the result" going forward without also
running `--walk-forward` and checking `drawdown_halted` — a single
whole-period P&L number has already been shown to be misleading on this
exact codebase.
