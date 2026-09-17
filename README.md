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

## Daily-bar strategies (a second family, eight mechanisms so far)

`backtest_daily.py` backtests daily-bar strategies against years of data
instead of intraday's 60-day cap, via
`--strategy {donchian,rsi2,threebar,squeeze,volume,turtlesoup,macd,bollinger}`:

```
python backtest_daily.py --strategy donchian --symbol '^NSEI' --period 10y --walk-forward
python backtest_daily.py --strategy rsi2 --symbol RELIANCE.NS --period 10y --walk-forward
python backtest_daily.py --strategy threebar --symbol SBIN.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy squeeze --symbol INFY.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy volume --symbol GC=F --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy turtlesoup --symbol AXISBANK.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy macd --symbol TCS.NS --period 10y --walk-forward --commission-per-trade 20
python backtest_daily.py --strategy bollinger --symbol RELIANCE.NS --period 10y --walk-forward --commission-per-trade 20
```

**`donchian`** — the "Turtle Trading" entry rule (N-day high/low channel
breakout). Systematic testing (18 instruments, parameter perturbation,
quarter-splits) found smooth, all-positive performance across every tested
parameter on BTC-USD/ETH-USD — but crypto isn't tradable via Kite at all,
and quarter-splitting shows that result is concentrated in crypto's
2016-2021 bull run, decayed to roughly flat in the most recent 2.5 years.
Kite-tradable candidates (gold, oil) are considerably weaker once
regime-split.

