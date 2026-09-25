"""
Probe: Mercury retrograde — the single most-cited concept in popular
("ancient") astrology, going back to Ptolemy's Tetrabiblos (2nd century
CE), which treats a planet's apparent backward motion through the zodiac
as a period of confusion, delay and bad decisions. Folk financial
astrology (still circulated today, e.g. retail-trading forums warning
"don't start new positions during Mercury retrograde") predicts WORSE
outcomes during retrograde windows. Different in kind from the
Ninety-fourth entry's lunar-phase test (a phase of the Moon relative to
the Sun, computed from a fixed synodic period) — this is real apparent
planetary motion, which requires actual orbital mechanics, not a fixed
cycle length: Mercury's retrograde windows are irregular (roughly 3-4
times a year, ~3 weeks each) because they depend on the relative
geometry of two elliptical orbits, not a simple period.

EPHEMERIS: no astronomy library added (checked first: none installed;
ladder rung 5 doesn't apply) — geocentric ecliptic longitude computed
from the standard, public-domain, low-precision Keplerian orbital
elements for Mercury and Earth (Standish/JPL, "Keplerian Elements for
Approximate Positions of the Major Planets", valid 1800-2050 AD;
~1-arcminute-level accuracy — plenty for locating week-scale retrograde
windows, not for precision astrometry). Kepler's equation solved by
Newton's method; heliocentric orbital-plane coordinates rotated into the
J2000 ecliptic frame; Mercury's GEOCENTRIC longitude = its heliocentric
position minus Earth's. A day is "retrograde" if that longitude
DECREASED (mod 360, shortest-path unwrapped) from the previous day.

SELF-CHECK (ladder: non-trivial logic gets one runnable check): retrograde
episode count/year and mean episode length are printed and compared
against the well-documented real-world figures (~3-4 episodes/year,
~3-week average) before any market-return claim is trusted — see
`if __name__ == "__main__"` with `--selfcheck`.

Pre-registered before any return was computed, same convention as the
Ninety-fourth entry: is the mean daily return on Mercury-retrograde days
different from non-retrograde days, on NIFTY and the S&P 500, against
the same random-same-size-subset null used throughout this project's
calendar-effect entries? Folklore's predicted direction (retrograde =
worse) is named but not assumed; reported regardless of sign.
"""
import argparse
import math
from datetime import date, datetime, timedelta

import numpy as np

from data_yfinance import fetch_candles

# Standish/JPL low-precision orbital elements, J2000 epoch, rate is per
# Julian century. angles in degrees, a in AU.
ELEMENTS = {
    "mercury": dict(
        a0=0.38709927, adot=0.00000037,
        e0=0.20563593, edot=0.00001906,
        i0=7.00497902, idot=-0.00594749,
        L0=252.25032350, Ldot=149472.67411175,
        peri0=77.45779628, peridot=0.16047689,
        node0=48.33076593, nodedot=-0.12534081,
    ),
    "earth": dict(
        a0=1.00000261, adot=0.00000562,
        e0=0.01671123, edot=-0.00004392,
        i0=-0.00001531, idot=-0.01294668,
        L0=100.46457166, Ldot=35999.37244981,
        peri0=102.93768193, peridot=0.32327364,
        node0=0.0, nodedot=0.0,
    ),
}


def julian_date(d) -> float:
    d = d.date() if isinstance(d, datetime) else d
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    jdn = d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jdn - 0.5


def heliocentric_position(planet: str, jd: float):
    """Heliocentric ecliptic (x, y, z) in AU, J2000 mean ecliptic/equinox."""
    el = ELEMENTS[planet]
    T = (jd - 2451545.0) / 36525.0
    a = el["a0"] + el["adot"] * T
    e = el["e0"] + el["edot"] * T
    i = math.radians(el["i0"] + el["idot"] * T)
    L = el["L0"] + el["Ldot"] * T
    peri = el["peri0"] + el["peridot"] * T
    node = el["node0"] + el["nodedot"] * T
    omega = math.radians(peri - node)  # argument of perihelion
    node_r = math.radians(node)

    M = (L - peri) % 360.0
    if M > 180.0:
        M -= 360.0
    M_r = math.radians(M)

    E = M_r
    for _ in range(50):
        dE = (E - e * math.sin(E) - M_r) / (1 - e * math.cos(E))
        E -= dE
        if abs(dE) < 1e-10:
            break

    xp = a * (math.cos(E) - e)
    yp = a * math.sqrt(1 - e * e) * math.sin(E)

    cos_w, sin_w = math.cos(omega), math.sin(omega)
    cos_o, sin_o = math.cos(node_r), math.sin(node_r)
    cos_i, sin_i = math.cos(i), math.sin(i)

    x = (cos_w * cos_o - sin_w * sin_o * cos_i) * xp + (-sin_w * cos_o - cos_w * sin_o * cos_i) * yp
    y = (cos_w * sin_o + sin_w * cos_o * cos_i) * xp + (-sin_w * sin_o + cos_w * cos_o * cos_i) * yp
    return x, y


