#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  ZONE MTF PRO TERMINAL  ·  single-file build  (v6.0.0)
  Pine Script v6  »  "Zone MTF (KTP rules)"  © iincomesense
───────────────────────────────────────────────────────────────────────────────
  पुराने AI PRO Institutional Terminal के सारे inputs, parameters, नियम, शर्तें
  और लॉजिक हटा दिए गए हैं। उनकी जगह Pine v6 स्क्रिप्ट के पूरे inputs, नियम,
  शर्तें और लॉजिक हैं — और स्कैन दी गई range + सभी 13 time frame पर होता है।

  यह एक ही फ़ाइल है: GitHub पर अपनी पुरानी app.py की जगह रख दें और
  Streamlit Cloud / `streamlit run app.py` से चला दें।
  pip: streamlit · yfinance · pandas · numpy · plotly · requests
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════
#  भाग 1 · आयात (imports)
# ═══════════════════════════════════════════════════════════════════════
import hashlib
import math
import os
import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st

# plotly ज़रूरी नहीं — न मिले तो Altair से चार्ट बनेंगे (Altair streamlit के साथ आता है)
try:
    if os.environ.get("ZONE_NO_PLOTLY"):
        raise ImportError("ZONE_NO_PLOTLY सेट है")
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except Exception:                                              # noqa: BLE001
    go = None
    HAS_PLOTLY = False

try:
    import altair as alt
    HAS_ALTAIR = True
except Exception:                                              # noqa: BLE001
    alt = None
    HAS_ALTAIR = False


# ═══════════════════════════════════════════════════════════════════════
#  भाग 2 · डेटा लेयर — Yahoo Finance candles, NSE सेशन resampling, कैश,
#           F&O यूनिवर्स, मार्केट कैप
# ═══════════════════════════════════════════════════════════════════════
try:
    import yfinance as yf