**`rsi2`** — Larry Connors' 2-period RSI mean-reversion strategy (a
published, widely-cited system: 200-day trend filter, RSI(2) extremes for
entry, 5-day SMA for exit). The strongest, most theoretically coherent
result of the whole project — an 8/14 hit rate on REAL Kite-tradable
instruments (Nifty, Bank Nifty, 5 large-cap stocks, oil), smooth parameter
sensitivity with a sensible causal story (only genuinely extreme RSI
readings work, matching Connors' own thesis). **But the magnitude is too
small to be worth trading at any risk setting**: 0.04-0.25%/year at
standard sizing, and even at 10% risk per trade (reckless), `^NSEI` only
reaches 0.83%/year with 19% drawdown — return and drawdown scale together,
so no amount of leverage fixes a genuinely thin per-trade edge. See
`CLAUDE.md` for the full breakdown, including two real bugs found and
fixed along the way (a fake 100% win rate from a units mismatch, and a
never-reset daily-loss breaker that silently truncated trading after ~4
losses in any multi-year run).

`rsi2`'s exits can optionally route through an ATR-scaled profit-booking
overlay (`--breakeven-atr-mult` / `--trail-atr-mult`, reusing
`strategy.py`'s `TrailingStopManager`) — tested specifically to see if
locking in profit earlier fixes RSI-2's thin-magnitude problem. **It
doesn't**: it made every tested instrument worse (see CLAUDE.md), because
RSI-2's own exit rule already functions as a profit target for its
mean-reversion mechanism. Off by default (`None`/`None`); left in the code
as a generic tool for a future strategy whose entry has no natural
profit-taking exit of its own.

**`threebar`** — a 3-bar range-compression breakout/continuation pattern
sourced from a ForexFactory thread (see CLAUDE.md for the exact thread
citations): two prior bars closing near the same level, then a third
closing decisively beyond both, with a fixed R-multiple take-profit. The
best instrument hit rate in the project after RSI-2 (6/12 Kite-tradable
instruments passed walk-forward screening) with genuinely stable
quarter-split results on 5 of those 6. At standard 0.5% risk-per-trade the
edge is thin (0.4-2.2%/year) like RSI-2 — but unlike RSI-2, a modest bump
to 1% risk-per-trade (still conservative) turns this into 1.9-4.6%/year
across five independent instruments at 1-4.4% max drawdown, without the
"return and drawdown just scale together" problem that made leverage
useless for RSI-2. Still requires per-instrument selection (half the
instruments tested show no edge) and one passer (`INFY.NS`) leans heavily
on a single COVID-era quarter. See CLAUDE.md's "Twelfth" section for the
full breakdown.

**`squeeze`** — John Carter's TTM Squeeze, reproducing LazyBear's widely-
forked open-source "Squeeze Momentum Indicator" exactly (Bollinger Bands
contracting inside Keltner Channels = "squeeze on"; BB expanding back out =
"squeeze fires", trade the linear-regression momentum's sign). A genuinely
different, volatility-state-timing mechanism — but only 3/12 Kite-tradable
instruments passed walk-forward screening, a 25% hit rate right at the
"pure chance" level already established as disqualifying in this project
(see the sector sweep's 18%). One passer (`INFY.NS`) looks clean on
quarter-split and reasonable on perturbation, and shares the "1% sizing
roughly doubles the return" property the 3-bar breakout has — but surfacing
from a chance-level sweep means it isn't distinguishable from a lucky draw.
See CLAUDE.md's "Fifteenth" section for the full breakdown.

**`volume`** — Chaikin Money Flow + On-Balance Volume confirmation,
reproducing `XBT3K/VOLUME-ALGO-EURUSD`'s open-source `VolumeOBVCMF`
backtrader strategy: long when both CMF and a (windowed) OBV are positive,
exit when both turn negative. The first strategy in this project to use
volume at all rather than price alone — and correctly never trades index
symbols (`^NSEI`, `^NSEBANK`), since yfinance reports no real volume for an
index. Screened on 10 stocks/futures: 3/10 passed walk-forward (`INFY.NS`,
`WIPRO.NS`, `GC=F`), but quarter-split shows `INFY.NS`/`WIPRO.NS` both
decaying in the most recent quarter — only `GC=F` (gold) is clean, with a
smooth, all-positive perturbation sweep on par with RSI-2's. **Sizing
doesn't rescue it**, though: unlike the 3-bar breakout or Squeeze, raising
risk-per-trade just drawdown-halts the run instead of scaling the return,
leaving `GC=F` stuck at ~1.30%/year — real but too thin, the same verdict
as RSI-2. See CLAUDE.md's "Sixteenth" section for the full breakdown.

**`turtlesoup`** — Linda Raschke's "Turtle Soup" failed-breakout fade: the
exact counter-trend inverse of `donchian`'s own entry trigger (buy a failed
new N-day low, sell a failed new N-day high, once price closes back inside
the prior range). Screened on the same 12-instrument set: 3/12 passed
walk-forward (`^NSEI`, `ITC.NS`, `AXISBANK.NS`) — 25%, chance-level again.
`AXISBANK.NS` is clean on quarter-split (all 4 quarters positive) and has
the smoothest single-survivor perturbation sweep in the whole project (10
tested parameter values, all positive on both halves, no cliffs) —
interesting since the same stock failed SuperTrend's own perturbation
sweep on a single-point-fit cliff. Sizing helps up to 1% risk (1.71%/year,
1.1% drawdown) then drawdown-halts at 2%+ — thin, same bucket as
SuperTrend's `CL=F` and volume's `GC=F`, not confirmed beyond one
instrument. See CLAUDE.md's "Twentieth" section for the full breakdown.

**`macd`** — Gerald Appel's MACD crossover, one of the most widely used
technical indicators anywhere: buy when the MACD line (fast EMA - slow EMA
of closes) crosses above its own signal line (an EMA of the MACD line),
short on the reverse. Screened on the same 12-instrument set: only **2/12
passed** (`TCS.NS`, `WIPRO.NS`) — 16.7%, the lowest hit rate of any
indicator-based strategy in this project. `WIPRO.NS` decays negative in
the most recent quarter; `TCS.NS` is clean (all 4 quarters positive) with
reasonable (not perfect) perturbation robustness. Sizing helps up to 1%
risk (1.73%/year, 2.6% drawdown) then drawdown-halts at 2%+ — thin, same
"real but too small" bucket as most of this project's survivors. See
CLAUDE.md's "Twenty-first" section for the full breakdown.

**`bollinger`** — John Bollinger's own classic bands: buy a close below a
rolling SMA-±-stdev lower band, short above the upper band, exit at the
middle band. Screened on the same 12-instrument set: **0/12 passed** — the
worst hit rate of any strategy in this project (below MACD's 16.7% and
the sector sweep's 18% floor), with two instruments (`RELIANCE.NS`,
`TCS.NS`) drawdown-halting outright. No perturbation needed given how
uniform the failure is. **A follow-up regime filter** (`--bb-trend-
filter-lookback`/`--bb-trend-filter-atr-mult`, skip entries while the
market is trending rather than range-bound — the same shape as RSI-2's
own trend filter, which made RSI-2 this project's most robust result)
didn't rescue it either — still 0/12 at the default setting, and 0/5 on a
subset across 4 tightness values. RSI-2's filter works because its
underlying signal is real; Bollinger's band-touch entry doesn't appear to
be, filtered or not. See CLAUDE.md's "Twenty-seventh"/"Twenty-eighth"
sections for the full breakdown.

## Other mechanisms explored as standalone probe scripts (not ported into the architecture)

`probe_gap_fill.py` (intraday gap-fill mean reversion — bet that an
opening gap reverts toward yesterday's close within the session) failed
the same way the sector sweep and calendar-effects mechanisms did: a
narrow band of the parameter space looks marginally positive on `^NSEI`,
but it sign-flips on walk-forward, is mixed across quarters, and doesn't
reproduce on `^NSEBANK` or `RELIANCE.NS` — see CLAUDE.md's "Tenth" entry
for the full numbers.

`probe_ibs.py` (Internal Bar Strength mean reversion — long when today's
close fell near today's own low, `IBS = (close-low)/(high-low) < 0.2`) found
exactly one passing instrument (`GC=F`, gold) out of 8 tested — a 12.5% hit
rate at or below the sector sweep's already-distrusted chance-level 18%. The
one survivor's internals look real (smooth perturbation, no quarter decay,
and sizing up to 1% risk more than triples the return) but one instrument
out of eight is thin ground — see CLAUDE.md's "Thirteenth" entry.

`probe_iron_condor_real_data.py` (weekly Nifty iron condor, real NSE
option-chain prices instead of Black-Scholes synthesis — see below and
CLAUDE.md's "Fourteenth" entry) is the strongest mechanism found in this
project on raw robustness, but real market pricing shows a much thinner
edge than the earlier synthetic backtest implied, and it's separately
blocked by a capital-tier problem regardless of pricing method.

`probe_supertrend.py` (SuperTrend — a sticky ATR ratchet-band trend
follower, reproducing a widely-cited open-source implementation) found
4/12 instruments passing walk-forward, a chance-level 33% hit rate; three
of the four fail quarter-split or perturbation (one, `AXISBANK.NS`, only
works at the exact default parameter — a single-point-fit red flag). The
one clean survivor, `CL=F` (oil), clears quarter-split, perturbation, AND
sizing (2% risk reaches 3.78%/year at 7.4% drawdown, genuine scaling, not
dilution) — comparable in quality to this project's best results, but
still just one instrument out of a chance-level sweep. **Retested on 8 more
commodities/FX pairs (the instrument class `CL=F` belongs to) — 0/8
passed**, confirming `CL=F` doesn't generalize within its own instrument
class and was the lucky draw its chance-level screen already implied might
exist. Closed; see CLAUDE.md's "Seventeenth"/"Eighteenth" entries.

`probe_gold_silver_ratio.py` (gold/silver ratio mean reversion — a real,
widely-followed commodity pairs trade, distinct from the equity pairs
trading already tried) found **0/18 parameter configs** passing
walk-forward, and unlike every prior cost-driven failure in this project
(silver scalping, overnight-drift), the gross P&L itself is near zero
across the whole grid — no real edge being masked by costs, just a flat
result. See CLAUDE.md's "Nineteenth" entry.

`probe_trend_volume_ibs.py` — this project's first ORIGINAL strategy, not
sourced from a forum thread or open-source repo: a hypothesis built from
three prior findings (IBS's lone `GC=F` survivor, RSI-2's real trend
filter, CMF-volume's own `GC=F` survivor), testing whether requiring all
three to agree on entry extends IBS's narrow edge. **0/10 instruments
passed** — and strikingly, `GC=F` itself (the one instrument where both
ingredient strategies individually worked) fails when combined, at every
one of 10 perturbed configs. Stacking independently-real signals didn't
compound their edges; it destroyed them — the same direction (if a
different mechanism) as the Eleventh entry's exit-side stacking finding.
See CLAUDE.md's "Twenty-second" entry.

`probe_low_volatility.py` (Low-Volatility Anomaly / "Betting Against
Beta" — a real academic factor: rank a 20-stock NSE universe by trailing
volatility, hold the least-volatile basket, rebalance monthly) passes
walk-forward cleanly (both halves positive, 8/8 perturbation configs
smooth, no cliffs) — but quarter-split shows the **exact same recent-
quarter decay** already seen with Donchian/BTC-USD and momentum rotation:
Q1-Q3 strongly positive, **Q4 (2024-2026) negative in every tested
config**, while the broad universe benchmark was actually +6.3% over the
same window — genuine underperformance, not just a falling market. Third
independent mechanism to hit this exact pattern in this exact recent
window, which is itself the more interesting finding than any one
instance. See CLAUDE.md's "Twenty-fourth" entry.

`probe_pead.py` (Post-Earnings Announcement Drift — a real academic
anomaly, event-driven rather than a continuous technical signal: buy a
stock that beat earnings estimates and hold for weeks, short a miss)
initially passed a 12-config walk-forward grid at every tested value on a
20-stock universe — the strongest aggregate screen since options-selling
— but per-symbol breakdown showed it was concentrated in 3 stocks
(`TATASTEEL.NS`/`SUNPHARMA.NS`/`ONGC.NS`, 15% hit rate). **Retested on a
doubled 40-stock universe (the standard next step for any promising-
looking result here) and it didn't replicate**: the same config flips
INCONSISTENT across walk-forward halves, and the full grid drops from
12/12 passing to 4/12. The "winning" stocks reshuffle too — new names
become the top contributors while the original top contributor
(`TATASTEEL.NS`) shrinks to near breakeven. Closed, the same way
SuperTrend's oil survivor was closed by its own 8-instrument retest. A
real capital-deployment bug was also caught and fixed along the way (the
first version allowed 130% of capital "deployed" simultaneously during
earnings-season clustering; rewritten to properly skip over-allocating
signals) — a permanent improvement kept regardless of PEAD's own fate.
See CLAUDE.md's "Thirtieth"/"Thirty-first" entries.

`probe_portfolio_combo.py` — a genuinely different test: not a new
signal, and not stacking signals into one trade (already found to hurt
twice), but the standard portfolio-construction question this project had
never asked — does combining several of its own already-validated
real-but-thin survivors (RSI-2, both `threebar` survivors, MACD, Turtle
Soup, each on its own instrument) produce a better combined return than
any one alone? **No** — the 5-way combination returns 0.30%/year at 4.1%
drawdown, worse on both dimensions than just running `turtlesoup`/
`AXISBANK.NS` alone (1.71%/year at 1.1% drawdown). Re-tested with only
the 3 strongest components (dropping RSI-2/MACD) — still worse than the
single best one alone. These edges are correlated (all long-biased NSE
equity strategies) and individually too thin for diversification to
produce a free lunch. See CLAUDE.md's "Thirty-second" entry.

Tested whether MARKET STRUCTURE, not mechanism, explains this project's
ceiling — reused `turtlesoup` and `threebar` unmodified against a genuine
small/mid-cap universe (12 stocks never used elsewhere here:
`DEEPAKNTR.NS`, `CROMPTON.NS`, `RADICO.NS`, `APLAPOLLO.NS`,
`JUBLPHARMA.NS`, `SYMPHONY.NS`, `VGUARD.NS`, `RATNAMANI.NS`,
`PERSISTENT.NS`, `GRAPHITE.NS`, `ELGIEQUIP.NS`, `KAJARIACER.NS`), on the
hypothesis that less-arbitraged stocks might leave more room for a
technical edge. **No improvement** — Turtle Soup reproduces its exact
25% hit rate unchanged; 3-bar breakout gets WORSE (16.7% vs its original
50%, and 5/12 instruments hit the drawdown breaker outright — higher
small-cap volatility hurt this strategy's risk control more than it
helped). One clean individual survivor (`SYMPHONY.NS`, remarkably stable
quarter-split) emerged but without the sizing response this project's
best large-cap results have — thin, ~1.18%/year at the only safe
setting. See CLAUDE.md's "Thirty-third" entry.

`regime.py` (market-regime classifier: trend-vs-200-day-SMA crossed with a
realized-volatility percentile rank, no lookahead) — the first entry to
condition on market regime rather than test a new signal or instrument.
Current `^NSEI` regime as of 2026-09-17: `down_low_vol`. Tested two
hypotheses against this project's own 5 best-known survivors: (a) gating
mean-reversion strategies to low-vol regimes and continuation strategies to
high-vol regimes — no consistent improvement, and the one case that looked
like a win (`threebar`/`SBIN.NS`'s drawdown dropping from 10.5% to 0.2%)
turned out to only survive at the exact default regime threshold, the same
single-point-fit red flag already seen with Squeeze/SuperTrend; (b)
regime-based strategy SWITCHING (Turtle Soup gated to choppy regimes,
Donchian to trending, on `AXISBANK.NS`) — nets ~flat, clearly worse than
running Turtle Soup alone. See CLAUDE.md's "Thirty-fourth" entry.

### Options premium selling (weekly Nifty iron condor) — real edge, wrong capital tier, and now real-data validated

Not a daily-bar or intraday-candle strategy, so it doesn't fit either
family above — a defined-risk short strangle (iron condor) sold weekly on
Nifty, harvesting the volatility risk premium. First tested with
Black-Scholes-synthesized premiums (CLAUDE.md's "Seventh" entry): +20.5%
CAGR, no quarter-decay, the most robust-looking result in the whole
project — but flagged immediately as unvalidated against real prices, and
separately shown to need ~₹350,000-475,000 of capital to size one lot
safely (Nifty's SEBI-mandated lot size makes one spread's max loss 20-30%
of a ₹30,000 account — see "Ninth").

Later validated against **real NSE F&O bhavcopy data** (free — `jugaad-data`
for older dates, NSE's own new-format UDIFF bhavcopy fetched directly for
newer dates, both covering 2015-2026, no paid Kite "Historical" subscription
needed). 140 real weekly cycles (2024-2026): the edge's *direction* holds up
(77% win rate, both walk-forward halves positive) but the *magnitude* was
drastically overstated by the synthetic pricing — real annualized return
at the capital this strategy needs is only ~2-3%/year, not 20%+, likely
below a risk-free rate. See CLAUDE.md's "Fourteenth" entry for the full
numbers. Net effect: this mechanism is now closed out on two independent
grounds (capital access AND thin real magnitude), not just one.

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
python run_live.py --token 256265 --symbol NIFTY --exchange NSE              # paper mode, 15s polling (default)
python run_live.py --token 256265 --symbol NIFTY --exchange NSE --stream     # paper mode, real-time WebSocket price
```

`--stream` uses Kite's own WebSocket (`KiteTicker`, in `kite_ticker.py`)
instead of polling for price — matters because entries fire on every price
update, not bar close, so streaming catches the exact first touch of a line
instead of lagging by up to one poll interval. H1/M15 candles still refresh
periodically either way (they don't change every second). Written against
KiteTicker's documented API but, like the rest of this repo's Kite
integration, untested against a live connection — the polling path is the
better-exercised default.

## Going live

Real orders require **both**:

```
export MONEYMAKING_LIVE=true
python run_live.py --token <token> --symbol <symbol> --exchange <NFO|NSE> --live
```

Don't flip this on until you've backtested and paper-traded to your own
satisfaction. `risk.py`'s `max_daily_loss_pct` circuit breaker is there as a
backstop, not a substitute for validating the strategy first.

## Transaction costs

`PaperBroker(commission_per_trade=...)` / `backtest.py --commission-per-trade`
deducts a flat cost per round-trip trade (default 0). **Always set this to a
realistic estimate before trusting a backtest result** — a systematic search
found a config that looked like a genuine edge (silver, 60d, +22,310 gross
across 278 trades) that flipped net-negative once a conservative $4/trade
cost was applied. A zero-cost backtest overstates high-trade-count results
the most.

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
- `run_live.py --stream` (see above) addresses the polling lag using
  KiteTicker, but is itself untested against a live connection.
- Daily access-token regeneration is manual (see Setup) — Zerodha's login
  is deliberately behind 2FA, and this repo doesn't try to script around it.
- **Every commodity/FX backtest in this project (all `probe_*.py` scripts
  and `backtest_daily.py`) sizes positions as `risk_amount / stop_distance`
  with no connection to the instrument's real exchange lot size** —
  correct for equity shares (buyable one at a time), silently wrong for
  currency/commodity derivatives (fixed-lot contracts only). Found via a
  striking-looking IBS-on-FX result that fell apart once checked: one real
  USDINR lot needs ~₹299,000 of capital to size safely within a 0.5%
  risk-per-trade standard, the same capital-tier wall that closed out this
  project's options-selling line. **Follow-up check on the actual gold/oil
  survivors** (applying a real USD/INR conversion, not this project's
  existing $-as-₹ simplification for GC=F/CL=F): oil is blocked at every
  real MCX contract size (₹328k-1.3M needed even at the smallest Mini
  contract). Gold initially looked like it might have an escape hatch via
  a small "Gold Petal" (1g) contract (₹14,638-58,550 needed) — **checked
  directly against Kite's real, live, unauthenticated instrument master
  (`https://api.kite.trade/instruments`) and confirmed no such contract
  exists on MCX**: MCX genuinely offers exactly `GOLD`/`GOLDM`/`GOLDGUINEA`
  (standard/Mini/8g), nothing smaller. This closes the whole capital-tier
  investigation — every real "survivor" this project found (options
  selling, SuperTrend's oil, IBS's/volume's gold) is now confirmed blocked
  by a genuine capital wall at this project's target scale, with no
  remaining unverified escape hatch. See CLAUDE.md's "Twenty-fifth"
  through "Twenty-ninth" entries for the full arc.
