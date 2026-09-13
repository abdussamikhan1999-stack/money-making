# money-making — Highest Open / Lowest Open, on Kite

An intraday reversal strategy — "Highest Open / Lowest Open" — wired up to
Zerodha's Kite Connect API. Backtest it, paper-trade it, and only then, if
you choose to, let it place real orders.

**⚠️ This is not financial advice and there is no guarantee this makes
money.** The source idea (a ForexFactory thread) is presented there for
educational purposes only, with an explicit warning to check with an
accredited financial specialist before risking real capital. Backtest and
paper-trade thoroughly before ever running this live. You are solely
responsible for any trades it places.

## Two variants

`--variant reversal` (default) is the rule described below. `--variant
breakout` is its trend-following inverse (trade WITH a breakout instead of
fading it) — built to test whether a trending instrument suits it better.
Tested on oil: it didn't — 0% win rate over 21 trades, 3x worse than the
reversal variant. See `CLAUDE.md` for the detailed comparison.

## The rule

1. Track the highest and lowest H1 (hourly) bar **open** price seen so far
   today — two lines.
2. **Short**: once price trades *above* the highest-open line, arm a short.
   The first time price falls back *down* through that line, enter.
3. **Long**: once price trades *below* the lowest-open line, arm a long. The
   first time price rises back *up* through that line, enter.
4. Initial stop-loss = the day's high-so-far (short) / low-so-far (long).
5. Move to breakeven+1 at +5 units profit; trail to entry+5 at +10 units
   profit (tune these — the source's 5/10 are forex pips, meaningless for
   an index or a ₹500 stock).
6. Entries fire on every price update, not on bar close — the source is
   explicit that waiting for the bar to close defeats the method.
7. Optional filter: also require the current M15 bar's open to be beyond
   the line before entering (a later addition to the source thread, for
   traders who found themselves entering too early).

See the docstring at the top of `strategy.py` for the exact interpretation
decisions made turning that prose into code (line-fires-once-per-level,
generalised profit units, etc.) — check them against what you actually
intend.

## Layout

```
strategy.py       Pure state machine: HighLowOpenStrategy + TrailingStopManager.
                  No network code — this is what's unit tested.
risk.py           Position sizing (% risk per trade) + a daily-loss circuit breaker.
data.py           Thin Kite historical_data wrapper.
data_yfinance.py  Free historical data via Yahoo Finance — no Kite subscription needed.
kite_client.py    Auth (login_url / generate_access_token) + real order placement.
paper_broker.py   Simulated fills + P&L tracking. The default broker everywhere.
backtest.py       CLI: run the strategy over historical data (yfinance, free, or Kite).
run_live.py       CLI: poll Kite in real time, paper-trade by default.
tests/            pytest, network-free — runs against plain floats, no fixtures needed.
```

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**To backtest for free, no account needed:** you're done — skip straight to
"Backtest (free, no Kite subscription)" below.

**To go further (live quotes, real order placement)** you need a **Kite
Connect** developer app. Zerodha offers two tiers when you create one at
https://developers.kite.trade/apps:

- **Personal** (free) — order placement only. **No historical data, no live
  quotes/WebSockets** — `data.py`/`run_live.py` need the paid tier to work.
- **Connect** (~₹500/30 days, billed via your Zerodha account) — adds
  historical data + live quotes. Needed for `backtest.py --source kite` and
  for `run_live.py` at all.

Also note: as of April 1, 2026, SEBI requires **live orders to come from a
registered static IP** — register one on your Kite Connect profile page
before ever using `--live`.

Put the app's `api_key`/`api_secret` in `.env` (`cp .env.example .env`
first). Kite access tokens expire daily (~7:30am IST); each trading day:

```
python -c "from kite_client import login_url; print(login_url())"
# open the URL, log in, copy request_token from the redirect URL
python -c "from kite_client import generate_access_token; print(generate_access_token('PASTE_REQUEST_TOKEN'))"
# paste the printed access_token into .env as KITE_ACCESS_TOKEN
```

## Run

```
pytest -v   # strategy/risk/broker logic — no network, safe anytime
```

### Backtest (free, no Kite subscription)

```
python backtest.py --source yfinance --symbol RELIANCE.NS --period 30d
python backtest.py --source yfinance --symbol '^NSEI' --period 60d --m15-filter
```

