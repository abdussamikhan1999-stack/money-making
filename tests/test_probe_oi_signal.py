import numpy as np
import pandas as pd

import probe_oi_signal as o

OLD = ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
       "FUTSTK,ACC,31-Oct-2019,0,XX,1476.45,1509.95,1461.65,1501.95,1501.95,8252,49093.25,2359600,110800,15-OCT-2019,\n"
       "FUTSTK,ACC,28-Nov-2019,0,XX,1480,1510,1462,1505.00,1505.00,100,600.00,400000,5000,15-OCT-2019,\n"
       "FUTSTK,ZZZ,31-Oct-2019,0,XX,1,1,1,1,1,1,1,999,1,15-OCT-2019,\n"
       "OPTSTK,ACC,31-Oct-2019,1500,CE,1,1,1,1,1,1,1,777,1,15-OCT-2019,\n"
       "FUTIDX,NIFTY,31-Oct-2019,0,XX,1,1,1,1,1,1,1,555,1,15-OCT-2019,\n"
       "OPTSTK,ACC,28-Nov-2019,1600,PE,1,1,1,1,1,40,1,300,1,15-OCT-2019,\n"
       "OPTSTK,ACC,28-Nov-2019,1400,PE,1,1,1,1,1,60,1,200,1,15-OCT-2019,\n"
       "OPTSTK,ACC,28-Nov-2019,1700,CE,1,1,1,1,1,5,1,1000,1,15-OCT-2019,\n")
NEW = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol\n"
       "2024-09-13,2024-09-13,FO,NSE,STF,40211,,NESTLEIND,,2024-10-31,2024-10-31,,,X,1,1,1,2550.40,1,1,2531.40,2550.40,422000,11800,567\n"
       "2024-09-13,2024-09-13,FO,NSE,STF,40212,,NESTLEIND,,2024-11-28,2024-11-28,,,X,1,1,1,2560.00,1,1,2531.40,2560.00,100000,0,10\n"
       "2024-09-13,2024-09-13,FO,NSE,STO,40213,,NESTLEIND,,2024-10-31,2024-10-31,2500,CE,X,1,1,1,9,1,1,2531.40,9,555555,0,1\n")


def test_parse_old_format_sums_expiries_keeps_only_wanted_stock_futures():
    df = o.parse_fo(OLD, "old", {"ACC"})
    assert list(df.symbol) == ["ACC"]                                           # ZZZ not wanted; OPTSTK/FUTIDX ignored
    r = df.iloc[0]
    assert r.oi_total == 2359600 + 400000 and r.oi_near == 2359600 and r.near_close == 1501.95
    assert r.contracts == 8252 + 100
    assert r.call_oi == 777 + 1000 and r.put_oi == 300 + 200                     # options summed over strikes and expiries
    assert r.call_vol == 1 + 5 and r.put_vol == 40 + 60


def test_parse_new_format_matches_the_same_contract():
    r = o.parse_fo(NEW, "new", {"NESTLEIND"}).iloc[0]
    assert r.oi_total == 522000 and r.oi_near == 422000 and r.near_close == 2550.40   # STO row not counted as a future
    assert r.call_oi == 555555 and np.isnan(r.put_oi)                                 # STO CE row is the call side


def panel(T=300, N=30, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=T)
    cols = [f"S{i}" for i in range(N)]
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (T, N)), axis=0), index=idx, columns=cols)
    oi = pd.DataFrame(rng.lognormal(12, 0.2, (T, N)), index=idx, columns=cols)
    return oi, close


def test_oi_signal_at_t_is_unaffected_by_later_data():
    oi, close = panel()
    a = o.signals(oi, close)
    oi2, close2 = oi.copy(), close.copy()
    oi2.iloc[200:] *= 9
    close2.iloc[200:] *= 3
    b = o.signals(oi2, close2)
    for k in a:
        pd.testing.assert_frame_equal(a[k].iloc[:200], b[k].iloc[:200])


def test_oi1_arithmetic_and_oi2_sign():
    oi, close = panel(T=120, N=3)
    oi.iloc[:, :] = 1000.0
    oi.iloc[-1, 0] = 1500.0                                   # +50% buildup on S0 in the last 5 days vs a flat baseline
    close.iloc[-6:, 0] = np.linspace(100, 90, 6)              # ... while the price fell
    s = o.signals(oi, close)
    assert abs(s["OI1"].iloc[-1, 0] - 0.5) < 1e-9             # (1500-1000)/1000
    assert s["OI2"].iloc[-1, 0] < -0.4                        # buildup on a falling stock = short buildup = negative