except Exception:                                            # pragma: no cover
    yf = None

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "zone_mtf")
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except Exception:                                          # read-only FS (Streamlit Cloud)
    import tempfile
    CACHE_DIR = os.path.join(tempfile.gettempdir(), "zone_mtf")
    os.makedirs(CACHE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# TF → (yahoo base interval, period, resample rule or None)
# ---------------------------------------------------------------------------
BASE_SPEC: Dict[str, Tuple[str, str]] = {
    "1m": ("1m", "5d"),          # Yahoo: 1m sirf 7 din
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "60m": ("60m", "1y"),
    "1d": ("1d", "5y"),
    "1wk": ("1wk", "10y"),
    "1mo": ("1mo", "max"),
}

TF_FETCH: Dict[str, Tuple[str, Optional[str]]] = {
    "3m": ("1m", "3min"),
    "5m": ("5m", None),
    "10m": ("5m", "10min"),
    "15m": ("15m", None),
    "30m": ("30m", None),
    "75m": ("15m", "75min"),
    "1h": ("60m", None),
    "2h": ("60m", "120min"),
    "4h": ("60m", "240min"),
    "6h": ("60m", "360min"),
    "1d": ("1d", None),
    "1w": ("1wk", None),
    "1M": ("1mo", None),
}

TTL = {"1m": 120, "5m": 150, "15m": 150, "30m": 180, "60m": 240,
       "1d": 900, "1wk": 3600, "1mo": 3600}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA",
    "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI",
    "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

FALLBACK_FNO = NIFTY50 + [
    "ABB", "ABCAPITAL", "ADANIGREEN", "ADANIPOWER", "ALKEM", "AMBUJACEM", "APLAPOLLO",
    "ASHOKLEY", "ASTRAL", "AUROPHARMA", "BANDHANBNK", "BANKBARODA", "BHEL", "CANBK",
    "CHAMBLFERT", "CHOLAFIN", "CONCOR", "CUMMINSIND", "DABUR", "DIVISLAB", "DLF",
    "FEDERALBNK", "GAIL", "GODREJCP", "HAVELLS", "IDEA", "IDFCFIRSTB", "INDHOTEL",
    "INDIAMART", "INDIGO", "INDUSTOWER", "IOC", "IRCTC", "LTIM", "LTF", "LUPIN",
    "MANAPPURAM", "MARICO", "MOTHERSON", "MRF", "NATIONALUM", "NAUKRI", "NMDC",
    "OBEROIRLTY", "OFSS", "PEL", "PERSISTENT", "PETRONET", "PFC", "PIDILITIND",
    "PNB", "POLYCAB", "RECLTD", "SAIL", "SIEMENS", "SRF", "SUNTV", "TATACHEM",
    "TATAPOWER", "TORNTPHARM", "TVSMOTOR", "UBL", "UNITDSPR", "VBL", "VEDL",
    "VOLTAS", "ZYDUSLIFE",
]


# ---------------------------------------------------------------------------
# cache helpers
# ---------------------------------------------------------------------------
def _ckey(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.pkl")


def _load(key: str, max_age: float):
    p = _path(key)
    if not os.path.exists(p):
        return None
    try:
        if max_age is not None and (time.time() - os.path.getmtime(p)) > max_age:
            return None
        with open(p, "rb") as fh:
            return pickle.load(fh)
    except Exception:                                          # noqa: BLE001
        return None


def _save(key: str, obj) -> None:
    try:
        tmp = _path(key) + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(obj, fh, protocol=4)
        os.replace(tmp, _path(key))
    except Exception:                                          # noqa: BLE001
        pass


def clear_cache() -> None:
    for f in os.listdir(CACHE_DIR):
        try:
            os.remove(os.path.join(CACHE_DIR, f))
        except Exception:                                      # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# NSE : live F&O universe
# ---------------------------------------------------------------------------
def live_fno_universe(timeout: int = 12) -> List[str]:
    key = _ckey("fno_universe")
    cached = _load(key, 6 * 3600)
    if cached:
        return cached
    out: List[str] = []
    try:
        s = requests.Session()
        h = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
             "Accept": "*/*"}
        s.get("https://www.nseindia.com/", headers=h, timeout=timeout)
        r = s.get("https://www.nseindia.com/api/master-quote",
                  headers={**h, "Referer": "https://www.nseindia.com/"}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                out = sorted({str(x).strip().upper() for x in data if str(x).strip()})
    except Exception:                                          # noqa: BLE001
        out = []
    if not out:
        out = list(FALLBACK_FNO)
    _save(key, out)
    return out


# ---------------------------------------------------------------------------
# market cap (Pine: request.financial TOTAL_SHARES_OUTSTANDING * close)
# ---------------------------------------------------------------------------
def market_caps(symbols: Sequence[str], ttl: int = 6 * 3600) -> Dict[str, Optional[float]]:
    """{symbol: market cap in ₹ Cr}  ·  display-only check (Pine rule ≥ 50,000 Cr)."""
    if yf is None or not symbols:
        return {}
    key = _ckey("mcap", tuple(sorted(symbols)))
    cached = _load(key, ttl)
    if cached:
        return cached

    def one(sym: str):
        try:
            fi = yf.Ticker(f"{sym}.NS").fast_info
            try:
                mc = fi.get("marketCap") if hasattr(fi, "get") else fi["marketCap"]
            except Exception:                                  # noqa: BLE001
                mc = None
            return sym, (round(float(mc) / 1e7, 0) if mc else None)
        except Exception:                                      # noqa: BLE001
            return sym, None

    out: Dict[str, Optional[float]] = {}
    try:
        with ThreadPoolExecutor(max_workers=10) as ex:
            for sym, mc in ex.map(one, list(symbols)):
                out[sym] = mc
    except Exception:                                          # noqa: BLE001
        out = {s: None for s in symbols}
    _save(key, out)
    return out


# ---------------------------------------------------------------------------
# yahoo download
# ---------------------------------------------------------------------------
def _flatten(df: pd.DataFrame, sym: str) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # group_by='ticker' -> (Ticker, Price) ; warna (Price, Ticker)
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)
        if f"{sym}.NS" in lvl0:
            sub = df[f"{sym}.NS"]
        elif f"{sym}.NS" in lvl1:
            sub = df.xs(f"{sym}.NS", axis=1, level=1)
        else:
            return None
    else:
        sub = df
    need = {"Open", "High", "Low", "Close"}
    if not need.issubset(set(sub.columns)):
        return None
    out = pd.DataFrame({
        "open": pd.to_numeric(sub["Open"], errors="coerce"),
        "high": pd.to_numeric(sub["High"], errors="coerce"),
        "low": pd.to_numeric(sub["Low"], errors="coerce"),
        "close": pd.to_numeric(sub["Close"], errors="coerce"),
        "volume": pd.to_numeric(sub.get("Volume", 0), errors="coerce").fillna(0),
    })
    out = out.dropna(subset=["open", "high", "low", "close"])
    if out.empty:
        return None
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize(IST)
    else:
        idx = idx.tz_convert(IST)
    out.index = idx
    return out[~out.index.duplicated(keep="last")].sort_index()


def _resample_session(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """NSE session 09:15 se anchored resample (bar-din ke andar hi)."""
    if df is None or df.empty:
        return df
    chunks = []
    for _, g in df.groupby(df.index.normalize(), sort=True):
        if len(g) == 0:
            continue
        r = g.resample(rule, origin=g.index[0], closed="left", label="left").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"})
        r = r.dropna(subset=["open", "high", "low", "close"])
        if len(r):
            chunks.append(r)
    if not chunks:
        return pd.DataFrame(columns=df.columns)
    out = pd.concat(chunks)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _download_base(symbols: Sequence[str], base: str) -> Dict[str, pd.DataFrame]:
    """Ek base interval ke liye bulk download (yahi sabse tez raasta hai)."""
    interval, period = BASE_SPEC[base]
    key = _ckey("dl", base, period, tuple(sorted(symbols)))
    cached = _load(key, TTL.get(base, 300))
    if cached is not None:
        return cached
    if yf is None:
        return {}
    tickers = [f"{s}.NS" for s in symbols]
    raw = None
    try:
        raw = yf.download(tickers, interval=interval, period=period,
                          progress=False, auto_adjust=False,
                          group_by="ticker", threads=True)
    except Exception:                                          # noqa: BLE001
        raw = None
    out: Dict[str, pd.DataFrame] = {}
    if raw is not None and len(raw):
        for s in symbols:
            f = _flatten(raw, s)
            if f is not None and len(f):
                out[s] = f
    # jo symbol bulk me nahi aaya use alag se try karo
    missing = [s for s in symbols if s not in out]
    if missing:
        def one(sym):
            try:
                d = yf.download(f"{sym}.NS", interval=interval, period=period,
                                progress=False, auto_adjust=False, threads=False)
                return sym, _flatten(d, sym)
            except Exception:                                  # noqa: BLE001
                return sym, None
        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, f in ex.map(one, missing):
                if f is not None and len(f):
                    out[sym] = f
    if out:
        _save(key, out)
    return out


def load_frames(symbols: Sequence[str], tfs: Sequence[str], progress=None
                ) -> Dict[str, Dict[str, pd.DataFrame]]:
    """{symbol: {tf: DataFrame}} — zaroori base intervals hi download hote hain."""
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    tfs = list(dict.fromkeys(tfs))
    bases = sorted({TF_FETCH[tf][0] for tf in tfs if tf in TF_FETCH})
    raw: Dict[str, Dict[str, pd.DataFrame]] = {}
    for i, base in enumerate(bases):
        if progress:
            progress(i, len(bases), base)
        raw[base] = _download_base(symbols, base)

    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        per: Dict[str, pd.DataFrame] = {}
        for tf in tfs:
            spec = TF_FETCH.get(tf)
            if not spec:
                continue
            base, rule = spec
            df = raw.get(base, {}).get(sym)
            if df is None or df.empty:
                continue
            per[tf] = _resample_session(df, rule) if rule else df
        if per:
            result[sym] = per
    return result


def last_price(frames: Dict[str, pd.DataFrame]) -> Optional[float]:
    for tf in ["1d", "1h", "15m", "5m", "3m", "1w", "1M"]:
        df = frames.get(tf)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
    for df in frames.values():
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
    return None


def as_of(frames: Dict[str, pd.DataFrame]) -> Optional[datetime]:
    best = None
    for tf in ["1d", "1h", "15m", "5m", "3m"]:
        df = frames.get(tf)
        if df is not None and len(df):
            ts = df.index[-1]
            best = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            break
    return best


def market_open_now(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return (9, 15) <= (now.hour, now.minute) <= (15, 30)

# ═══════════════════════════════════════════════════════════════════════
#  भाग 3 · ज़ोन इंजन — Pine v6 "Zone MTF (KTP rules)" का पूरा पोर्ट
#           1-2-4 · CBLI · formation · white-area · covered leg-out ·
#           maturity · DATR बफ़र · XRPT · score · states · MTF
# ═══════════════════════════════════════════════════════════════════════
# ---------------------------------------------------------------------------
# 1. TIMEFRAMES  (Pine: tf1 … tf9  ·  app: saare 13 TF)
# ---------------------------------------------------------------------------
TF_SECONDS: Dict[str, int] = {
    "3m": 180, "5m": 300, "10m": 600, "15m": 900, "30m": 1800,
    "75m": 4500, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800, "1M": 2592000,
}
ALL_TFS: List[str] = ["3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h",
                      "4h", "6h", "1d", "1w", "1M"]

TF_LABEL_HI = {
    "3m": "3 मिनट", "5m": "5 मिनट", "10m": "10 मिनट", "15m": "15 मिनट",
    "30m": "30 मिनट", "75m": "75 मिनट", "1h": "1 घंटा", "2h": "2 घंटे",
    "4h": "4 घंटे", "6h": "6 घंटे", "1d": "1 दिन", "1w": "1 हफ़्ता",
    "1M": "1 महीना",
}

# ---------------------------------------------------------------------------
# 2. INPUTS  (Pine ke input.* bilkul waise hi, wahi default)
# ---------------------------------------------------------------------------
G0 = "0 · Trade, risk, buffer"
G1 = "1 · Leg-in / Boring / Leg-out (1-2-4)"
G2 = "2 · Validation rules"
G3 = "3 · Quality score (ranking only)"
G4 = "4 · Display"
GM = "5 · MTF lines"
GS = "6 · Shadows & swings"


@dataclass
class PineInputs:
    # ---------------- G0 : trade / risk / buffer -------------------------
    accountCapital: float = 25000.0
    riskPct: float = 0.5
    targetRR: float = 5.0
    traderType: str = "Auto"          # Auto / Aggressive (1M) / Hourly (3M/5M) /
                                      # Daily (10M/15M) / Weekly/Swing (1H/75M/2H) / Monthly (D)
    bufferMode: str = "DATR % (course)"   # or "Chart ATR ×"
    bufAggrPct: float = 2.0
    bufDailyPct: float = 5.0
    bufSwingPct: float = 10.0
    slBufferAtr: float = 0.1

    # ---------------- G1 : 1-2-4 ----------------------------------------
    atrPeriod: int = 14
    minBaseCount: int = 1
    maxBaseCount: int = 3
    maxBaseAtrMult: float = 1.0
    legInMinAtrMult: float = 1.0
    legInMinBodyPct: float = 0.50
    legInMaxClosingWickPct: float = 0.20
    legInToBaseSizeMult: float = 2.0
    legOutToBaseSizeMult: float = 4.0
    maxLegOut: int = 3
    legOutJoinTolAtr: float = 0.25
    legOutTrMult: float = 1.2
    maxWickPct: float = 0.30

    # ---------------- G2 : validation -----------------------------------
    useCBLI: bool = True
    cbliMaxPct: float = 0.50
    useFormation: bool = True
    formationTolAtr: float = 0.10
    useWhiteArea: bool = True
    useCoveredLegOut: bool = True
    useMaturity: bool = True
    maxTouches: int = 3
    distalMode: str = "Pattern lowest/highest (course)"   # or "Boring only"
    requireVolume: bool = False
    useImbalance: bool = False

    # ---------------- G3 : quality score --------------------------------
    volSmaPeriod: int = 20
    hqLegOutTrMult: float = 2.0
    hqLegInAtrMult: float = 1.5
    legOutBodyHeavyPct: float = 0.60
    genuineGapBonus: int = 10
    overnightGapBonus: int = 15
    minValidScore: int = 40
    hqScoreThreshold: int = 90

    # ---------------- G4 : display --------------------------------------
    showInfo: bool = True
    showMcap: bool = True
    minMcapCr: float = 50000.0

    # ---------------- GM : MTF ------------------------------------------
    showMTF: bool = True
    filterDistantMTF: bool = True
    mtfNearPct: float = 8.0

    # ---------------- GS : shadows & swings -----------------------------
    showShadow: bool = True
    showFlags: bool = True
    shadowAtr: float = 0.08
    swingLeft: int = 3
    swingRight: int = 3
    maxStored: int = 220

    # ---------------- app layer (Pine me nahi, scanner ke liye) ---------
    scanRangePct: float = 10.0        # "dिया gaya range" — ± % from LTP
    scanBars: int = 900               # kitne bars par candidate dhoondhe (speed)

    def sanitized(self) -> "PineInputs":
        d = asdict(self)
        d["minBaseCount"] = int(max(1, min(self.minBaseCount, self.maxBaseCount)))
        d["maxBaseCount"] = int(max(d["minBaseCount"], min(self.maxBaseCount, 4)))
        d["maxLegOut"] = int(max(1, min(self.maxLegOut, 3)))
        d["atrPeriod"] = int(max(1, self.atrPeriod))
        d["maxTouches"] = int(max(1, self.maxTouches))
        d["maxStored"] = int(max(10, min(self.maxStored, 400)))
        d["scanBars"] = int(max(200, min(self.scanBars, 5000)))
        out = PineInputs(**d)
        return out


# ---------------------------------------------------------------------------
# 3. SMALL MATH HELPERS  (Pine: ta.rma / ta.sma / true_range / wick_pct …)
# ---------------------------------------------------------------------------
def _rma(x: np.ndarray, n: int) -> np.ndarray:
    """Pine `ta.rma` — Wilder smoothing, SMA seed, pehle n-1 bars na."""
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    seed = np.nanmean(x[:n])
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(x)):
        prev = (prev * (n - 1) + x[i]) / n
        out[i] = prev
    return out


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(x, dtype="float64")
    return s.rolling(n).mean().to_numpy()


def _true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    tr = h - l
    if len(tr) > 1:
        pc = np.concatenate(([np.nan], c[:-1]))
        tr = np.fmax(tr, np.fmax(np.abs(h - pc), np.abs(l - pc)))
        tr[0] = h[0] - l[0]
    return tr


def _wick_pct(o, h, l, c):
    r = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        v = ((h - np.fmax(o, c)) + (np.fmin(o, c) - l)) / np.where(r == 0, np.nan, r)
    return np.nan_to_num(v, nan=0.0)


def _body_pct(o, h, l, c):
    r = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.abs(c - o) / np.where(r == 0, np.nan, r)
    return np.nan_to_num(v, nan=0.0)


def pine_type(tf_sec: float) -> int:
    """Pine f_type(): 1 Aggressive · 2 Hourly · 3 Daily · 4 Weekly/Swing · 5 Monthly."""
    if tf_sec <= 60:
        return 1
    if tf_sec <= 300:
        return 2
    if tf_sec <= 1800:
        return 3
    if tf_sec < 86400:
        return 4
    return 5


_TYPE_NAME = {1: "Aggressive", 2: "Hourly", 3: "Daily", 4: "Weekly/Swing", 5: "Monthly"}

_TYPE_FROM_LABEL = {
    "Aggressive (1M)": 1,
    "Hourly (3M/5M)": 2,
    "Daily (10M/15M)": 3,
    "Weekly/Swing (1H/75M/2H)": 4,
    "Monthly (D)": 5,
}

_GRADE = {1: "Great", 2: "Very good", 3: "Good (½ qty)"}
_GRADE_HI = {1: "बेहतरीन", 2: "बहुत अच्छा", 3: "अच्छा (आधी मात्रा)"}


def _grade(bc: int) -> str:
    return _GRADE.get(bc, "So-so")


# ---------------------------------------------------------------------------
# 4. SERIES — ek symbol+TF ke arrays (position = absolute index)
# ---------------------------------------------------------------------------
class _S:
    __slots__ = ("o", "h", "l", "c", "v", "t", "dow", "day", "n",
                 "tr", "atr", "vol_sma", "wp", "bp", "index")

    def __init__(self, df: pd.DataFrame):
        self.o = df["open"].to_numpy(dtype="float64")
        self.h = df["high"].to_numpy(dtype="float64")
        self.l = df["low"].to_numpy(dtype="float64")
        self.c = df["close"].to_numpy(dtype="float64")
        self.v = df["volume"].to_numpy(dtype="float64") if "volume" in df.columns \
            else np.zeros(len(df))
        idx = pd.DatetimeIndex(df.index)
        self.index = idx
        # Pine `time` = bar open time, milliseconds
        self.t = idx.asi8 // 1_000_000
        self.dow = idx.dayofweek.to_numpy()
        # dayCount — Pine: `if timeframe.change("D") dayCount += 1`
        dts = idx.normalize()
        self.day = np.zeros(len(df), dtype="int64")
        cnt = 0
        for i in range(len(df)):
            if i > 0 and dts[i] != dts[i - 1]:
                cnt += 1
            self.day[i] = cnt
        self.n = len(df)

    def prepare(self, inp: PineInputs) -> None:
        self.tr = _true_range(self.h, self.l, self.c)
        self.atr = _rma(self.tr, inp.atrPeriod)
        self.vol_sma = _sma(self.v, inp.volSmaPeriod)
        self.wp = _wick_pct(self.o, self.h, self.l, self.c)
        self.bp = _body_pct(self.o, self.h, self.l, self.c)


# ---------------------------------------------------------------------------
# 5. CANDIDATE  — Pine f_candidate(bc, nOut) ka seedha port
# ---------------------------------------------------------------------------
def _candidate(p: int, bc: int, n_out: int, S: _S, I: PineInputs) -> Optional[dict]:
    fo = p - (n_out - 1)                 # first leg-out
    b0 = p - n_out                       # first boring
    b_last = p - (n_out + bc - 1)        # last boring
    li = p - (n_out + bc)                # leg-in
    pi = p - (n_out + bc + 1)            # candle behind leg-in
    if pi < 0 or li < 0 or fo < 0 or b_last < 0:
        return None
    if not np.isfinite(S.atr[pi]) or not np.isfinite(S.atr[fo]):
        return None

    o, h, l, c, v = S.o, S.h, S.l, S.c, S.v

    # ---- Leg-in : Filled candle --------------------------------------
    in_high, in_low = h[li], l[li]
    in_range = in_high - in_low
    in_bull = c[li] > o[li]
    in_bear = o[li] > c[li]
    in_tr = S.tr[li]
    if in_range <= 0 or not (in_bull or in_bear):
        return None
    if S.bp[li] < I.legInMinBodyPct:
        return None
    close_wick = (in_high - c[li]) / in_range if in_bull else (c[li] - in_low) / in_range
    if close_wick > I.legInMaxClosingWickPct:
        return None
    if in_tr < I.legInMinAtrMult * S.atr[li]:
        return None

    # ---- CBLI : leg-in ke peeche ulti colour + badi body = trap ------
    if I.useCBLI:
        opp = (in_bull and c[pi] < o[pi]) or (in_bear and c[pi] > o[pi])
        in_body = abs(c[li] - o[li])
        pi_body = abs(c[pi] - o[pi])
        if opp and in_body > 0 and pi_body / in_body > I.cbliMaxPct:
            return None

    # ---- Boring candles ---------------------------------------------
    base_high, base_low = -1e20, 1e20
    base_body_hi, base_body_lo = -1e20, 1e20
    for b in range(b_last, b0 + 1):
        if not np.isfinite(S.atr[b]) or S.tr[b] > I.maxBaseAtrMult * S.atr[b]:
            return None
        base_high = max(base_high, h[b])
        base_low = min(base_low, l[b])
        base_body_hi = max(base_body_hi, max(o[b], c[b]))
        base_body_lo = min(base_body_lo, min(o[b], c[b]))
    base_size = base_high - base_low
    if base_size <= 0:
        return None

    # ---- 1-2-4 part 1 : leg-in >= 2 x boring -------------------------
    if in_range < I.legInToBaseSizeMult * base_size:
        return None

    # ---- first leg-out : Filled & explosive --------------------------
    demand = c[fo] > o[fo]
    supply = o[fo] > c[fo]
    if not (demand or supply):
        return None
    out_tr = S.tr[fo]
    if out_tr < I.legOutTrMult * S.atr[fo]:
        return None
    if S.wp[fo] > I.maxWickPct:
        return None

    # ---- leg-out 2 / 3 : same colour, open ~= previous close ---------
    out_high, out_low = h[fo], l[fo]
    if n_out > 1:
        # Pine: for k = fo-1 downto 0  (k = bars-ago);  position = p - k
        for k in range(n_out - 2, -1, -1):
            kp = p - k              # bar "k ago"
            kp1 = p - (k + 1)       # bar "k+1 ago" (ek bar aur purana)
            same = (c[kp] > o[kp]) if demand else (o[kp] > c[kp])
            if not same:
                return None
            if abs(o[kp] - c[kp1]) > I.legOutJoinTolAtr * S.atr[kp1]:
                return None
            out_high = max(out_high, h[kp])
            out_low = min(out_low, l[kp])

    # ---- 1-2-4 part 2 : leg-out >= 4 x boring ------------------------
    out_extent = out_high - out_low
    if out_extent < I.legOutToBaseSizeMult * base_size:
        return None

    pat = ("RBR" if in_bull else "DBR") if demand else ("DBD" if in_bear else "RBD")
    reversal = pat in ("DBR", "RBD")

    # ---- Formation of leg-out (sirf reversal zones) ------------------
    if I.useFormation and reversal:
        in_body_hi = max(o[li], c[li])
        in_body_lo = min(o[li], c[li])
        tol = I.formationTolAtr * S.atr[fo]
        if o[fo] > in_body_hi + tol or o[fo] < in_body_lo - tol:
            return None

    # ---- White area --------------------------------------------------
    if I.useWhiteArea:
        out_body_near = min(o[fo], c[fo]) if demand else max(o[fo], c[fo])
        if demand and out_body_near <= base_body_lo:
            return None
        if (not demand) and out_body_near >= base_body_hi:
            return None

    # ---- optional : imbalance / volume -------------------------------
    gap = (l[fo] > base_high) if demand else (h[fo] < base_low)
    if I.useImbalance:
        imb = gap or ((c[fo] > in_high) if demand else (c[fo] < in_low))
        if not imb:
            return None
    if I.requireVolume and not (v[fo] > v[li]):
        return None

    # ---- opposite-colour boring candle (score) -----------------------
    opposite_base = False
    for b in range(b_last, b0 + 1):
        if (demand and c[b] < o[b]) or (supply and c[b] > o[b]):
            opposite_base = True
            break

    # Pine: dayofweek[fo] != dayofweek[fo+1]  ->  "fo+1 bars ago" = position b0
    overnight = (S.dow[fo] != S.dow[b0]) or (S.t[fo] - S.t[b0] > 86400000)

    # ---- Quality score ----------------------------------------------
    score = 10
    score += 15 if bc == 1 else (5 if bc == 2 else 0)
    if in_tr >= I.hqLegInAtrMult * S.atr[li]:
        score += 10
    if out_tr >= I.hqLegOutTrMult * in_tr:
        score += 15
    if out_extent >= 2.0 * in_range:
        score += 15
    if np.isfinite(S.vol_sma[fo]) and v[fo] > S.vol_sma[fo]:
        score += 10
    if v[fo] > v[li]:
        score += 10
    out_range = h[fo] - l[fo]
    body_pos = 0.0
    if out_range > 0:
        body_pos = (c[fo] - l[fo]) / out_range if demand else (h[fo] - c[fo]) / out_range
    if body_pos >= 0.80 or S.bp[fo] >= I.legOutBodyHeavyPct:
        score += 15
    if opposite_base:
        score += 10
    if gap:
        score += I.genuineGapBonus
    if overnight and gap:
        score += I.overnightGapBonus
    if score < I.minValidScore:
        return None

    # ---- levels ------------------------------------------------------
    course = I.distalMode == "Pattern lowest/highest (course)"
    if course:
        d_dist = min(base_low, l[li], l[fo]) if reversal else min(base_low, l[fo])
        s_dist = max(base_high, h[li], h[fo]) if reversal else max(base_high, h[fo])
    else:
        d_dist, s_dist = base_low, base_high

    return dict(
        bc=bc, out_count=n_out,
        prox=float(base_high if demand else base_low),
        dist=float(d_dist if demand else s_dist),
        cover=float((o[fo] + c[fo]) / 2.0),
        xrpt=float(max(o[p], c[p]) if demand else min(o[p], c[p])),
        demand=bool(demand), score=int(score), pattern=pat,
        category="Reversal" if reversal else "Continuation",
        leg_in_tr=float(in_tr), leg_out_tr=float(out_tr),
        overnight=bool(overnight), base_high=float(base_high), base_low=float(base_low),
        in_high=float(in_high), in_low=float(in_low), in_range=float(in_range),
        base_size=float(base_size), out_extent=float(out_extent),
        gap=bool(gap), opposite_base=bool(opposite_base), reversal=bool(reversal),
    )


# ---------------------------------------------------------------------------
# 6. ENGINE — zone creation + lifecycle (Pine chart Zone block)
# ---------------------------------------------------------------------------
def _matured(tt: int, created_time_ms: int, created_day: int, dc: int,
             now_ms: int) -> bool:
    """Pine f_matured(): course maturity rules."""
    if tt == 1:
        return True
    if tt == 2:
        return (now_ms - created_time_ms) >= 75 * 60000          # 75 min
    if tt == 3:
        return dc > created_day                                  # next trading day
    if tt == 4:
        return (dc - created_day) >= 7                           # 7 trading days
    return (dc - created_day) >= 21                              # 21 days


def _buffer(I: PineInputs, ttype: int, chart_atr: float, datr: float) -> float:
    """Pine: DATR %  ya  Chart ATR ×.  DATR na ho to chart ATR fallback."""
    buf_pct = I.bufAggrPct if ttype <= 2 else (I.bufDailyPct if ttype == 3 else I.bufSwingPct)
    if I.bufferMode == "DATR % (course)" and np.isfinite(datr) and datr > 0:
        return datr * buf_pct / 100.0
    if np.isfinite(chart_atr):
        return I.slBufferAtr * chart_atr
    return 0.0


def daily_atr_prev(daily: Optional[pd.DataFrame], atr_period: int = 14) -> pd.Series:
    """Pine request.security(D, ta.rma(true_range(), n)[1], lookahead_on).

    Previous completed day ka daily-ATR — isliye har intraday bar par wahi
    value milti hai jo TradingView deta hai (koi repaint nahi).
    """
    if daily is None or len(daily) == 0:
        return pd.Series(dtype="float64")
    d = daily.copy()
    h = d["high"].to_numpy(dtype="float64")
    l = d["low"].to_numpy(dtype="float64")
    c = d["close"].to_numpy(dtype="float64")
    atr = _rma(_true_range(h, l, c), atr_period)
    return pd.Series(atr, index=pd.DatetimeIndex(d.index).normalize()).shift(1)


def _map_datr(index: pd.DatetimeIndex, datr: pd.Series) -> np.ndarray:
    if datr is None or len(datr) == 0:
        return np.full(len(index), np.nan)
    norm = pd.DatetimeIndex(index).normalize()
    out = np.full(len(index), np.nan)
    vals = datr.reindex(norm)
    m = vals.notna().to_numpy()
    out[m] = vals.to_numpy(dtype="float64")[m]
    # agar us din ka daily bar na ho to pichhla available value (ffill)
    if np.isnan(out).any():
        ff = pd.Series(out).ffill().bfill()
        out = ff.to_numpy(dtype="float64")
    return out


def engine(df: pd.DataFrame,
           inputs: PineInputs,
           tf: str,
           datr_series: Optional[pd.Series] = None,
           ttype_override: Optional[int] = None,
           upto: Optional[int] = None,
           return_all: bool = False) -> List[dict]:
    """Ek symbol × ek timeframe ke saare zones (Pine ke chart-zone block jaisa).

    Parameters
    ----------
    df            : OHLCV DataFrame, index tz-aware IST datetime
    inputs        : PineInputs
    tf            : "3m" … "1M"  (sirf metadata + maturity ke liye)
    datr_series   : daily_atr_prev() ka output (buffer ke liye)
    ttype_override: manual "Trader type" (None = Auto → f_type(tf))
    upto          : kis bar tak simulate karna hai (default = last bar)
    """
    I = inputs.sanitized()
    if df is None or len(df) < 30:
        return []
    df = df[~df.index.duplicated(keep="last")].sort_index()
    S = _S(df)
    if S.n < 30:
        return []
    S.prepare(I)

    tf_sec = TF_SECONDS.get(tf, 900)
    ttype = ttype_override if ttype_override is not None else pine_type(tf_sec)
    last = S.n - 1 if upto is None else min(upto, S.n - 1)
    datr_arr = _map_datr(S.index, datr_series) if datr_series is not None \
        else np.full(S.n, np.nan)

    start = max(I.atrPeriod, I.maxBaseCount + I.maxLegOut + 3, 11)
    if I.scanBars and I.scanBars > 0:
        start = max(start, last - I.scanBars)

    zones: List[dict] = []
    alive: List[int] = []

    # ---------- swing pivots (ta.pivothigh / ta.pivotlow) --------------
    L, R = I.swingLeft, I.swingRight
    last_swing_high = last_swing_low = None
    lsh_bar = lsl_bar = None
    high_conf = low_conf = None
    high_break = low_break = None
    pivot_high = np.full(S.n, np.nan)
    pivot_low = np.full(S.n, np.nan)
    for i in range(L + R, S.n):
        # pivot confirmed at bar i, pivot bar = i - R
        pb = i - R
        hi, lo = S.h[pb], S.l[pb]
        is_hi = True
        is_lo = True
        for j in range(pb - L, pb + R + 1):
            if j == pb:
                continue
            if S.h[j] >= hi:
                is_hi = False
            if S.l[j] <= lo:
                is_lo = False
        if is_hi:
            pivot_high[i] = hi
        if is_lo:
            pivot_low[i] = lo

    for p in range(start, last + 1):
        # ---- swing state update (bar p) ------------------------------
        if np.isfinite(pivot_high[p]):
            last_swing_high = pivot_high[p]
            lsh_bar = p - R
            high_conf = p
            high_break = None
        if np.isfinite(pivot_low[p]):
            last_swing_low = pivot_low[p]
            lsl_bar = p - R
            low_conf = p
            low_break = None
        if last_swing_high is not None and high_break is None and p >= (high_conf or 0) \
                and S.c[p] > last_swing_high:
            high_break = p
        if last_swing_low is not None and low_break is None and p >= (low_conf or 0) \
                and S.c[p] < last_swing_low:
            low_break = p

        # ---- candidate search ---------------------------------------
        found = False
        for n_out in range(1, I.maxLegOut + 1):
            if found:
                break
            for bc in range(I.minBaseCount, I.maxBaseCount + 1):
                cand = _candidate(p, bc, n_out, S, I)
                if cand is None:
                    continue
                found = True

                # ---- duplicate check (Pine: 11 alive zones) --------
                duplicate = False
                checked = 0
                for j in range(len(zones) - 1, -1, -1):
                    z = zones[j]
                    if z["state"] not in ("Fresh", "Triggered"):
                        continue
                    if z["demand"] == cand["demand"] and \
                            abs(z["prox"] - cand["prox"]) < S.atr[p] * 0.25:
                        duplicate = True
                        break
                    checked += 1
                    if checked >= 11:
                        break
                if duplicate:
                    break

                b0 = p - n_out
                b_last = p - (n_out + bc - 1)
                left_bar = p - b_last - 1

                # ---- shadow logic ----------------------------------
                behind = False
                base_breaks_old = False
                for z in zones:
                    if z["bar"] < p - b_last:
                        if z["state"] in ("Fresh", "Triggered"):
                            behind = True
                        if z["state"] == "Broken" and z.get("broken_at") is not None \
                                and (p - b_last) <= z["broken_at"] <= b0:
                            for b in range(b0, b_last + 1):
                                if (z["demand"] and S.c[b] < z["dist"]) or \
                                        ((not z["demand"]) and S.c[b] > z["dist"]):
                                    base_breaks_old = True
                                    break
                    if behind and base_breaks_old:
                        break

                swing_ok_h = (last_swing_high is not None and lsh_bar is not None
                              and lsh_bar < p - b_last and (high_conf or 0) < p - b_last
                              and high_break is not None)
                swing_ok_l = (last_swing_low is not None and lsl_bar is not None
                              and lsl_bar < p - b_last and (low_conf or 0) < p - b_last
                              and low_break is not None)
                broke_high = swing_ok_h and high_break is not None and high_break >= p - b_last
                broke_low = swing_ok_l and low_break is not None and low_break >= p - b_last
                swing_break = bool(broke_high or broke_low)

                # ---- risk / reward --------------------------------
                sl_buf = _buffer(I, ttype, S.atr[p], datr_arr[p])
                sl = cand["dist"] - sl_buf if cand["demand"] else cand["dist"] + sl_buf
                risk = abs(cand["prox"] - sl)
                tp = (cand["prox"] + risk * I.targetRR if cand["demand"]
                      else cand["prox"] - risk * I.targetRR)
                xrr = abs(cand["xrpt"] - cand["prox"]) / risk if risk > 0 else 0.0
                qty = int(math.floor(I.accountCapital * I.riskPct / 100.0 / risk)) if risk > 0 else 0

                z = dict(
                    symbol=None, tf=tf, tf_sec=tf_sec,
                    demand=cand["demand"],
                    side="Demand" if cand["demand"] else "Supply",
                    prox=cand["prox"], dist=cand["dist"], cover=cand["cover"],
                    sl=sl, tp=tp, xrpt=cand["xrpt"], xrpt_rr=round(xrr, 2),
                    qty=qty, risk=risk,
                    score=cand["score"], hq=cand["score"] >= I.hqScoreThreshold,
                    pattern=cand["pattern"], category=cand["category"],
                    base_count=cand["bc"], grade=_grade(cand["bc"]),
                    grade_hi=_GRADE_HI.get(cand["bc"], "ठीक-ठाक"),
                    leg_in_tr=cand["leg_in_tr"], leg_out_tr=cand["leg_out_tr"],
                    state="Fresh", touches=0, outside=True, target_hit=False,
                    mtf=False,
                    bar=p, left_bar=left_bar,
                    time=int(S.t[p]), created_bar=p, created_day=int(S.day[p]),
                    broken_at=None,
                    behind=behind, base_break=base_breaks_old, swing_break=swing_break,
                    shadow=bool(I.showShadow and (behind or base_breaks_old or swing_break)),
                    gap=cand["gap"], overnight=cand["overnight"],
                    base_high=cand["base_high"], base_low=cand["base_low"],
                    age_bars=0,
                )
                zones.append(z)
                alive.append(len(zones) - 1)
                if len(zones) > I.maxStored:
                    # Pine: array.shift() -> sabse purana zone (dead ho ya alive)
                    zones.pop(0)
                    alive = [a - 1 for a in alive if a != 0]
                break

        # ---- lifecycle update (Pine "Zone state update" block) ------
        new_alive = []
        for j in alive:
            z = zones[j]
            if z["created_bar"] >= p:
                new_alive.append(j)
                continue
            if z["state"] not in ("Fresh", "Triggered"):
                continue
            hit_sl = (S.l[p] <= z["sl"]) if z["demand"] else (S.h[p] >= z["sl"])
            in_z = (S.l[p] <= z["prox"]) if z["demand"] else (S.h[p] >= z["prox"])
            if z["state"] == "Fresh":
                if in_z:
                    if I.useMaturity and not _matured(ttype, z["time"], z["created_day"],
                                                      int(S.day[p]), int(S.t[p])):
                        z["state"] = "Immature"
                    elif hit_sl:
                        z["state"] = "Broken"
                        z["broken_at"] = p
                    else:
                        z["state"] = "Triggered"
                        z["touches"] = 1
                        z["outside"] = False
                elif I.useCoveredLegOut and (
                        (min(S.o[p], S.c[p]) < z["cover"]) if z["demand"]
                        else (max(S.o[p], S.c[p]) > z["cover"])):
                    z["state"] = "Covered"
            else:
                if hit_sl:
                    z["state"] = "Broken"
                    z["broken_at"] = p
                else:
                    if not z["target_hit"] and (
                            (S.h[p] >= z["tp"]) if z["demand"] else (S.l[p] <= z["tp"])):
                        z["target_hit"] = True
                    if not in_z:
                        z["outside"] = True
                    elif z["outside"]:
                        z["touches"] += 1
                        z["outside"] = False
                        if z["touches"] > I.maxTouches:
                            z["state"] = "Exhausted"
            if z["state"] in ("Fresh", "Triggered"):
                new_alive.append(j)
        alive = new_alive

    # ---- post-processing ------------------------------------------------
    ltp = float(S.c[last])
    out = []
    for j in (range(len(zones)) if return_all else alive):
        if return_all:
            z = zones[j]
            z["age_bars"] = last - z["bar"]
            z["ltp"] = ltp
            z["dist_pct"] = abs(z["prox"] - ltp) / ltp * 100.0 if ltp else None
            z["in_zone"] = bool((S.l[last] <= z["prox"]) if z["demand"]
                                else (S.h[last] >= z["prox"]))
            z["rr"] = I.targetRR
            z["tf_seconds"] = tf_sec
            out.append(z)
            continue
        z = zones[j]
        z["age_bars"] = last - z["bar"]
        z["ltp"] = ltp
        z["dist_pct"] = abs(z["prox"] - ltp) / ltp * 100.0 if ltp else None
        z["in_zone"] = bool(
            (S.l[last] <= z["prox"]) if z["demand"] else (S.h[last] >= z["prox"]))
        z["rr"] = I.targetRR
        z["tf_seconds"] = tf_sec
        out.append(z)
    out.sort(key=lambda r: (r["bar"]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# 7. MTF SNAPSHOT + CONFIRMATION  (Pine f_mtf_engine / f_remote / f_mtf_ok)
# ---------------------------------------------------------------------------
def mtf_snapshot(zone_list: Sequence[dict], live: bool = True) -> dict:
    """Har side ka latest zone proximal — Pine `[latestD, latestS, dTime, sTime]`.

    Pine ka MTF engine sirf alive zones rakhta hai (Immature/Covered → Dead),
    isliye snapshot me bhi wahi filter hai.
    """
    keep = {"Fresh", "Triggered"} if live else {"Fresh", "Triggered", "Immature", "Covered"}
    d = s = None
    dt = st = 0
    best_d = best_s = -1
    for z in zone_list:
        if z["state"] not in keep:
            continue
        if z["demand"] and z["bar"] > best_d:
            best_d, d, dt = z["bar"], z["prox"], z["time"]
        if (not z["demand"]) and z["bar"] > best_s:
            best_s, s, st = z["bar"], z["prox"], z["time"]
    return {"demand": d, "supply": s, "demand_time": dt, "supply_time": st}


def adjacent_tfs(chart_tf: str, peers: Sequence[str]) -> List[str]:
    """Pine f_adj(): ratio != 1 and 0.2 <= tf/chart <= 5.0"""
    base = TF_SECONDS[chart_tf]
    out = []
    for p in peers:
        r = TF_SECONDS[p] / base
        if r != 1.0 and 0.2 <= r <= 5.0:
            out.append(p)
    return out


def mtf_ok(demand: bool, prox: float, dist: float, atr_val: float,
           chart_tf: str, snapshots: Dict[str, dict]) -> bool:
    """Pine f_mtf_ok(): neighbour TF ka same-side proximal is zone ke andar ho."""
    top = max(prox, dist) + 0.5 * (atr_val if np.isfinite(atr_val) else 0.0)
    bot = min(prox, dist) - 0.5 * (atr_val if np.isfinite(atr_val) else 0.0)
    for peer in adjacent_tfs(chart_tf, list(snapshots.keys())):
        lvl = snapshots[peer]["demand"] if demand else snapshots[peer]["supply"]
        if lvl is None:
            continue
        if bot <= lvl <= top:
            return True
    return False


# ---------------------------------------------------------------------------
# 8. PINE INFO LABEL  (f_info)
# ---------------------------------------------------------------------------
def info_text(z: dict, target_rr: float) -> str:
    s = (f"{z['pattern']} · {z['category']} · {z['base_count']} boring "
         f"({_grade(z['base_count'])})")
    s += f"\nScore {z['score']}" + (" HQ" if z["hq"] else "")
    s += f"\nE {z['prox']:.2f}  SL {z['sl']:.2f}"
    s += f"\nT1 1:{target_rr:g} {z['tp']:.2f}"
    s += f"\nXRPT 1:{z['xrpt_rr']:.1f}  Qty {z['qty']}"
    s += (f"\nMTF {'✓' if z['mtf'] else '–'} · {z['state']}"
          + (f" ×{z['touches']}" if z["touches"] else "")
          + (" · T1 hit" if z["target_hit"] else ""))
    return s


# ---------------------------------------------------------------------------
# 9. HIGH LEVEL SCANNER HELPERS
# ---------------------------------------------------------------------------
def scan_symbol(symbol: str,
                frames: Dict[str, pd.DataFrame],
                daily: Optional[pd.DataFrame],
                inputs: PineInputs,
                range_pct: float,
                tfs: Sequence[str] = ALL_TFS,
                ltp: Optional[float] = None) -> dict:
    """Ek symbol ke saare TF scan karo + MTF confirmation + range filter.

    Returns {"rows": [...], "snapshots": {...}, "stats": {...}}
    """
    I = inputs.sanitized()
    ttype_override = _TYPE_FROM_LABEL.get(I.traderType) if I.traderType != "Auto" else None
    datr = daily_atr_prev(daily, I.atrPeriod)

    per_tf: Dict[str, List[dict]] = {}
    atr_map: Dict[str, float] = {}
    for tf in tfs:
        df = frames.get(tf)
        if df is None or len(df) < 40:
            per_tf[tf] = []
            atr_map[tf] = float("nan")
            continue
        zs = engine(df, I, tf, datr_series=datr, ttype_override=ttype_override)
        for z in zs:
            z["symbol"] = symbol
        per_tf[tf] = zs
        S = _S(df)
        S.prepare(I)
        atr_map[tf] = float(S.atr[-1])

    snaps = {tf: mtf_snapshot(per_tf[tf]) for tf in tfs}

    rows: List[dict] = []
    px = ltp if ltp else _last_close(frames, tfs)
    for tf in tfs:
        for z in per_tf[tf]:
            z["mtf"] = mtf_ok(z["demand"], z["prox"], z["dist"], atr_map.get(tf, float("nan")),
                              tf, snaps)
            z["dist_pct"] = abs(z["prox"] - px) / px * 100.0 if px else None
            if range_pct and z["dist_pct"] is not None and z["dist_pct"] > range_pct:
                continue
            rows.append(z)
    rows.sort(key=lambda r: (r["dist_pct"] if r["dist_pct"] is not None else 9e9,
                             -r["score"], TF_SECONDS.get(r["tf"], 0)))
    return {"rows": rows, "snapshots": snaps, "ltp": px, "atr": atr_map}


def _last_close(frames: Dict[str, pd.DataFrame], tfs: Sequence[str]) -> float:
    for tf in ("1d", "1h", "15m", "5m", "3m"):
        if tf in tfs:
            df = frames.get(tf)
            if df is not None and len(df):
                return float(df["close"].iloc[-1])
    for tf in tfs:
        df = frames.get(tf)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
    return 0.0

# ═══════════════════════════════════════════════════════════════════════
#  भाग 4 · Streamlit UI — sidebar में Pine के सारे input, स्कैन,
#           टेबल, ज़ोन चार्ट, MTF तालिका, CSV
# ═══════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# 0 · छोटे helpers
# ---------------------------------------------------------------------------
st.set_page_config(page_title="ज़ोन MTF स्कैनर · KTP नियम",
                   page_icon="📐", layout="wide",
                   initial_sidebar_state="expanded")

STATE_HI = {
    "Fresh": "ताज़ा (Fresh)",
    "Triggered": "सक्रिय (Triggered)",
    "Immature": "अपरिपक्व (Immature)",
    "Covered": "ढका (Covered)",
    "Exhausted": "समाप्त (Exhausted)",
    "Broken": "टूटा (Broken)",
}
SIDE_HI = {"Demand": "मांग (Demand)", "Supply": "आपूर्ति (Supply)"}
CAT_HI = {"Reversal": "पलटाव", "Continuation": "निरंतरता"}
ALL_STATES = ["Fresh", "Triggered", "Immature", "Covered", "Exhausted"]


def stretch(fn, *a, **k):
    """streamlit नए/पुराने दोनों में width चले।"""
    try:
        return fn(*a, width="stretch", **k)
    except TypeError:
        return fn(*a, use_container_width=True, **k)


def fmt_t(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%d-%b %H:%M")


def css():
    st.markdown("""
    <style>
      .block-container{padding-top:1.2rem;padding-bottom:2rem;}
      h1,h2,h3{letter-spacing:.2px;}
      .hero{background:linear-gradient(100deg,#0b132b 0%,#1c2541 45%,#3a506b 100%);
            padding:18px 24px;border-radius:14px;color:#fff;margin-bottom:14px;
            box-shadow:0 6px 22px rgba(11,19,43,.25);}
      .hero h1{margin:0;font-size:1.55rem;color:#fff;}
      .hero p{margin:.35rem 0 0;color:#c9d6e8;font-size:.85rem;}
      .pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.72rem;
            font-weight:600;letter-spacing:.3px;}
      .p-d{background:#d7f5e3;color:#0b6b3a;}
      .p-s{background:#ffe0e0;color:#9b1c1c;}
      .p-n{background:#e9edf5;color:#33415c;}
      div[data-testid="stMetricValue"]{font-size:1.35rem;}
      section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"]{gap:.35rem;}

      /* ───── Google Sheets जैसी शीट ───── */
      .gs-bar{display:flex;align-items:center;gap:10px;font-size:12px;color:#5f6368;
              margin:.2rem 0 .45rem;}
      .gs-bar b{color:#202124;}
      .gs-wrap{border:1px solid #dadce0;border-radius:6px;background:#fff;
               box-shadow:0 1px 3px rgba(60,64,67,.10);overflow:hidden;}
      .gs-scroll{max-height:620px;overflow:auto;}
      table.gs{border-collapse:separate;border-spacing:0;width:100%;
               font-family:Roboto,'Noto Sans Devanagari',Arial,sans-serif;
               font-size:12.5px;color:#202124;}
      table.gs th,table.gs td{border-right:1px solid #e8eaed;border-bottom:1px solid #e8eaed;
               padding:5px 9px;white-space:nowrap;vertical-align:middle;}
      table.gs thead th{position:sticky;background:#f1f3f4;font-weight:700;color:#3c4043;
               text-align:left;z-index:2;}
      table.gs thead tr.gs-letters th{top:0;background:#e8eaed;color:#5f6368;font-size:10px;
               text-align:center;padding:1px 6px;z-index:3;font-weight:600;}
      table.gs thead tr.gs-head th{top:19px;box-shadow:inset 0 -1px 0 #dadce0;}
      table.gs th.gs-rn,table.gs td.gs-rn{position:sticky;left:0;z-index:1;background:#f8f9fa;
               color:#80868b;text-align:center;font-size:10px;padding:5px 6px;
               min-width:34px;border-right:1px solid #dadce0;}
      table.gs thead th.gs-rn{z-index:4;}
      table.gs tbody tr:hover td{background:#f1f8ff;}
      table.gs tbody tr:hover td.gs-rn{background:#f1f3f4;}
      table.gs td.num{text-align:right;font-variant-numeric:tabular-nums;}
      table.gs td.ctr{text-align:center;}
      table.gs a{color:#1a73e8;text-decoration:none;font-weight:600;}
      table.gs a:hover{text-decoration:underline;}
      .gs-pill{display:inline-block;padding:1px 8px;border-radius:11px;font-size:10.5px;
               font-weight:700;}
      .gs-d{background:#ceead6;color:#0b6b3a;}
      .gs-s{background:#fce8e6;color:#a50e0e;}
      .gs-fresh{color:#0b6b3a;font-weight:600;}
      .gs-trig{color:#1a73e8;font-weight:600;}
      .gs-imm{color:#b06000;font-weight:600;}
      .gs-cov{color:#7b61ff;font-weight:600;}
      .gs-exh{color:#80868b;font-weight:600;}
      .gs-ok{color:#0b6b3a;font-weight:700;}
      .gs-no{color:#d93025;font-weight:700;}
    </style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 1 · SIDEBAR  — Pine के सारे input, वही default, वही नाम
# ---------------------------------------------------------------------------
TFS_ALL = list(ALL_TFS)


def reset_inputs():
    for k in list(st.session_state.keys()):
        if str(k).startswith("in_"):
            del st.session_state[k]


def preset_loose():
    """Pine default से थोड़ा ढीला — सिर्फ़ खोज के लिए।"""
    for k, v in {"in_losize": 3.0, "in_lisize": 1.5, "in_minscorep": 30,
                 "in_baseatr": 1.2, "in_libody": 0.40, "in_liwick": 0.30}.items():
        st.session_state[k] = v


def sidebar():
    sb = st.sidebar
    sb.markdown("## 📐 ज़ोन MTF स्कैनर")
    sb.caption("Pine v6 · Zone MTF (KTP rules) — ₹ मूल्य, सभी 13 TF")
    c_r1, c_r2 = sb.columns(2)
    c_r1.button("↺ Pine default", on_click=reset_inputs, width="stretch",
                help="सारे input वापस Pine v6 के मूल default पर")
    c_r2.button("🔓 ढीला", on_click=preset_loose, width="stretch",
                help="कुछ ज़्यादा ज़ोन दिखेंगे (Pine default नहीं — प्रयोग के लिए)")

    # ---------------- स्कैन सेटिंग (ऐप स्तर) ----------------
    with sb.expander("🔭 स्कैन सेटिंग", expanded=True):
        uni = st.selectbox(
            "यूनिवर्स", ["NIFTY 50", "F&O (NSE लाइव)", "NIFTY 50 + F&O", "अपनी सूची"],
            key="in_uni")
        custom = ""
        if uni == "अपनी सूची":
            custom = st.text_area("स्क्रिप्ट (कॉमा / स्पेस से अलग)", "RELIANCE, TCS, INFY",
                                  key="in_custom", height=80)
        scan_range = st.number_input(
            "स्कैन रेंज — LTP से ±%", 0.5, 100.0, 10.0, 0.5, key="in_range",
            help="Pine में नहीं है; सिर्फ़ उतने ही ज़ोन दिखेंगे जो LTP से इस % के अंदर हों।")
        scan_bars = st.number_input(
            "कितने बार पीछे देखें (scanBars)", 200, 5000, 900, 100, key="in_scanbars",
            help="तेज़ी के लिए; 1-2-4 ढाँचा इतने बार के अंदर ही खोजा जाएगा।")
        tfs = st.multiselect("स्कैन के time frame", TFS_ALL, default=TFS_ALL,
                             format_func=lambda t: f"{t} · {TF_LABEL_HI[t]}",
                             key="in_tfs",
                             help="डिफ़ॉल्ट = सभी 13 time frame।")
        min_score = st.slider("सिर्फ़ इतने स्कोर से ऊपर", 0, 150, 40, 5, key="in_minscore")
        states = st.multiselect("ज़ोन की स्थिति दिखाएँ", ALL_STATES,
                                default=["Fresh", "Triggered", "Immature", "Covered"],
                                format_func=lambda s: STATE_HI[s], key="in_states")
        max_syms = st.number_input("अधिकतम स्क्रिप्ट (0 = सभी)", 0, 2000, 50, 10,
                                   key="in_maxsyms",
                                   help="F&O में ~200 स्क्रिप्ट हैं; पहली बार डेटा लाने में समय लगता है।")
        only_mtf = st.checkbox("सिर्फ़ MTF-confirmed ज़ोन", value=False, key="in_onlymtf")
        only_hq = st.checkbox("सिर्फ़ HQ ज़ोन (score ≥ HQ threshold)", value=False, key="in_onlyhq")

    # ---------------- G0 ----------------
    with sb.expander("0 · पूँजी, जोखिम, बफ़र"):
        cap = st.number_input("खाता पूँजी ₹ (accountCapital)", 1000.0, 1e9, 25000.0, 1000.0,
                              key="in_cap")
        risk = st.number_input("जोखिम % (riskPct)", 0.05, 10.0, 0.5, 0.05, key="in_risk")
        rr = st.number_input("टारगेट RR (targetRR)", 0.5, 20.0, 5.0, 0.5, key="in_rr")
        ttype = st.selectbox(
            "ट्रेडर प्रकार (traderType)",
            ["Auto", "Aggressive (1M)", "Hourly (3M/5M)", "Daily (10M/15M)",
             "Weekly/Swing (1H/75M/2H)", "Monthly (D)"], key="in_ttype")
        bmode = st.selectbox("बफ़र विधि (bufferMode)",
                             ["DATR % (course)", "Chart ATR ×"], key="in_bmode")
        b_aggr = st.number_input("Aggressive बफ़र % (bufAggrPct)", 0.0, 100.0, 2.0, 0.5,
                                 key="in_baggr")
        b_day = st.number_input("Daily बफ़र % (bufDailyPct)", 0.0, 100.0, 5.0, 0.5,
                                key="in_bday")
        b_swing = st.number_input("Swing बफ़र % (bufSwingPct)", 0.0, 100.0, 10.0, 0.5,
                                  key="in_bswing")
        sl_buf = st.number_input("SL बफ़र ATR × (slBufferAtr)", 0.0, 5.0, 0.1, 0.05,
                                 key="in_slbuf")

    # ---------------- G1 ----------------
    with sb.expander("1 · Leg-in / Boring / Leg-out (1-2-4)"):
        atr_p = st.number_input("ATR पीरियड (atrPeriod)", 2, 200, 14, 1, key="in_atr")
        min_bc = st.number_input("न्यूनतम boring (minBaseCount)", 1, 4, 1, 1, key="in_minbc")
        max_bc = st.number_input("अधिकतम boring (maxBaseCount)", 1, 4, 3, 1, key="in_maxbc",
                                 help="4 से ज़्यादा = trap")
        base_atr = st.number_input("boring ≤ ATR × (maxBaseAtrMult)", 0.1, 5.0, 1.0, 0.1,
                                   key="in_baseatr")
        li_atr = st.number_input("leg-in ≥ ATR × (legInMinAtrMult)", 0.0, 5.0, 1.0, 0.1,
                                 key="in_liatr")
        li_body = st.number_input("leg-in body ≥ % (legInMinBodyPct)", 0.0, 1.0, 0.50, 0.05,
                                  key="in_libody")
        li_wick = st.number_input("leg-in closing wick ≤ % (legInMaxClosingWickPct)",
                                  0.0, 1.0, 0.20, 0.05, key="in_liwick")
        li_size = st.number_input("leg-in ≥ base × (legInToBaseSizeMult)", 0.0, 10.0, 2.0, 0.1,
                                  key="in_lisize")
        lo_size = st.number_input("leg-out ≥ base × (legOutToBaseSizeMult)", 0.0, 20.0, 4.0, 0.1,
                                  key="in_losize")
        max_lo = st.number_input("leg-out के अधिकतम candles (maxLegOut)", 1, 3, 3, 1,
                                 key="in_maxlo")
        join_tol = st.number_input("leg-out join सहनशीलता ATR × (legOutJoinTolAtr)",
                                   0.0, 2.0, 0.25, 0.05, key="in_jointol")
        lo_tr = st.number_input("leg-out TR ≥ ATR × (legOutTrMult)", 0.0, 5.0, 1.2, 0.1,
                                key="in_lotr")
        max_wick = st.number_input("leg-out wick ≤ % (maxWickPct)", 0.0, 1.0, 0.30, 0.05,
                                   key="in_maxwick")

    # ---------------- G2 ----------------
    with sb.expander("2 · सत्यापन नियम"):
        c_cbli = st.checkbox("CBLI जाँच (useCBLI)", True, key="in_cbli")
        cbli_p = st.number_input("CBLI सीमा % (cbliMaxPct)", 0.0, 1.0, 0.50, 0.05,
                                 key="in_cblip")
        c_form = st.checkbox("Formation (DBR/RBD) जाँच (useFormation)", True, key="in_form")
        form_tol = st.number_input("Formation सहनशीलता ATR × (formationTolAtr)",
                                   0.0, 2.0, 0.10, 0.05, key="in_formtol")
        c_white = st.checkbox("White area (useWhiteArea)", True, key="in_white")
        c_cov = st.checkbox("Covered leg-out (useCoveredLegOut)", True, key="in_cov")
        c_mat = st.checkbox("Maturity (useMaturity)", True, key="in_mat")
        max_touch = st.number_input("अधिकतम touches (maxTouches)", 1, 20, 3, 1,
                                    key="in_maxtouch")
        distal = st.selectbox("Distal विधि (distalMode)",
                              ["Pattern lowest/highest (course)", "Boring only"],
                              key="in_distal")
        c_vol = st.checkbox("Volume अनिवार्य (requireVolume)", False, key="in_vol")
        c_imb = st.checkbox("Imbalance जाँच (useImbalance)", False, key="in_imb")

    # ---------------- G3 ----------------
    with sb.expander("3 · Quality score"):
        vol_sma = st.number_input("Volume SMA (volSmaPeriod)", 2, 200, 20, 1, key="in_volsma")
        hq_lo = st.number_input("HQ leg-out TR ≥ leg-in × (hqLegOutTrMult)", 0.0, 10.0, 2.0,
                                0.1, key="in_hqlo")
        hq_li = st.number_input("HQ leg-in ≥ ATR × (hqLegInAtrMult)", 0.0, 10.0, 1.5, 0.1,
                                key="in_hqli")
        heavy = st.number_input("भारी body % (legOutBodyHeavyPct)", 0.0, 1.0, 0.60, 0.05,
                                key="in_heavy")
        gap_b = st.number_input("Genuine gap बोनस (genuineGapBonus)", 0, 50, 10, 1,
                                key="in_gapb")
        on_b = st.number_input("Overnight gap बोनस (overnightGapBonus)", 0, 50, 15, 1,
                               key="in_onb")
        min_score_p = st.number_input("न्यूनतम मान्य स्कोर (minValidScore)", 0, 200, 40, 1,
                                      key="in_minscorep")
        hq_thr = st.number_input("HQ सीमा (hqScoreThreshold)", 0, 300, 90, 1, key="in_hqthr")

    # ---------------- G4 + GM + GS ----------------
    with sb.expander("4 · प्रदर्शन / MTF / Shadow"):
        s_info = st.checkbox("Info लेबल (showInfo)", True, key="in_info")
        s_mcap = st.checkbox("मार्केट कैप (showMcap)", True, key="in_mcap")
        min_mcap = st.number_input("न्यूनतम मार्केट कैप ₹Cr (minMcapCr)", 0.0, 1e6, 50000.0,
                                   1000.0, key="in_minmcap")
        s_mtf = st.checkbox("MTF पंक्तियाँ (showMTF)", True, key="in_showmtf")
        f_dmtf = st.checkbox("दूर के MTF ज़ोन छाँटें (filterDistantMTF)", True, key="in_fdmtf")
        mtf_near = st.number_input("MTF निकटता % (mtfNearPct)", 0.5, 50.0, 8.0, 0.5,
                                   key="in_mtfnear")
        peers = st.multiselect(
            "MTF समय-सीमाएँ (Pine tf1…tf9)", TFS_ALL,
            default=["3m", "5m", "10m", "15m", "30m", "1h", "75m", "2h", "1d"],
            format_func=lambda t: f"{t} · {TF_LABEL_HI[t]}", key="in_peers")
        s_shadow = st.checkbox("Shadow ज़ोन (showShadow)", True, key="in_shadow")
        s_flags = st.checkbox("BASE BREAK / SWING फ़्लैग (showFlags)", True, key="in_flags")
        shadow_atr = st.number_input("Shadow दूरी ATR × (shadowAtr)", 0.0, 1.0, 0.08, 0.01,
                                     key="in_shadowatr")
        sw_l = st.number_input("Swing बाएँ (swingLeft)", 1, 20, 3, 1, key="in_swl")
        sw_r = st.number_input("Swing दाएँ (swingRight)", 1, 20, 3, 1, key="in_swr")
        max_store = st.number_input("ज़ोन भंडार (maxStored)", 10, 400, 220, 10,
                                    key="in_maxstore")

    inp = PineInputs(
        accountCapital=float(cap), riskPct=float(risk), targetRR=float(rr),
        traderType=ttype, bufferMode=bmode,
        bufAggrPct=float(b_aggr), bufDailyPct=float(b_day), bufSwingPct=float(b_swing),
        slBufferAtr=float(sl_buf),
        atrPeriod=int(atr_p), minBaseCount=int(min_bc), maxBaseCount=int(max_bc),
        maxBaseAtrMult=float(base_atr), legInMinAtrMult=float(li_atr),
        legInMinBodyPct=float(li_body), legInMaxClosingWickPct=float(li_wick),
        legInToBaseSizeMult=float(li_size), legOutToBaseSizeMult=float(lo_size),
        maxLegOut=int(max_lo), legOutJoinTolAtr=float(join_tol), legOutTrMult=float(lo_tr),
        maxWickPct=float(max_wick),
        useCBLI=bool(c_cbli), cbliMaxPct=float(cbli_p), useFormation=bool(c_form),
        formationTolAtr=float(form_tol), useWhiteArea=bool(c_white),
        useCoveredLegOut=bool(c_cov), useMaturity=bool(c_mat), maxTouches=int(max_touch),
        distalMode=distal, requireVolume=bool(c_vol), useImbalance=bool(c_imb),
        volSmaPeriod=int(vol_sma), hqLegOutTrMult=float(hq_lo), hqLegInAtrMult=float(hq_li),
        legOutBodyHeavyPct=float(heavy), genuineGapBonus=int(gap_b),
        overnightGapBonus=int(on_b), minValidScore=int(min_score_p),
        hqScoreThreshold=int(hq_thr),
        showInfo=bool(s_info), showMcap=bool(s_mcap), minMcapCr=float(min_mcap),
        showMTF=bool(s_mtf), filterDistantMTF=bool(f_dmtf), mtfNearPct=float(mtf_near),
        showShadow=bool(s_shadow), showFlags=bool(s_flags), shadowAtr=float(shadow_atr),
        swingLeft=int(sw_l), swingRight=int(sw_r), maxStored=int(max_store),
        scanRangePct=float(scan_range), scanBars=int(scan_bars),
    ).sanitized()

    opts = dict(universe=uni, custom=custom, range_pct=float(scan_range), tfs=tuple(tfs),
                max_syms=int(max_syms),
                min_score=int(min_score), states=list(states), only_mtf=bool(only_mtf),
                only_hq=bool(only_hq), peers=tuple(peers), min_mcap=float(min_mcap),
                show_mcap=bool(s_mcap))
    return inp, opts


# ---------------------------------------------------------------------------
# 2 · SCAN
# ---------------------------------------------------------------------------
def resolve_universe(uni: str, custom: str):
    if uni == "NIFTY 50":
        return list(dict.fromkeys(NIFTY50))
    if uni == "F&O (NSE लाइव)":
        try:
            u = live_fno_universe()
            return list(dict.fromkeys(u)) if u else list(dict.fromkeys(FALLBACK_FNO))
        except Exception:
            return list(dict.fromkeys(FALLBACK_FNO))
    if uni == "NIFTY 50 + F&O":
        try:
            u = list(live_fno_universe())
        except Exception:
            u = []
        return list(dict.fromkeys(list(NIFTY50) + (u or list(FALLBACK_FNO))))
    return [s.strip().upper() for s in custom.replace("\n", ",").split(",") if s.strip()]


def run_scan(symbols, inp, opts, ph, pbar):
    tfs = list(opts["tfs"]) or TFS_ALL
    frames = load_frames(symbols, tfs)          # disk cache की वजह से तेज़
    rows, snaps, atrs, ltps, errs = [], {}, {}, {}, {}
    jobs = [(s, frames[s]) for s in symbols if s in frames]
    missing = [s for s in symbols if s not in frames]
    for s in missing:
        errs[s] = "डेटा नहीं मिला"

    if not jobs:
        return dict(rows=pd.DataFrame(), snaps={}, atr={}, ltp={}, errs=errs,
                    at=datetime.now(IST), scanned=0)

    done = 0
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
        futs = {ex.submit(scan_symbol, s, f, f.get("1d"), inp, 1e9, tfs): s
                for s, f in jobs}
        for fu in as_completed(futs):
            s = futs[fu]
            done += 1
            pbar.progress(done / len(jobs), text=f"स्कैन: {s}  ({done}/{len(jobs)})")
            try:
                r = fu.result()
            except Exception as e:                            # noqa: BLE001
                errs[s] = f"{type(e).__name__}: {e}"
                continue
            snaps[s] = r["snapshots"]
            atrs[s] = r["atr"]
            ltps[s] = r["ltp"]
            rows.extend(r["rows"])

    # MTF peers — Pine tf1…tf9
    peers = set(opts["peers"])
    if peers and peers != set(tfs):
        for z in rows:
            sn = {k: v for k, v in snaps.get(z["symbol"], {}).items() if k in peers}
            z["mtf"] = mtf_ok(z["demand"], z["prox"], z["dist"],
                                atrs.get(z["symbol"], {}).get(z["tf"], float("nan")),
                                z["tf"], sn)

    df = pd.DataFrame(rows)
    if not df.empty:
        df.insert(0, "symbol", df.pop("symbol"))
        df["dist_abs"] = (df["prox"] - df["ltp"]).abs()
        df["side_hi"] = df["side"].map(SIDE_HI)
        df["state_hi"] = df["state"].map(STATE_HI).fillna(df["state"])
        df["cat_hi"] = df["category"].map(CAT_HI).fillna(df["category"])
        df["tf_hi"] = df["tf"].map(TF_LABEL_HI).fillna(df["tf"])
        df["created_ist"] = df["time"].map(fmt_t)
        df = df.sort_values(["dist_pct", "score"], ascending=[True, False]).reset_index(drop=True)
    return dict(rows=df, snaps=snaps, atr=atrs, ltp=ltps, errs=errs,
                at=datetime.now(IST), scanned=len(jobs))


# ---------------------------------------------------------------------------
# 3 · TABLE / CHART
# ---------------------------------------------------------------------------
# ---- TradingView लिंक (ज़ोन के टाइम फ्रेम पर ही खुले) ----
TV_TF = {"3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30", "75m": "75",
         "1h": "60", "2h": "120", "4h": "240", "6h": "360", "1d": "D", "1w": "W",
         "1M": "M"}


def tv_url(symbol: str, tf: str) -> str:
    """https://www.tradingview.com/chart/?symbol=NSE:SBIN&interval=15"""
    from urllib.parse import quote
    return (f"https://www.tradingview.com/chart/?symbol={quote('NSE:' + str(symbol))}"
            f"&interval={TV_TF.get(str(tf), '15')}")


def _col_letter(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA"""
    out, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _lerp(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _score_bg(sc: float) -> str:
    """Google Sheets जैसा conditional रंग: लाल → पीला → हरा"""
    low, mid, high = (252, 232, 230), (254, 247, 224), (206, 234, 214)
    t = max(0.0, min(1.0, (float(sc) - 30.0) / 90.0))
    c = _lerp(low, mid, t / 0.5) if t <= 0.5 else _lerp(mid, high, (t - 0.5) / 0.5)
    return f"rgb({c[0]},{c[1]},{c[2]})"


_STATE_CLS = {"Fresh": "gs-fresh", "Triggered": "gs-trig", "Immature": "gs-imm",
              "Covered": "gs-cov", "Exhausted": "gs-exh", "Broken": "gs-no"}


def sheet_html(df: pd.DataFrame, show_mcap=True, min_mcap=50000.0,
               max_rows: int = 400) -> str:
    """Google Sheets जैसी टेबल — column letters, row numbers, sticky header,
    रंग-कोडेड स्कोर, और symbol/TF पर TradingView लिंक (उसी TF का interval)।"""
    if df is None or df.empty:
        return ""
    d = df.head(max_rows)
    cols = [("स्क्रिप्ट", "s"), ("TF", "s"), ("पक्ष", "s"), ("पैटर्न", "ctr"),
            ("बेस", "s"), ("एंट्री", "num"), ("डिस्टल", "num"), ("SL", "num"),
            ("T1", "num"), ("दूरी %", "num"), ("स्कोर", "ctr"), ("HQ", "ctr"),
            ("MTF", "ctr"), ("स्थिति", "s"), ("XRPT", "num"), ("मात्रा", "num")]
    if show_mcap:
        cols.append(("मार्केट कैप ₹Cr", "num"))
    cols += [("बना (IST)", "ctr"), ("चार्ट", "ctr")]

    letters = "".join(f"<th>{_col_letter(i)}</th>" for i in range(len(cols) + 1))
    head = "".join(f'<th class="{a}">{t}</th>' for t, a in cols)

    body = []
    for n, (_, r) in enumerate(d.iterrows(), start=2):
        u = tv_url(r["symbol"], r["tf"])
        sym = (f'<a href="{u}" target="_blank" rel="noopener" '
               f'title="TradingView पर {r["symbol"]} · {r["tf"]}">{r["symbol"]}</a>')
        tf_c = (f'<a href="{u}" target="_blank" rel="noopener" '
                f'title="TradingView · {r["tf"]}">{r["tf"]}</a>')
        side = (f'<span class="gs-pill {"gs-d" if r["demand"] else "gs-s"}">'
                f'{"मांग" if r["demand"] else "आपूर्ति"}</span>')
        stt = (f'<span class="{_STATE_CLS.get(str(r["state"]), "")}">'
               f'{r["state_hi"]}</span>')
        sc_bg = _score_bg(r["score"])
        cap = ""
        if show_mcap:
            mv = r.get("mcap_cr", float("nan"))
            if mv is None or (isinstance(mv, float) and pd.isna(mv)):
                cap = '<td class="num">—</td>'
            else:
                ok = float(mv) >= float(min_mcap)
                cap = (f'<td class="num">{float(mv):,.0f} '
                       f'<span class="{"gs-ok" if ok else "gs-no"}">'
                       f'{"✓" if ok else "✗"}</span></td>')
        hq_cell = '<span style="color:#f9ab00;font-size:14px">★</span>' if r["hq"] else ""
        mtf_cell = ('<span class="gs-ok">✓</span>' if r["mtf"]
                    else '<span class="gs-exh">–</span>')
        t1c = f'<td class="ctr"><a href="{u}" target="_blank" rel="noopener">📈</a></td>'
        body.append(
            f'<tr><td class="gs-rn">{n}</td>'
            f'<td>{sym}</td><td>{tf_c}</td><td>{side}</td>'
            f'<td class="ctr">{r["pattern"]}</td>'
            f'<td>{int(r["base_count"])} · {_GRADE_HI.get(int(r["base_count"]), "")}</td>'
            f'<td class="num">{r["prox"]:,.2f}</td>'
            f'<td class="num">{r["dist"]:,.2f}</td>'
            f'<td class="num">{r["sl"]:,.2f}</td>'
            f'<td class="num">{r["tp"]:,.2f}</td>'
            f'<td class="num">{r["dist_pct"]:.2f}%</td>'
            f'<td class="ctr" style="background:{sc_bg};font-weight:700">{int(r["score"])}</td>'
            f'<td class="ctr">{hq_cell}</td>'
            f'<td class="ctr">{mtf_cell}</td>'
            f'<td>{stt}</td>'
            f'<td class="num">1:{r["xrpt_rr"]:.2f}</td>'
            f'<td class="num">{int(r["qty"])}</td>'
            f'{cap}'
            f'<td class="ctr">{r["created_ist"]}</td>'
            f'{t1c}</tr>')

    more = ""
    if len(df) > len(d):
        more = (f'<div class="gs-bar">… और {len(df) - len(d)} पंक्तियाँ छिपी हैं '
                f'(CSV डाउनलोड करें)</div>')
    return (
        '<div class="gs-wrap"><div class="gs-scroll"><table class="gs">'
        f'<thead><tr class="gs-letters"><th class="gs-rn"></th>{letters}</tr>'
        f'<tr class="gs-head"><th class="gs-rn">1</th>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div></div>{more}')


def zone_table(df: pd.DataFrame, show_mcap=True, min_mcap=50000.0):
    d = pd.DataFrame({
        "स्क्रिप्ट": df["symbol"],
        "TF": df["tf"],
        "पक्ष": df["side_hi"],
        "पैटर्न": df["pattern"],
        "बेस": df["base_count"].map(lambda b: f"{int(b)} · {_GRADE_HI.get(int(b), '')}"),
        "एंट्री (proximal)": df["prox"].round(2),
        "डिस्टल": df["dist"].round(2),
        "SL": df["sl"].round(2),
        "T1": df["tp"].round(2),
        "दूरी %": df["dist_pct"].round(2),
        "स्कोर": df["score"].astype(int),
        "HQ": df["hq"].map(lambda v: "★" if v else ""),
        "MTF": df["mtf"].map(lambda v: "✓" if v else "–"),
        "स्थिति": df["state_hi"],
        "XRPT RR": df["xrpt_rr"],
        "मात्रा": df["qty"].astype(int),
    })
    if show_mcap:
        mc = df["mcap_cr"] if "mcap_cr" in df.columns else None
        if mc is not None:
            d["मार्केट कैप ₹Cr"] = mc.map(
                lambda v: "—" if pd.isna(v) else (f"{v:,.0f}" + (" ✓" if v >= min_mcap else " ✗")))
    d["बना (IST)"] = df["created_ist"]
    return d


def zone_chart(df: pd.DataFrame, zones: pd.DataFrame, sym: str, tf: str,
               inputs: PineInputs, sl: float | None = None, tp: float | None = None):
    """Plotly हो तो Plotly; वरना Altair (streamlit के साथ bundled)।"""
    if HAS_PLOTLY:
        fig = _zone_chart_plotly(df, zones, sym, tf, inputs)
        if sl is not None:
            fig.add_hline(y=float(sl), line=dict(color="#d62728", width=1, dash="dash"),
                          annotation_text=f"SL {float(sl):.2f}", annotation_position="right")
        if tp is not None:
            fig.add_hline(y=float(tp), line=dict(color="#2ca02c", width=1, dash="dash"),
                          annotation_text=f"T1 {float(tp):.2f}", annotation_position="right")
        return fig
    return _zone_chart_altair(df, zones, sym, tf, sl, tp)


def _zone_chart_plotly(df: pd.DataFrame, zones: pd.DataFrame, sym: str, tf: str,
                       inputs: PineInputs):
    d = df.tail(400)
    x = d.index
    step = (x[-1] - x[-2]) if len(x) > 1 else pd.Timedelta(minutes=15)
    last_x = x[-1]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=x, open=d["open"], high=d["high"], low=d["low"], close=d["close"],
        name=sym, increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350", showlegend=False))
    for _, z in zones.iterrows():
        try:
            x0 = df.index[int(z["left_bar"])]
        except Exception:
            continue
        x1 = last_x + step * 12
        col = "#26a69a" if z["demand"] else "#ef5350"
        fill = "rgba(38,166,154,0.16)" if z["demand"] else "rgba(239,83,80,0.16)"
        fig.add_shape(type="rect", xref="x", yref="y", x0=x0, x1=x1,
                      y0=float(z["dist"]), y1=float(z["prox"]),
                      line=dict(color=col, width=1), fillcolor=fill, layer="below")
        fig.add_annotation(x=x1, y=float(z["prox"]), text=(
            f"{'D' if z['demand'] else 'S'} {z['pattern']} · {z['score']}"),
            showarrow=False, xanchor="left", font=dict(size=9, color=col), bgcolor="rgba(255,255,255,.65)")
        if str(z["state"]) in ("Covered",):
            fig.add_shape(type="line", xref="x", yref="y", x0=x0, x1=x1,
                          y0=float(z["cover"]), y1=float(z["cover"]),
                          line=dict(color="#7b61ff", width=1, dash="dot"))
    fig.update_layout(height=520, margin=dict(l=8, r=8, t=30, b=8),
                      xaxis_rangeslider_visible=False,
                      title=f"{sym} · {tf} ({TF_LABEL_HI.get(tf, tf)}) · ज़ोन",
                      hovermode="x unified", dragmode="pan")
    if TF_SECONDS.get(tf, 0) < 86400:
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]),
                                      dict(bounds=[15.5, 9.25], pattern="hour")])
    return fig


def _zone_chart_altair(df: pd.DataFrame, zones: pd.DataFrame, sym: str, tf: str,
                       sl: float | None, tp: float | None):
    """fallback — वही candle + ज़ोन, बिना plotly।"""
    d = df.tail(400).copy()
    d = d.reset_index()
    tcol = d.columns[0]
    try:
        d[tcol] = (pd.to_datetime(d[tcol]).dt.tz_convert(IST).dt.tz_localize(None))
    except Exception:                                          # noqa: BLE001
        d[tcol] = pd.to_datetime(d[tcol]).dt.tz_localize(None)
    tname = "t"
    d = d.rename(columns={tcol: tname})
    tmin, tmax = d[tname].min(), d[tname].max()
    step = (tmax - tmin) / max(len(d), 1) if len(d) > 1 else pd.Timedelta(minutes=15)
    t_end = tmax + step * 12

    up = "#26a69a"
    dn = "#ef5350"
    color = alt.condition(alt.datum["close"] >= alt.datum["open"],
                          alt.value(up), alt.value(dn))
    wick = (alt.Chart(d).mark_rule(strokeWidth=1)
            .encode(x=alt.X(f"{tname}:T", title=None),
                    y=alt.Y("low:Q", title="मूल्य", scale=alt.Scale(zero=False)),
                    y2="high:Q", color=color))
    body = (alt.Chart(d).mark_bar(size=5)
            .encode(x=f"{tname}:T", y="open:Q", y2="close:Q", color=color,
                    tooltip=[alt.Tooltip(f"{tname}:T", title="समय"),
                             alt.Tooltip("open:Q", format=".2f"),
                             alt.Tooltip("high:Q", format=".2f"),
                             alt.Tooltip("low:Q", format=".2f"),
                             alt.Tooltip("close:Q", format=".2f")]))
    layers = [wick, body]

    zr = []
    for _, z in zones.iterrows():
        try:
            t0 = pd.to_datetime(df.index[int(z["left_bar"])])
            t0 = t0.tz_convert(IST).tz_localize(None)
        except Exception:                                      # noqa: BLE001
            t0 = tmin
        zr.append({tname: t0, "t1": t_end, "y1": float(z["prox"]), "y0": float(z["dist"]),
                   "col": up if z["demand"] else dn,
                   "lbl": f"{'D' if z['demand'] else 'S'} {z['pattern']} · {int(z['score'])}"})
    if zr:
        zdf = pd.DataFrame(zr)
        zinc = (alt.Chart(zdf).mark_rect(opacity=0.16, strokeWidth=1)
                .encode(x=alt.X(f"{tname}:T"), x2=alt.X2("t1:T"),
                        y=alt.Y("y1:Q"), y2=alt.Y2("y0:Q"),
                        color=alt.Color("col:N", scale=None, legend=None),
                        stroke=alt.Color("col:N", scale=None, legend=None)))
        ztxt = (alt.Chart(zdf).mark_text(align="left", dx=3, fontSize=9)
                .encode(x=alt.X(f"{tname}:T"), x2=alt.X2("t1:T"),
                        y=alt.Y("y1:Q"), text="lbl:N",
                        color=alt.Color("col:N", scale=None, legend=None)))
        layers += [zinc, ztxt]

    for lvl, cl, nm in ((sl, "#d62728", "SL"), (tp, "#2ca02c", "T1")):
        if lvl is None:
            continue
        rdf = pd.DataFrame({tname: [tmin], "t1": [t_end], "y": [float(lvl)], "nm": [f"{nm} {float(lvl):.2f}"]})
        rule = (alt.Chart(rdf).mark_rule(strokeDash=[5, 4], color=cl)
                .encode(x=alt.X(f"{tname}:T"), x2=alt.X2("t1:T"), y=alt.Y("y:Q")))
        txt = (alt.Chart(rdf).mark_text(align="left", dx=3, color=cl, fontSize=9)
               .encode(x=alt.X(f"{tname}:T"), x2=alt.X2("t1:T"), y=alt.Y("y:Q"), text="nm:N"))
        layers += [rule, txt]

    return (alt.layer(*layers)
            .properties(height=520,
                        title=f"{sym} · {tf} ({TF_LABEL_HI.get(tf, tf)}) · ज़ोन")
            .interactive())


def mtf_panel(snap: dict, tfs, peers):
    rows = []
    for tf in tfs:
        s = snap.get(tf) or {}
        rows.append({
            "TF": tf,
            "नाम": TF_LABEL_HI.get(tf, tf),
            "मांग proximal": s.get("demand"),
            "मांग बना": fmt_t(s.get("demand_time", 0)) if s.get("demand") else "—",
            "आपूर्ति proximal": s.get("supply"),
            "आपूर्ति बना": fmt_t(s.get("supply_time", 0)) if s.get("supply") else "—",
            "MTF में गिना": "✓" if tf in peers else "",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4 · MAIN
# ---------------------------------------------------------------------------
REQ_TXT = """streamlit
yfinance
pandas
numpy
plotly      # वैकल्पिक — न हो तो Altair चार्ट चलेंगे
requests"""


def deps_guard() -> bool:
    """ज़रूरी पैकेज हैं या नहीं — Streamlit Cloud पर साफ़ संदेश दिखाने के लिए।"""
    missing = []
    if yf is None:
        missing.append("yfinance")
    if not missing:
        return True
    st.error("### ⚠️ ज़रूरी पैकेज इंस्टॉल नहीं हैं: " + ", ".join(missing))
    st.markdown(
        "GitHub repo की जड़ (root) में **`requirements.txt`** खोलें और ये पंक्तियाँ रखें — "
        "फिर Streamlit Cloud में **Manage app → Reboot app**:")
    st.code(REQ_TXT, language="text")
    st.caption("plotly वैकल्पिक है — न हो तो ज़ोन चार्ट Altair में बनते हैं।")
    return False


def main():
    css()
    if not deps_guard():
        return
    inp, opts = sidebar()

    st.markdown("""
    <div class="hero">
      <h1>📐 ज़ोन MTF स्कैनर — KTP नियम (Pine v6)</h1>
      <p>1-2-4 ढाँचा · CBLI · Formation · White-area · Covered leg-out · Maturity · DATR बफ़र ·
      XRPT · Quality score · MTF confirmation — और स्कैन <b>सभी 13 time frame</b> पर, आपकी दी हुई range में।</p>
    </div>""", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.1, 1, 2])
    with c1:
        go_scan = st.button("🔍 स्कैन शुरू करें", type="primary", width="stretch")
    with c2:
        if st.button("♻️ डेटा रीफ़्रेश", width="stretch"):
            clear_cache()
            st.session_state.pop("scan", None)
            st.rerun()
    with c3:
        mo = market_open_now()
        st.caption(("🟢 बाज़ार खुला है · " if mo else "🔴 बाज़ार बंद है · ")
                   + datetime.now(IST).strftime("%d %b %Y, %H:%M IST"))

    if go_scan:
        syms = resolve_universe(opts["universe"], opts["custom"])
        lim = int(opts.get("max_syms") or 0)
        if lim and len(syms) > lim:
            st.caption(f"पहली {lim} स्क्रिप्ट स्कैन होंगी (कुल {len(syms)})।")
            syms = syms[:lim]
        if not syms:
            st.error("कोई स्क्रिप्ट नहीं चुनी गई।")
            st.stop()
        ph = st.empty()
        pbar = st.progress(0.0, text="डेटा लाया जा रहा है…")
        with ph.container():
            st.info(f"{len(syms)} स्क्रिप्ट × {len(opts['tfs'])} time frame स्कैन हो रहा है…")
        res = run_scan(syms, inp, opts, ph, pbar)
        pbar.empty()
        ph.empty()
        res["inputs"] = inp
        res["opts"] = opts
        res["symbols"] = syms
        st.session_state["scan"] = res
        st.toast(f"स्कैन पूरा — {len(res['rows'])} ज़ोन", icon="✅")

    res = st.session_state.get("scan")
    if not res:
        st.info("⬅️ बाईं ओर सेटिंग देखें और **स्कैन शुरू करें** दबाएँ। "
                "स्कैन में सभी 13 time frame एक साथ देखे जाते हैं।")
        with st.expander("Pine v6 नियम — संक्षेप में"):
            st.markdown(PINE_HELP)
        return

    df_all: pd.DataFrame = res["rows"]
    opt_s = res["opts"]
    inp_s: PineInputs = res["inputs"]
    syms = res["symbols"]

    # ---- market cap (display only, Pine minMcapCr) ----
    if opt_s.get("show_mcap") and not df_all.empty:
        try:
            caps = market_caps(sorted(df_all["symbol"].unique()))
            df_all["mcap_cr"] = df_all["symbol"].map(caps)
        except Exception:                                        # noqa: BLE001
            df_all["mcap_cr"] = np.nan

    # ---- filters (UI, बिना दोबारा स्कैन) ----
    if df_all.empty:
        st.warning("इस रन में कोई ज़ोन नहीं बना। स्कैन रेंज बढ़ाएँ या नियम ढीले करें "
                   "(minBaseCount / legOutToBaseSizeMult / minValidScore)।")
        return

    f = df_all.copy()
    f = f[f["dist_pct"] <= opt_s["range_pct"]]
    f = f[f["score"] >= opt_s["min_score"]]
    if opt_s["states"]:
        f = f[f["state"].isin(opt_s["states"])]
    if opt_s["only_mtf"]:
        f = f[f["mtf"]]
    if opt_s["only_hq"]:
        f = f[f["hq"]]

    # ---- KPI ----
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("स्कैन हुई स्क्रिप्ट", res["scanned"])
    m2.metric("कुल ज़ोन (13 TF)", len(df_all))
    m3.metric("रेंज + फ़िल्टर में", len(f))
    m4.metric("ताज़ा + सक्रिय", int(f["state"].isin(["Fresh", "Triggered"]).sum()))
    m5.metric("हाई-क्वालिटी ★", int(f["hq"].sum()))
    st.caption(f"रेंज ±{opt_s['range_pct']:.1f}% · स्कैन समय {res['at']:%d-%b %H:%M:%S} IST · "
               f"{len(opt_s['tfs'])} TF · रन में त्रुटि: {len(res['errs'])}")

    if res["errs"]:
        with st.expander(f"⚠️ {len(res['errs'])} स्क्रिप्ट में समस्या"):
            st.write(pd.DataFrame({"स्क्रिप्ट": list(res["errs"]), "कारण": list(res["errs"].values())}))

    t1, t2, t3, t4 = st.tabs(["📋 स्कैन परिणाम", "🕯️ ज़ोन डिटेल + चार्ट",
                              "🔗 MTF तालिका", "📖 नियम व सेटिंग"])

    # ---------------- TAB 1 ----------------
    with t1:
        if f.empty:
            st.warning("इन फ़िल्टर पर कोई ज़ोन नहीं। फ़िल्टर ढीले करें।")
        else:
            tbl = zone_table(f, opt_s.get("show_mcap", True), opt_s.get("min_mcap", 50000.0))

            # ── Google Sheets जैसा दृश्य (symbol/TF पर TradingView लिंक) ──
            st.markdown(
                f'<div class="gs-bar"><b>📄 शीट दृश्य</b> · {len(f)} पंक्तियाँ · '
                f'<b>स्क्रिप्ट / TF / 📈</b> पर क्लिक → TradingView उसी टाइम फ्रेम पर खुलेगा '
                f'(NSE:स्क्रिप्ट · interval = ज़ोन का TF)</div>',
                unsafe_allow_html=True)
            st.markdown(sheet_html(f, opt_s.get("show_mcap", True),
                                   opt_s.get("min_mcap", 50000.0)),
                        unsafe_allow_html=True)

            c_dl1, c_dl2, c_dl3 = st.columns([1.1, 1.1, 2.2])
            with c_dl1:
                dl = tbl.to_csv(index=False).encode("utf-8-sig")
                st.download_button("⬇️ CSV (Sheets में Import)", dl,
                                   file_name=f"zone_mtf_{datetime.now(IST):%Y%m%d_%H%M}.csv",
                                   mime="text/csv", width="stretch")
            with c_dl2:
                st.download_button("⬇️ TSV (सीधे पेस्ट)",
                                   tbl.to_csv(index=False, sep="\t").encode("utf-8-sig"),
                                   file_name=f"zone_mtf_{datetime.now(IST):%Y%m%d_%H%M}.tsv",
                                   mime="text/tab-separated-values", width="stretch")
            with c_dl3:
                st.caption("Google Sheets → File → Import → Upload (या TSV कॉपी कर सीधे पेस्ट)। "
                           "TradingView पर 10m/75m/6H जैसा custom TF न हो तो निकटतम TF खुलेगा।")

            # ── इंटरैक्टिव तालिका — सॉर्ट + पंक्ति क्लिक → ज़ोन डिटेल ──
            with st.expander("🔎 फ़िल्टर/सॉर्ट करने योग्य तालिका — पंक्ति क्लिक → ज़ोन डिटेल", expanded=False):
                tbl_x = tbl.copy()
                tbl_x.insert(1, "चार्ट", [tv_url(sy, tf) for sy, tf in zip(f["symbol"], f["tf"])])
                sel = stretch(st.dataframe, tbl_x, hide_index=True, height=460,
                        on_select="rerun", selection_mode="single-row", key="res_table",
                        column_config={
                            "चार्ट": st.column_config.LinkColumn("चार्ट", display_text="📈 खोलें"),
                            "स्कोर": st.column_config.ProgressColumn(
                                "स्कोर", min_value=0, max_value=150, format="%d"),
                            "दूरी %": st.column_config.NumberColumn("दूरी %", format="%.2f%%"),
                            "एंट्री (proximal)": st.column_config.NumberColumn(format="%.2f"),
                            "डिस्टल": st.column_config.NumberColumn(format="%.2f"),
                            "SL": st.column_config.NumberColumn(format="%.2f"),
                            "T1": st.column_config.NumberColumn(format="%.2f"),
                        })
                try:
                    picked = sel.selection.rows if sel is not None else []
                except Exception:                                # noqa: BLE001
                    picked = []
                if picked:
                    pr = f.iloc[picked[0]]
                    pk = {"symbol": str(pr["symbol"]), "tf": str(pr["tf"]), "bar": int(pr["bar"])}
                    if st.session_state.get("_pick") != pk:
                        st.session_state["_pick"] = pk
                        st.session_state["d_sym"] = pk["symbol"]
                        st.session_state["d_tf"] = pk["tf"]
                        st.session_state.pop("d_zone", None)
                st.caption("किसी पंक्ति पर क्लिक करें → 'ज़ोन डिटेल + चार्ट' टैब में वही ज़ोन खुल जाएगा।")

            # ---- quick summary by TF / side ----
            ssum = (f.groupby(["tf", "side"], observed=True)
                     .agg(ज़ोन=("symbol", "count"), औसत_स्कोर=("score", "mean"))
                     .reset_index())
            if not ssum.empty:
                with st.expander("TF-वार सारांश"):
                    piv = ssum.pivot(index="tf", columns="side", values="ज़ोन").fillna(0).astype(int)
                    piv = piv.reindex([t for t in TFS_ALL if t in piv.index])
                    piv.columns = [SIDE_HI.get(c, c) for c in piv.columns]
                    stretch(st.dataframe, piv, column_config={
                        c: st.column_config.NumberColumn(c, format="%d") for c in piv.columns})

    # ---------------- TAB 2 ----------------
    with t2:
        if f.empty:
            st.info("पहले फ़िल्टर में कुछ ज़ोन आने दें।")
        else:
            cc1, cc2, cc3 = st.columns([1, 1, 2])
            sym_opts = sorted(f["symbol"].unique())
            if st.session_state.get("d_sym") not in sym_opts:
                st.session_state.pop("d_sym", None)
            sym_pick = cc1.selectbox("स्क्रिप्ट", sym_opts, key="d_sym")
            sub = f[f["symbol"] == sym_pick]
            tf_opts = list(sub["tf"].unique())
            if st.session_state.get("d_tf") not in tf_opts:
                st.session_state.pop("d_tf", None)
            tf_pick = cc2.selectbox("टाइम फ़्रेम", tf_opts, key="d_tf",
                                    format_func=lambda t: f"{t} · {TF_LABEL_HI.get(t, t)}")
            zsub = sub[sub["tf"] == tf_pick].reset_index(drop=True)
            pk = st.session_state.get("_pick") or {}
            zi = 0
            if pk.get("symbol") == sym_pick and pk.get("tf") == tf_pick and "bar" in zsub:
                hits = zsub.index[zsub["bar"] == pk["bar"]].tolist()
                zi = hits[0] if hits else 0
            if "d_zone" in st.session_state and int(st.session_state["d_zone"]) >= len(zsub):
                st.session_state.pop("d_zone", None)
            idx = cc3.selectbox(
                "ज़ोन", range(len(zsub)), index=zi, key="d_zone",
                format_func=lambda i: (
                    f"{'मांग' if zsub.loc[i,'demand'] else 'आपूर्ति'} · {zsub.loc[i,'pattern']} · "
                    f"एंट्री {zsub.loc[i,'prox']:.2f} · स्कोर {int(zsub.loc[i,'score'])} · "
                    f"{STATE_HI.get(zsub.loc[i,'state'], zsub.loc[i,'state'])}"))
            zrow = zsub.loc[idx]

            k1, k2, k3, k4, k5, k6 = st.columns(6)
            k1.metric("एंट्री (proximal)", f"{zrow['prox']:.2f}")
            k2.metric("SL", f"{zrow['sl']:.2f}")
            k3.metric(f"T1 (1:{inp_s.targetRR:g})", f"{zrow['tp']:.2f}")
            k4.metric("XRPT 1:R", f"{zrow['xrpt_rr']:.2f}")
            k5.metric("मात्रा", f"{int(zrow['qty'])}")
            k6.metric("स्कोर", f"{int(zrow['score'])}" + (" ★HQ" if zrow["hq"] else ""))

            st.markdown(
                f"<span class='pill {'p-d' if zrow['demand'] else 'p-s'}'>"
                f"{SIDE_HI.get(zrow['side'], zrow['side'])}</span> "
                f"<span class='pill p-n'>{zrow['pattern']} · {zrow['cat_hi']} · "
                f"{int(zrow['base_count'])} boring</span> "
                f"<span class='pill p-n'>{STATE_HI.get(zrow['state'], zrow['state'])}</span> "
                f"<span class='pill p-n'>MTF {'✓' if zrow['mtf'] else '–'}</span> "
                f"<span class='pill p-n'>बना {zrow['created_ist']}</span>"
                + ("<span class='pill p-n'>BASE BREAK</span>" if zrow.get("base_break") else "")
                + ("<span class='pill p-n'>SWING</span>" if zrow.get("swing_break") else ""),
                unsafe_allow_html=True)

            st.link_button(f"📈 TradingView पर खोलें — NSE:{zrow['symbol']} · {zrow['tf']}",
                           tv_url(zrow["symbol"], zrow["tf"]), type="primary")

            # chart
            try:
                frames = load_frames([sym_pick], [tf_pick])
                df_tf = frames.get(sym_pick, {}).get(tf_pick)
            except Exception as e:                                # noqa: BLE001
                df_tf = None
                st.error(f"चार्ट डेटा नहीं मिला: {e}")
            if df_tf is not None and len(df_tf):
                zc = zsub[zsub["state"].isin(["Fresh", "Triggered", "Covered", "Immature"])]
                fig = zone_chart(df_tf, zc, sym_pick, tf_pick, inp_s,
                                 sl=float(zrow["sl"]), tp=float(zrow["tp"]))
                if HAS_PLOTLY:
                    stretch(st.plotly_chart, fig)
                else:
                    stretch(st.altair_chart, fig)
            if inp_s.showInfo:
                st.code(info_text(zrow.to_dict(), inp_s.targetRR), language="text")

            with st.expander("इस स्क्रिप्ट के सभी ज़ोन (सभी TF)"):
                allsub = df_all[df_all["symbol"] == sym_pick]
                stretch(st.dataframe,
                        zone_table(allsub, opt_s.get("show_mcap", True), opt_s.get("min_mcap", 50000)),
                        hide_index=True, height=320)

    # ---------------- TAB 3 ----------------
    with t3:
        st.caption("हर TF का नवीनतम मांग/आपूर्ति proximal — Pine के MTF snapshot जैसा "
                   "(सिर्फ़ Fresh व Triggered ज़ोन गिने जाते हैं)।")
        if f.empty:
            sym2 = sorted(res["snaps"].keys())[0] if res["snaps"] else None
        else:
            sym2 = st.selectbox("स्क्रिप्ट", sorted(res["snaps"].keys()), key="m_sym")
        if sym2:
            mdf = mtf_panel(res["snaps"].get(sym2, {}), list(opt_s["tfs"]), set(opt_s["peers"]))
            ltp = res["ltp"].get(sym2)
            if ltp:
                mdf["LTP से %"] = mdf["मांग proximal"].map(
                    lambda v: None if pd.isna(v) else round(abs(v - ltp) / ltp * 100, 2))
            stretch(st.dataframe, mdf, hide_index=True, height=520,
                    column_config={
                        "मांग proximal": st.column_config.NumberColumn(format="%.2f"),
                        "आपूर्ति proximal": st.column_config.NumberColumn(format="%.2f"),
                        "LTP से %": st.column_config.NumberColumn(format="%.2f%%"),
                    })
            mc = f[f["symbol"] == sym2]
            if not mc.empty:
                st.caption(f"{sym2} · MTF confirmed ज़ोन: {int(mc['mtf'].sum())}/{len(mc)} · "
                           f"ताज़ा: {int(mc['state'].eq('Fresh').sum())}")

    # ---------------- TAB 4 ----------------
    with t4:
        c1, c2 = st.columns([1, 1])
        with c1:
            st.markdown("#### इस रन के इनपुट (Pine v6)")
            st.json({k: (v if not isinstance(v, float) else round(v, 4))
                     for k, v in inp_s.__dict__.items()})
        with c2:
            st.markdown("#### Pine नियम — संक्षेप में")
            st.markdown(PINE_HELP)

    engine = "Plotly" if HAS_PLOTLY else ("Altair (fallback)" if HAS_ALTAIR else "कोई नहीं")
    st.caption(f"डेटा: Yahoo Finance (15-20 मिनट देरी संभव) · नियम: Pine v6 “Zone MTF (KTP rules)” "
               f"© iincomesense · चार्ट इंजन: {engine} · यह शिक्षा हेतु है, निवेश सलाह नहीं।")


PINE_HELP = """
**1-2-4 ढाँचा (Zone)**
- **Leg-in** ज़ोरदार candle: body ≥ 50%, closing-side wick ≤ 20%, TR ≥ 1.0 × ATR
- **Boring base**: 1–4 candles, हर TR ≤ 1.0 × ATR, कुल रेंज leg-in से ≥ 1/2
- **Leg-out**: TR ≥ 1.2 × ATR, wick ≤ 30%, 1–3 समान रंग की candles (join सहनशीलता 0.25 × ATR)
- रेंज: leg-in ≥ 2 × base, leg-out ≥ 4 × base
- **CBLI**: leg-in के पीछे का उल्टे रंग का candle leg-in body के 50% से बड़ा न हो
- **Formation**: पलटाव पैटर्न (DBR/RBD) में leg-out का open leg-in body के अंदर (±0.10 ATR)
- **White area**: leg-out body boring body को निगल न ले
- **Maturity**: hourly TF = 75 मिनट, daily = अगला दिन, weekly = +7, monthly = +21 ट्रेडिंग दिन
- **Score**: 10 से शुरू, base/leg-in/leg-out/gap/volume पर अंक, < 40 = अमान्य, ≥ 90 = HQ ★

**स्तर**
- **Proximal** = base का high (मांग) / low (आपूर्ति) — एंट्री
- **Distal** = course विधि में pattern का सबसे नीचा/ऊँचा, वरना सिर्फ़ boring
- **SL** = distal ∓ DATR बफ़र (पिछले पूरे दिन का ATR14; बफ़र % ट्रेडर प्रकार से)
- **T1** = proximal ± जोखिम × targetRR · **XRPT** = पहले leg-out candle का (open+close)/2

**स्थितियाँ**: Fresh → Triggered (ज़ोन में घुसा + matured) / Immature / Covered (body leg-out के 50% से आगे) /
Broken (SL पार) / Exhausted (maxTouches से ज़्यादा बार)

**MTF**: पड़ोसी TF का same-side proximal इस ज़ोन के ± 0.5 × ATR के अंदर हो
(TF अनुपात 0.2× से 5× के बीच)।
"""

main()