def mercury_geocentric_longitude(d) -> float:
    jd = julian_date(d)
    mx, my = heliocentric_position("mercury", jd)
    ex, ey = heliocentric_position("earth", jd)
    gx, gy = mx - ex, my - ey
    return math.degrees(math.atan2(gy, gx)) % 360.0


def retrograde_flags(dates) -> np.ndarray:
    """True for each date (from the 2nd on) where longitude moved backward
    since the prior CALENDAR day (not prior trading day — real planetary
    motion doesn't skip weekends)."""
    lam = np.array([mercury_geocentric_longitude(d) for d in dates])
    delta = np.diff(lam)
    delta = (delta + 180) % 360 - 180  # shortest signed angular change
    return delta < 0


def selfcheck(start_year=2020, end_year=2026):
    days = [date(start_year, 1, 1) + timedelta(n)
            for n in range((date(end_year, 12, 31) - date(start_year, 1, 1)).days + 1)]
    retro = retrograde_flags(days)
    episodes, lengths, cur = [], [], 0
    for r in retro:
        if r:
            cur += 1
        elif cur:
            lengths.append(cur)
            episodes.append(cur)
            cur = 0
    if cur:
        lengths.append(cur)
    years = end_year - start_year + 1
    print(f"selfcheck {start_year}-{end_year}: {len(lengths)} episodes "
          f"({len(lengths)/years:.1f}/yr), mean length {np.mean(lengths):.1f} calendar days "
          f"(real-world reference: ~3-4 episodes/yr, ~21 days each)")


def returns_by_retrograde(symbol: str, period: str):
    candles = fetch_candles(symbol, "1d", period)
    closes = np.array([c["close"] for c in candles])
    dates = [d.date() if isinstance(d, datetime) else d for d in (c["date"] for c in candles)]
    rets = closes[1:] / closes[:-1] - 1
    # retrograde_flags(dates) has len(dates)-1 entries, aligned to dates[1:],
    # the same alignment as rets (the return ENDING on dates[1:])
    retro = retrograde_flags(dates)
    return {"retrograde": rets[retro], "direct": rets[~retro]}, rets


def subset_control(rets: np.ndarray, n: int, draws: int, rng: np.random.Generator):
    out = np.empty(draws)
    idx = np.arange(len(rets))
    for i in range(draws):
        sample = rng.choice(idx, size=n, replace=False)
        out[i] = rets[sample].mean()
    return out


def report(symbol: str, period: str, draws: int, seed: int = 0):
    by_label, rets = returns_by_retrograde(symbol, period)
    rng = np.random.default_rng(seed)
    print(f"\n{symbol}: {len(rets)} trading days, {period}; "
          f"overall mean daily return {rets.mean():.4%}")
    rows = []
    for label in ("retrograde", "direct"):
        r = by_label[label]
        n = len(r)
        actual = r.mean()
        null = subset_control(rets, n, draws, rng)
        p = ((np.abs(null) >= abs(actual)).sum() + 1) / (draws + 1)
        print(f"  {label:10s}: n={n} mean={actual:.4%} null_mean={null.mean():.4%} p={p:.4f}")
        rows.append((symbol, label, actual, n, p))
    print(f"  retrograde - direct = {by_label['retrograde'].mean() - by_label['direct'].mean():.4%} "
          f"(folklore predicts retrograde < direct)")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--period-nifty", default="20y")
    parser.add_argument("--period-spx", default="max")
    parser.add_argument("--draws", type=int, default=5000)
    parser.add_argument("--selfcheck-only", action="store_true")
    args = parser.parse_args()

    selfcheck()
    if args.selfcheck_only:
        raise SystemExit(0)

    all_rows = []
    all_rows += report("^NSEI", args.period_nifty, args.draws)
    all_rows += report("^GSPC", args.period_spx, args.draws)

    print("\n=== summary (uncorrected p < 0.05) ===")
    for sym, label, mean, n, p in all_rows:
        if p < 0.05:
            print(f"  {sym} {label}: n={n} mean={mean:.4%} p={p:.4f}")