def test_put_call_signals_direction_and_no_lookahead():
    oi, close = panel(T=120, N=3)
    call = pd.DataFrame(1000.0, index=oi.index, columns=oi.columns)
    put = pd.DataFrame(1000.0, index=oi.index, columns=oi.columns)
    put.iloc[-1, 0] = 3000.0                                  # S0 turns put-heavy on the last day
    s = o.signals(oi, close, call, put)
    assert s["PC1"].iloc[-1, 0] < s["PC1"].iloc[-1, 1:].min()  # put-heavy => lowest score (bearish)
    assert s["PC2"].iloc[-1, 0] < s["PC2"].iloc[-1, 1:].min()  # rising put/call => lowest score
    put2 = put.copy()
    put2.iloc[100:] *= 5
    b = o.signals(oi, close, call, put2)
    pd.testing.assert_frame_equal(s["PC1"].iloc[:100], b["PC1"].iloc[:100])


def test_put_call_is_nan_when_a_name_has_no_options_listed():
    oi, close = panel(T=120, N=3)
    call = pd.DataFrame(1000.0, index=oi.index, columns=oi.columns)
    put = pd.DataFrame(500.0, index=oi.index, columns=oi.columns)
    call.iloc[-1, 0] = 0.0
    put.iloc[-1, 0] = 0.0                                     # S0: no options at all on the last day
    call.iloc[-1, 1] = 0.0                                    # S1: no calls but 500 puts -> a real (very put-heavy) reading
    s = o.signals(oi, close, call, put)
    assert np.isnan(s["PC1"].iloc[-1, 0]) and not np.isnan(s["PC1"].iloc[-1, 1])


def test_oi2_covers_only_oi_up_quadrants_and_oi2x_keeps_the_original_product():
    oi, close = panel(T=120, N=4)
    oi.iloc[:, :] = 1000.0
    oi.iloc[-1, 0] = 1500.0                                   # S0: OI up, price up      -> long buildup   (bullish)
    oi.iloc[-1, 1] = 1500.0                                   # S1: OI up, price down    -> short buildup  (bearish)
    oi.iloc[-1, 2] = 500.0                                    # S2: OI down, price up    -> short covering (bullish lore)
    oi.iloc[-1, 3] = 500.0                                    # S3: OI down, price down  -> long unwinding (bearish lore)
    up = np.linspace(100, 110, 6)
    down = np.linspace(100, 90, 6)
    for j, path in enumerate([up, down, up, down]):
        close.iloc[-6:, j] = path
    s = o.signals(oi, close)
    v = s["OI2"].iloc[-1]
    assert v.iloc[0] > 0 and v.iloc[1] < 0                    # the classic OI-up quadrants: long buildup +, short buildup -
    assert v.iloc[2] == 0 and v.iloc[3] == 0                  # OI-down days carry no score (were inverted before the review)
    x = s["OI2X"].iloc[-1]
    assert x.iloc[2] < 0 and x.iloc[3] > 0                    # the original product scored them opposite to the lore


def test_adjust_oi_puts_pre_split_rows_on_the_post_split_share_basis():
    idx = pd.bdate_range("2020-01-01", periods=10)
    oi = pd.DataFrame({"X": [100.0] * 5 + [1000.0] * 5, "Y": 50.0}, index=idx)          # 10-for-1 split on day 6
    adj = o.adjust_oi(oi, {"X": pd.Series([10.0], index=[idx[5]])})
    assert (adj["X"] == 1000.0).all() and (adj["Y"] == 50.0).all()                       # no jump left; other names untouched


def test_expiry_windows_flag_the_roll_day_and_the_four_days_after_it():
    idx = pd.bdate_range("2020-01-01", periods=30)
    rng = np.random.default_rng(0)
    call = pd.DataFrame(1000.0 * rng.uniform(0.98, 1.02, (30, 20)), index=idx, columns=[f"S{i}" for i in range(20)])
    call.iloc[12:] *= 0.15                                    # ~-85% for every name from day 12 on: an expiry roll
    w = o.expiry_windows(call)
    assert list(np.flatnonzero(w.to_numpy())) == [12, 13, 14, 15, 16]   # the 5-day windows that contain day 12