`--symbol` is a Yahoo Finance ticker (`.NS` suffix for NSE stocks, `^NSEI`
for the Nifty 50 index). `--period` is how far back to pull (Yahoo caps
intraday history: up to ~60 days for the 15m/5m data this uses). Yahoo's
NSE data is best-effort, not exchange-of-record — fine for validating
strategy logic, not a substitute for real broker data before trading real
money.

**Tune the trailing-stop thresholds to your instrument** — the source
thread's +5/+10 defaults are forex pips and will misfire wildly on
something like the Nifty index (confirmed: 146 trades / -5,447 P&L over 60
days with the untuned defaults, dropping to 77 trades with wider
thresholds below):

```
python backtest.py --source yfinance --symbol '^NSEI' --period 60d \
  --breakeven-trigger 30 --breakeven-offset 5 --trail-trigger 60 --trail-offset 30
```

**Check for overfitting before trusting any single result** — add
`--walk-forward` to split the sample into two independent halves and report
both separately:

```
python backtest.py --source yfinance --symbol INFY.NS --period 60d --walk-forward
```

A real edge should look broadly similar on both halves. In practice, every
"profitable" single-period result found so far (INFY.NS, HDFCBANK.NS,
ITC.NS) turned out **inconsistent** across the two halves once checked this
way — profitable on one half, losing on the other — which is the standard
signature of noise, not a real edge. Don't trust a whole-period number that
hasn't been walk-forward checked.

### Backtest against real Kite data (needs the Connect tier)

```
python backtest.py --source kite --token 256265 --from 2026-08-01 --to 2026-09-01
```

`--token` is a Kite `instrument_token` — look one up via
`kite.instruments("NSE")` / `kite.instruments("NFO")`, or Kite's published
instrument dump.

### Live/paper runner (needs the Connect tier)

```
python run_live.py --token 256265 --symbol NIFTY --exchange NSE   # paper mode (default)
```

## Going live

Real orders require **both**:

```
export MONEYMAKING_LIVE=true
python run_live.py --token <token> --symbol <symbol> --exchange <NFO|NSE> --live
```

Don't flip this on until you've backtested and paper-traded to your own
satisfaction. `risk.py`'s `max_daily_loss_pct` circuit breaker is there as a
backstop, not a substitute for validating the strategy first.

## Risk management

Two independent, layered circuit breakers in `risk.py` (checked before
every new entry in both `backtest.py` and `run_live.py`):

- **Daily loss limit** (`max_daily_loss_pct`, default 2%) — resets every day.
- **Cumulative drawdown limit** (`max_drawdown_pct`, default 10%) —
  tracks equity from its all-time peak and **never resets automatically**.
  This one was added after a 60-day silver backtest lost 61% of starting
  capital: nothing was tracking cumulative equity, only same-day P&L, so
  the backtest kept "trading" long past the point a real account would
  have been wiped out. `reset_capital()` clears it, but only call that
  deliberately (a fresh backtest segment, or a real decision to
  re-fund/restart) — never automatically from a runner.

Also available: `RiskManager.volatility_position_size(atr)` sizes off an
instrument's own recent volatility (via `indicators.average_true_range`)
instead of the strategy's own stop distance — useful as a sanity cap
(`min()` of the two) when the strategy's stop is tighter than the
instrument actually moves, which is common on a fast-moving instrument.
Not wired into `backtest.py`/`run_live.py` by default — the strategy's own
day-high/low stop is still the primary sizing input; ATR sizing is there to
cross-check it.

## Known limitations

- `backtest.py` approximates each minute's intra-bar path as
  open → high → low → close, since Kite's historical API gives OHLC, not
  ticks. This can misorder which of a minute's high/low actually came
  first — a real limitation, not a bug to silently trust past.
- `run_live.py` polls (every 15s) rather than streaming ticks via
  `KiteTicker` — simpler and robust for v1, but means entries can lag the
  true first touch of a line by up to the poll interval. Worth upgrading to
  `KiteTicker` if this trades a fast-moving instrument.
- Daily access-token regeneration is manual (see Setup) — Zerodha's login
  is deliberately behind 2FA, and this repo doesn't try to script around it.
