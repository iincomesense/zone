"""NSE swing study — कितनी candles (left/right) पर असली swing high / low मार्क होते हैं?

तरीका:
1. Yahoo से NSE F&O के liquid stocks का OHLC (5m, 15m, 75m, 1h, 1d, 1w)।
2. "असली" swing = ZigZag: भाव ATR(14) × K जितना उल्टा चले तब पिछला high/low swing माना जाए
   (यही swings एक trader आँख से चार्ट पर मार्क करता है)। K = 2, 3, 4 तीनों पर।
3. हर असली swing की ताक़त: बाएँ कितनी candles तक उससे ऊँचा high नहीं (left strength),
   दाएँ कितनी candles तक उससे ऊँचा high नहीं (right strength)। low के लिए उल्टा।
4. ta.pivothigh(N, N) / ta.pivotlow(N, N) को N = 2…15 पर चलाकर देखा कि असली swings से
   कितना मेल खाता है: precision (गलत swing कम), recall (असली swing छूटे नहीं), F1 = दोनों का संतुलन।
   सबसे अच्छा F1 वाला N = सुझाया गया "bars on left / right"।
नतीजा: results/swing_study.md और results/swing_study.json
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf

SYMBOLS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK",
    "LT", "ITC", "HINDUNILVR", "BHARTIARTL", "BAJFINANCE", "MARUTI", "SUNPHARMA", "TATAMOTORS",
    "TATASTEEL", "M&M", "ULTRACEMCO", "TITAN", "ASIANPAINT", "NTPC", "POWERGRID", "ONGC",
    "COALINDIA", "HCLTECH", "WIPRO", "TECHM", "JSWSTEEL", "HINDALCO", "ADANIENT", "ADANIPORTS",
    "BAJAJ-AUTO", "DRREDDY", "CIPLA", "GRASIM", "INDUSINDBK", "EICHERMOT", "BPCL", "DLF",
]

TFS = {
    # name: (yahoo interval, period, resample rule or None)
    "5m": ("5m", "60d", None),
    "15m": ("15m", "60d", None),
    "75m": ("15m", "60d", "75min"),
    "1h": ("60m", "730d", None),
    "1d": ("1d", "5y", None),
    "1w": ("1wk", "10y", None),
}
KS = (2.0, 3.0, 4.0)
NS = list(range(2, 26))


def fetch(sym: str, interval: str, period: str) -> pd.DataFrame | None:
    for _ in range(3):
        try:
            d = yf.download(sym + ".NS", period=period, interval=interval, progress=False,
                            auto_adjust=False, threads=False)
            if d is not None and len(d):
                if isinstance(d.columns, pd.MultiIndex):
                    d.columns = [c[0] for c in d.columns]
                d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
                return d
        except Exception:                                    # noqa: BLE001
            time.sleep(2)
    return None


def resample_session(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    df = df.copy()
    df.index = idx
    out = []
    for _, g in df.groupby(df.index.date):
        start = g.index[0].normalize() + pd.Timedelta(hours=9, minutes=15)
        r = g.resample(rule, origin=start).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        out.append(r)
    return pd.concat(out) if out else df


def atr_rma(h, l, c, n=14):
    pc = np.concatenate(([np.nan], c[:-1]))
    tr = np.where(np.isnan(pc), h - l, np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))))
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def zigzag(h, l, atr, k):
    """ATR×K उल्टी चाल पर पक्का होने वाले swing highs / lows की index सूची।"""
    highs, lows = [], []
    n = len(h)
    start = next((i for i in range(n) if np.isfinite(atr[i])), None)
    if start is None:
        return highs, lows
    trend = 0                       # 1 = ऊपर जा रहा (high ढूँढो), -1 = नीचे (low ढूँढो)
    hi_i, lo_i = start, start
    for i in range(start + 1, n):
        a = atr[i]
        if trend >= 0 and h[i] > h[hi_i]:
            hi_i = i
        if trend <= 0 and l[i] < l[lo_i]:
            lo_i = i
        if trend >= 0 and hi_i < i and h[hi_i] - l[i] >= k * a:
            highs.append(hi_i)
            trend, lo_i = -1, i
        elif trend <= 0 and lo_i < i and h[i] - l[lo_i] >= k * a:
            lows.append(lo_i)
            trend, hi_i = 1, i
    return highs, lows


def strength(arr, idx, is_high):
    n = len(arr)
    v = arr[idx]
    left = 0
    j = idx - 1
    while j >= 0 and ((arr[j] < v) if is_high else (arr[j] > v)):
        left += 1
        j -= 1
    right = 0
    j = idx + 1
    while j < n and ((arr[j] < v) if is_high else (arr[j] > v)):
        right += 1
        j += 1
    return left, right, j >= n            # आख़िरी: दाएँ डेटा ख़त्म (censored)


def pivots(arr, N, is_high):
    """Pine ta.pivothigh/low(N, N) जैसा — बीच वाली candle दोनों तरफ़ N candles से सख़्ती से बड़ी/छोटी।"""
    from numpy.lib.stride_tricks import sliding_window_view as swv
    if len(arr) < 2 * N + 1:
        return set()
    w = swv(arr, 2 * N + 1)
    mid = w[:, N]
    others = np.delete(w, N, axis=1)
    ok = (mid > others.max(axis=1)) if is_high else (mid < others.min(axis=1))
    return set((np.nonzero(ok)[0] + N).tolist())


def main():
    rows = {}
    for tf, (iv, per, rule) in TFS.items():
        lefts, rights, mins = {k: [] for k in KS}, {k: [] for k in KS}, {k: [] for k in KS}
        f1acc = {k: {N: [0, 0, 0] for N in NS} for k in KS}      # matched, pivots, zz
        bars_per_swing = {k: [] for k in KS}
        nsym = 0
        for s in SYMBOLS:
            d = fetch(s, iv, per)
            if d is None or len(d) < 120:
                continue
            if rule:
                d = resample_session(d, rule)
            h, l, c = d["high"].to_numpy(float), d["low"].to_numpy(float), d["close"].to_numpy(float)
            atr = atr_rma(h, l, c)
            nsym += 1
            for k in KS:
                zh, zl = zigzag(h, l, atr, k)
                if zh or zl:
                    bars_per_swing[k].append(len(h) / max(1, len(zh) + len(zl)))
                for idx, arr, ih in [(i, h, True) for i in zh] + [(i, l, False) for i in zl]:
                    L_, R_, cens = strength(arr, idx, ih)
                    lefts[k].append(min(L_, 100))
                    if not cens:
                        rights[k].append(min(R_, 100))
                        mins[k].append(min(L_, R_, 100))
                zh_s, zl_s = set(zh), set(zl)
                last_ok = len(h) - 26                         # आख़िरी candles पर दाएँ पूरे बार नहीं
                for N in NS:
                    ph = {i for i in pivots(h, N, True) if i < last_ok}
                    pl = {i for i in pivots(l, N, False) if i < last_ok}
                    zz = {i for i in zh_s if i < last_ok}, {i for i in zl_s if i < last_ok}
                    m = len(ph & zz[0]) + len(pl & zz[1])
                    f1acc[k][N][0] += m
                    f1acc[k][N][1] += len(ph) + len(pl)
                    f1acc[k][N][2] += len(zz[0]) + len(zz[1])
        res = {"symbols": nsym}
        for k in KS:
            f1s = {}
            for N in NS:
                m, p, z = f1acc[k][N]
                prec = m / p if p else 0.0
                rec = m / z if z else 0.0
                f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
                f1s[N] = dict(precision=round(prec, 3), recall=round(rec, 3), f1=round(f1, 3))
            best = max(NS, key=lambda N: f1s[N]["f1"]) if f1s else None
            res[f"K{k:g}"] = dict(
                swings=len(lefts[k]),
                left_mean=round(float(np.mean(lefts[k])), 2) if lefts[k] else None,
                left_median=float(np.median(lefts[k])) if lefts[k] else None,
                right_mean=round(float(np.mean(rights[k])), 2) if rights[k] else None,
                right_median=float(np.median(rights[k])) if rights[k] else None,
                both_median=float(np.median(mins[k])) if mins[k] else None,
                both_p25=float(np.percentile(mins[k], 25)) if mins[k] else None,
                bars_per_swing=round(float(np.mean(bars_per_swing[k])), 1) if bars_per_swing[k] else None,
                best_N=best, best=f1s.get(best), f1_by_N=f1s,
            )
        rows[tf] = res
        print(tf, json.dumps({kk: (vv if not isinstance(vv, dict) else {x: vv[x] for x in vv if x != "f1_by_N"})
                              for kk, vv in res.items()}), flush=True)

    os.makedirs("results", exist_ok=True)
    with open("results/swing_study.json", "w") as f:
        json.dump(rows, f, indent=1)
    lines = ["# NSE swing study", "",
             "K = ZigZag उल्टी चाल ATR(14) × K · L/R = असली swing के बाएँ/दाएँ कितनी candles उससे आगे नहीं गईं ·",
             "best N = ta.pivothigh/low(N,N) का असली swings से सबसे अच्छा मेल (F1)", "",
             "| TF | K | swings | Left median | Right median | min(L,R) median | min(L,R) 25% | best N | precision | recall | F1 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for tf, res in rows.items():
        for k in KS:
            r = res[f"K{k:g}"]
            b = r["best"] or {}
            lines.append(f"| {tf} | {k:g} | {r['swings']} | {r['left_median']} | {r['right_median']} | "
                         f"{r['both_median']} | {r['both_p25']} | {r['best_N']} | {b.get('precision')} | "
                         f"{b.get('recall')} | {b.get('f1')} |")
    with open("results/swing_study.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
