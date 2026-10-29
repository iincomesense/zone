#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  ZONE MTF PRO TERMINAL  ·  single-file build  (v8.0.0)
  Pine Script v6  »  "Zone MTF (KTP rules)" v24  © iincomesense
───────────────────────────────────────────────────────────────────────────────
  Dr. Ravi R. Kumar (KTP / Super Trader) कोर्स के नियम — Pine v24 जैसे ही:
  Leg-in (filled, TR ≥ ATR, closing wick छोटी) · Boring 1–3 (TR ≤ ATR) ·
  Leg-out 1–3 (पहली: TR ≥ 1.2 ATR, wick ≤ 30%) · 1-2-4 · CBLI · Formation
  (DBR/RBD: leg-out open leg-in body के अंदर, tolerance नहीं) · White area
  (leg-out body boring की पूरी wick न ढके) · Volume / Imbalance (optional)।
  Quality score 0–100 सिर्फ़ रैंकिंग · ★ HQ · Max entries → Exhausted ·
  Confirmed swing 6/6 (NSE अध्ययन) · BASE BREAK / SWING निशान।
  स्कैन सभी 13 time frame पर होता है।

  यह एक ही फ़ाइल है: GitHub पर रखें और Streamlit Cloud / `streamlit run app.py` से चलाएँ।
  pip: streamlit · yfinance · pandas · numpy · requests
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════
#  भाग 1 · आयात (imports)
# ═══════════════════════════════════════════════════════════════════════
import hashlib
import io
import json
import math
import os
import pickle
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
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
    "1m": ("1m", "7d"),          # Yahoo: 1m sirf 7 din
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "60m": ("60m", "1y"),         # ट्रेडिंग स्कैन: लंबा इतिहास नहीं चाहिए
    "1d": ("1d", "2y"),
    "1wk": ("1wk", "5y"),
    "1mo": ("1mo", "10y"),
}

TF_FETCH: Dict[str, Tuple[str, Optional[str]]] = {
    "3m": ("1m", "3min"),
    "5m": ("5m", None),
    "10m": ("5m", "10min"),
    "15m": ("15m", None),
    "30m": ("30m", "30min"),
    "75m": ("15m", "75min"),
    "1h": ("60m", "60min"),
    "2h": ("60m", "120min"),
    "4h": ("60m", "240min"),
    "6h": ("60m", "360min"),
    "1d": ("1d", None),
    "1w": ("1wk", None),
    "1M": ("1mo", None),
}

TTL = {"1m": 600, "5m": 900, "15m": 1800, "30m": 2700, "60m": 3600,
       "1d": 14400, "1wk": 43200, "1mo": 86400}


def _bar_ttl(base: str) -> float:
    """डेटा कब तक ताज़ा माना जाए — बड़ा TF, लंबा कैश (बार-बार डाउनलोड नहीं)।"""
    return float(TTL.get(base, 1800))

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

# ───────────────────────────────────────────────────────────────────────────
# GLOBAL INSTRUMENTS — (दिखने का नाम, Yahoo ticker, TradingView cash, TV future)
# ───────────────────────────────────────────────────────────────────────────
GLOBAL_INSTRUMENTS = [
    # (दिखने का नाम, Yahoo ticker, TradingView symbol)
    ("DXY",        "DX-Y.NYB",  "TVC:DXY"),
    ("USDINR",     "INR=X",     "FX_IDC:USDINR"),
    ("TLT",        "TLT",       "AMEX:TLT"),
    ("US10Y",      "^TNX",      "TVC:US10Y"),
    ("XAUUSD",     "GC=F",      "OANDA:XAUUSD"),
    ("XAGUSD",     "SI=F",      "OANDA:XAGUSD"),
    ("SPOTCRUDE",  "CL=F",      "TVC:USOIL"),
    ("US30",       "^DJI",      "TVC:DJI"),
    ("US500",      "^GSPC",     "TVC:SPX"),
    ("000001",     "000001.SS", "SSE:000001"),
    ("XIN9",       "2823.HK",   "SGX:CN1!"),
    ("JP225",      "^N225",     "TVC:NI225"),
    ("NIFTY1!",    "^NSEI",     "NSE:NIFTY1!"),
    ("FTSE100",    "^FTSE",     "TVC:UKX"),
    ("DAX",        "^GDAXI",    "XETR:DAX"),
    ("Copper",     "HG=F",      "COMEX:HG1!"),
    ("Aluminium",  "ALI=F",     "COMEX:ALI1!"),
    ("Zinc",       "ZNC=F",     "COMEX:ZNC1!"),
    ("NatGas",     "NG=F",      "NYMEX:NG1!"),
    ("GBYUSD",     "GBPUSD=X",  "FX:GBPUSD"),
    ("UROUSD",     "EURUSD=X",  "FX:EURUSD"),
    ("JPYUSD",     "JPYUSD=X",  "FX:JPYUSD"),
    ("JPYINR",     "JPYINR=X",  "FX_IDC:JPYINR"),
    ("GBPINR",     "GBPINR=X",  "FX_IDC:GBPINR"),
    ("Bitcoin",    "BTC-USD",   "BITSTAMP:BTCUSD"),
]
GLOBAL_MAP = {name.upper(): dict(name=name, yahoo=y, cash=tv, fut=None)
              for name, y, tv in GLOBAL_INSTRUMENTS}
GLOBAL_SYMBOLS = [g[0] for g in GLOBAL_INSTRUMENTS]


def sym_meta(sym: str) -> dict:
    """display symbol → {name, yahoo, cash, fut}"""
    up = str(sym).upper()
    m = GLOBAL_MAP.get(up)
    if m:
        return m
    if up in IDX_SYMBOLS:                       # सूचकांक (cash index + futures दोनों)
        return dict(name=sym, yahoo=IDX_YAHOO.get(up, "^NSEI"),
                    cash=IDX_TV.get(up), fut=IDX_TV.get(up))
    return dict(name=sym, yahoo=f"{sym}.NS", cash=f"NSE:{sym}", fut=f"NSE:{sym}1!")


def _ticker(sym: str) -> str:
    """display symbol → Yahoo ticker (global के लिए जैसा है वैसा, stocks के लिए .NS)"""
    return sym_meta(sym)["yahoo"]


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
IDX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
IDX_YAHOO = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
             "MIDCPNIFTY": "^NSEMDCP50", "NIFTYNXT50": "^NSENXT50"}
IDX_TV = {"NIFTY": "NSE:NIFTY1!", "BANKNIFTY": "NSE:BANKNIFTY1!", "FINNIFTY": "NSE:FINNIFTY1!",
          "MIDCPNIFTY": "NSE:MIDCPNIFTY1!", "NIFTYNXT50": "NSE:NIFTYNXT501!"}


def live_fno_universe(timeout: int = 20) -> List[str]:
    """NSE की **FUTURES** सूची — fo_mktlots.csv से (option-only नहीं)।

    Pine/ट्रेडिंगव्यू के futures सिंबल्स (NSE:JSWSTEEL1! आदि) इन्हीं के लिए बनते हैं।
    """
    key = _ckey("fno_fut_universe_v2")
    cached = _load(key, 6 * 3600)
    if cached:
        return cached
    out: List[str] = []
    # 1) NSE archives — फ्यूचर्स का lot-size फ़ाइल = पूरी futures सूची
    try:
        r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
                         headers={"User-Agent": _UA, "Accept": "*/*"}, timeout=timeout)
        if r.ok and len(r.content) > 500:
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [str(c).strip() for c in df.columns]
            col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[1]
            syms = (df[col].astype(str).str.strip().str.upper()
                    .replace({"NAN": "", "SYMBOL": ""}))
            out = sorted({s for s in syms if s and s.isascii()})
    except Exception:                                          # noqa: BLE001
        out = []
    # 2) वैकल्पिक: NSE का master-quote (जिसमें सिर्फ़ F&O अंडरलाइंग होते हैं)
    if not out:
        try:
            s2 = requests.Session()
            h = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"}
            s2.get("https://www.nseindia.com/", headers=h, timeout=timeout)
            r2 = s2.get("https://www.nseindia.com/api/master-quote",
                        headers={**h, "Referer": "https://www.nseindia.com/"}, timeout=timeout)
            if r2.status_code == 200:
                data = r2.json()
                if isinstance(data, list):
                    out = sorted({str(x).strip().upper() for x in data if str(x).strip()})
        except Exception:                                      # noqa: BLE001
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
            fi = yf.Ticker(_ticker(sym)).fast_info
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
def _flatten(df: pd.DataFrame, tick: str) -> Optional[pd.DataFrame]:
    """Yahoo के bulk फ्रेम से एक ticker का हिस्सा निकालो।"""
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # group_by='ticker' -> (Ticker, Price) ; warna (Price, Ticker)
        lvl0 = df.columns.get_level_values(0)
        lvl1 = df.columns.get_level_values(1)
        if tick in lvl0:
            sub = df[tick]
        elif tick in lvl1:
            sub = df.xs(tick, axis=1, level=1)
        else:
            return None
        if isinstance(sub.columns, pd.MultiIndex):
            sub = sub.droplevel(0, axis=1)
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
    """दिन के पहले bar से anchored resample (session के अंदर ही) — vectorized।

    पहले हर दिन के लिए अलग `resample()` चलता था (बड़े इतिहास पर बहुत धीमा);
    अब एक ही groupby — नतीजा बिल्कुल वही (O/H/L/C/V बराबर)।
    """
    if df is None or df.empty:
        return df
    try:
        secs_rule = int(pd.Timedelta(rule).total_seconds())
    except Exception:                                          # noqa: BLE001
        return df
    if secs_rule <= 0:
        return df
    idx = pd.DatetimeIndex(df.index)
    wall = idx.tz_localize(None) if idx.tz is not None else idx
    day = wall.normalize()
    secs = np.asarray(pd.TimedeltaIndex(wall - day).total_seconds(), dtype="float64")
    anchor = pd.Series(secs).groupby(day).transform("min").to_numpy()
    bucket = ((secs - anchor) // secs_rule).astype("int64")
    # दिन की आख़िरी छोटी candle (जैसे NSE में 15:15–15:30) को पिछली candle में मिलाओ —
    # वरना वह "boring" बनकर नकली ज़ोन बनाती है (1h, 2h, 6h पर)
    if len(secs) > 1:
        step = float(np.median(np.diff(np.unique(secs)))) if len(np.unique(secs)) > 1 else 0.0
        dser = pd.Series(secs).groupby(day)
        last_s = dser.transform("max").to_numpy()
        close_s = np.minimum(last_s + max(step, 60.0), np.where(last_s < 55800, 55800, last_s + max(step, 60.0)))
        last_b = pd.Series(bucket).groupby(day).transform("max").to_numpy()
        b_start = anchor + last_b * secs_rule
        stub = (bucket == last_b) & (last_b > 0) & ((close_s - b_start) <= 0.5 * secs_rule)
        bucket = np.where(stub, bucket - 1, bucket)
    g = df.groupby([day.to_numpy(), bucket], sort=True).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"})
    g = g.dropna(subset=["open", "high", "low", "close"])
    if g.empty:
        return pd.DataFrame(columns=df.columns)
    days = pd.DatetimeIndex(g.index.get_level_values(0))
    bks = g.index.get_level_values(1).to_numpy()
    anchors = pd.Series(secs).groupby(day).min()
    anc = anchors.reindex(days).to_numpy()
    lbl = days + pd.to_timedelta(anc + bks * secs_rule, unit="s")
    if idx.tz is not None:
        lbl = lbl.tz_localize(idx.tz)
    g.index = pd.DatetimeIndex(lbl)
    return g.sort_index()


def _download_base(symbols: Sequence[str], base: str) -> Dict[str, pd.DataFrame]:
    """Ek base interval ke liye bulk download (yahi sabse tez raasta hai)."""
    interval, period = BASE_SPEC[base]
    key = _ckey("dl", base, period, tuple(sorted(_ticker(x) for x in symbols)))
    cached = _load(key, _bar_ttl(base))
    if cached is not None:
        return cached
    if yf is None:
        return {}
    tick = {s: _ticker(s) for s in symbols}
    tickers = list(dict.fromkeys(tick.values()))
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
            f = _flatten(raw, tick[s])
            if f is not None and len(f):
                out[s] = f
    # jo symbol bulk me nahi aaya use alag se try karo
    missing = [s for s in symbols if s not in out]
    if missing:
        def one(sym):
            try:
                d = yf.download(tick[sym], interval=interval, period=period,
                                progress=False, auto_adjust=False, threads=False)
                return sym, _flatten(d, tick[sym])
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
    # base intervals समानांतर (IO-bound) — पहला स्कैन काफ़ी तेज़ हो जाता है
    if len(bases) > 1:
        with ThreadPoolExecutor(max_workers=min(6, len(bases))) as ex:
            futs = {ex.submit(_download_base, symbols, b): b for b in bases}
            for i, fu in enumerate(as_completed(futs)):
                b = futs[fu]
                try:
                    raw[b] = fu.result()
                except Exception:                              # noqa: BLE001
                    raw[b] = {}
                if progress:
                    progress(i, len(bases), b)
    else:
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



# ═══════════════════════════════════════════════════════════════════════
#  भाग 2b · Dhan API — current-month FUTURE candles (Cash + Future = "C+F")
#  Streamlit → Manage app → Settings → Secrets में डालें:
#      DHAN_CLIENT_ID = "1000xxxxxx"
#      DHAN_ACCESS_TOKEN = "eyJ..."
#  (सिर्फ़ डेटा पढ़ा जाता है — कोई order API इस्तेमाल नहीं होता)
# ═══════════════════════════════════════════════════════════════════════
DHAN_BASE = "https://api.dhan.co/v2"
COMMODITY_SYMBOLS = {"XAUUSD", "XAGUSD", "SPOTCRUDE", "COPPER", "ALUMINIUM", "ZINC", "NATGAS"}
_DHAN_STATUS: Dict[str, str] = {"status": "not configured"}


def dhan_creds() -> Tuple[str, str]:
    cid = tok = ""
    try:
        import streamlit as _st
        cid = str(_st.secrets.get("DHAN_CLIENT_ID", "") or "")
        tok = str(_st.secrets.get("DHAN_ACCESS_TOKEN", "") or "")
    except Exception:                                          # noqa: BLE001
        pass
    cid = cid or os.environ.get("DHAN_CLIENT_ID", "")
    tok = tok or os.environ.get("DHAN_ACCESS_TOKEN", "")
    return cid.strip(), tok.strip()


def _dhan_future_ids(symbols: Sequence[str]) -> Dict[str, str]:
    """symbol → current-month FUTSTK security id (Dhan scrip master से)।"""
    key = _ckey("dhan_fut_ids", datetime.now(IST).date().isoformat(), tuple(sorted(symbols)))
    cached = _load(key, 12 * 3600)
    if cached is not None:
        return cached
    try:
        from scanner.dhan import build_security_map, load_instrument_master
        _, fut = build_security_map(load_instrument_master(), list(symbols))
        out = {s: v[0] for s, v in fut.items()}
    except Exception as e:                                     # noqa: BLE001
        _DHAN_STATUS["status"] = f"scrip master error: {type(e).__name__}"
        return {}
    _save(key, out)
    return out


def _dhan_post(endpoint: str, payload: dict, cid: str, tok: str) -> Optional[pd.DataFrame]:
    headers = {"access-token": tok, "client-id": cid, "Accept": "application/json",
               "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(DHAN_BASE + endpoint, json=payload, headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                _DHAN_STATUS["status"] = "token गलत या पुराना (Dhan में नया token बनाएँ)"
                return None
            r.raise_for_status()
            js = r.json()
            data = js.get("data", js) if isinstance(js, dict) else {}
            ts = data.get("timestamp") or []
            if not ts:
                return None
            df = pd.DataFrame({k: data.get(k) for k in ("open", "high", "low", "close", "volume")})
            df.index = pd.to_datetime(pd.Series(ts, dtype="float64"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            df = df.astype("float64").dropna(subset=["open", "high", "low", "close"])
            return df[~df.index.duplicated(keep="last")].sort_index()
        except Exception:                                      # noqa: BLE001
            time.sleep(0.6)
    return None


# Yahoo base interval → (Dhan interval, कितने दिन पीछे)
_DHAN_BASE = {"1m": (1, 5), "5m": (5, 60), "15m": (15, 60), "60m": (60, 365), "1d": ("D", 400)}


def _dhan_base_frame(sec_id: str, base: str, cid: str, tok: str) -> Optional[pd.DataFrame]:
    key = _ckey("dhan_fut", sec_id, base)
    cached = _load(key, _bar_ttl(base))
    if cached is not None:
        return cached
    spec = _DHAN_BASE.get(base)
    if not spec:
        return None
    interval, days = spec
    today = datetime.now(IST).date()
    common = {"securityId": str(sec_id), "exchangeSegment": "NSE_FNO", "instrument": "FUTSTK", "oi": False}
    if interval == "D":
        df = _dhan_post("/charts/historical",
                        dict(common, expiryCode=0, fromDate=(today - timedelta(days=days)).isoformat(),
                             toDate=(today + timedelta(days=1)).isoformat()), cid, tok)
    else:
        parts = []
        start = today - timedelta(days=days)
        while start <= today:                       # Dhan intraday: एक बार में ≤ 90 दिन
            end = min(start + timedelta(days=89), today + timedelta(days=1))
            part = _dhan_post("/charts/intraday",
                              dict(common, interval=interval, fromDate=start.isoformat(),
                                   toDate=end.isoformat()), cid, tok)
            if part is not None and len(part):
                parts.append(part)
            start = end + timedelta(days=1)
            time.sleep(0.25)                       # rate limit के भीतर
        df = pd.concat(parts).sort_index() if parts else None
        if df is not None:
            df = df[~df.index.duplicated(keep="last")]
    if df is not None and len(df):
        _save(key, df)
    return df


def load_future_frames(symbols: Sequence[str], tfs: Sequence[str]) -> Dict[str, Dict[str, pd.DataFrame]]:
    """{symbol: {tf: future DataFrame}} — Dhan secrets न हों तो {}।"""
    cid, tok = dhan_creds()
    if not cid or not tok:
        _DHAN_STATUS["status"] = "not configured"
        return {}
    syms = [s for s in symbols if s.upper() not in GLOBAL_MAP and s.upper() not in IDX_SYMBOLS]
    ids = _dhan_future_ids(syms)
    if not ids:
        _DHAN_STATUS.setdefault("status", "no futures mapped")
        return {}
    bases = sorted({TF_FETCH[tf][0] for tf in tfs if tf in TF_FETCH and TF_FETCH[tf][0] in _DHAN_BASE})
    out: Dict[str, Dict[str, pd.DataFrame]] = {}

    def one(sym):
        per = {}
        raw = {b: _dhan_base_frame(ids[sym], b, cid, tok) for b in bases}
        for tf in tfs:
            spec = TF_FETCH.get(tf)
            if not spec or raw.get(spec[0]) is None:
                continue
            df = raw[spec[0]]
            per[tf] = _resample_session(df, spec[1]) if spec[1] else df
        return sym, per

    with ThreadPoolExecutor(max_workers=3) as ex:
        for sym, per in ex.map(one, list(ids)):
            if per:
                out[sym] = per
    _DHAN_STATUS["status"] = f"ok · {len(out)}/{len(ids)} futures" if out else _DHAN_STATUS.get("status", "no data")
    return out


# ═══════════════════════════════════════════════════════════════════════
#  भाग 2c · Dhan API — NSE CASH (equity) intraday का लंबा इतिहास (बैकटेस्ट के लिए)
#   Dhan: 1, 5, 15, 25, 60 मिनट · पिछले 5 साल तक · एक बार में ≤ 90 दिन
# ═══════════════════════════════════════════════════════════════════════
DHAN_EQ_DAYS = {"1m": 90, "5m": 365, "15m": 730, "60m": 730}
DHAN_EQ_INTERVAL = {"1m": 1, "5m": 5, "15m": 15, "60m": 60}


def _dhan_eq_ids(symbols: Sequence[str]) -> Dict[str, str]:
    key = _ckey("dhan_eq_ids", datetime.now(IST).date().isoformat(), tuple(sorted(symbols)))
    cached = _load(key, 12 * 3600)
    if cached is not None:
        return cached
    try:
        from scanner.dhan import build_security_map, load_instrument_master
        eq, _ = build_security_map(load_instrument_master(), list(symbols))
    except Exception as e:                                     # noqa: BLE001
        _DHAN_STATUS["status"] = f"scrip master error: {type(e).__name__}"
        return {}
    if eq:
        _save(key, eq)
    return eq


def _dhan_eq_base(sec_id: str, base: str, cid: str, tok: str, days: Optional[int] = None) -> Optional[pd.DataFrame]:
    days = int(days or DHAN_EQ_DAYS.get(base, 60))
    key = _ckey("dhan_eq", sec_id, base, days, datetime.now(IST).date().isoformat())
    cached = _load(key, 12 * 3600)
    if cached is not None:
        return cached
    interval = DHAN_EQ_INTERVAL.get(base)
    if not interval:
        return None
    today = datetime.now(IST).date()
    common = {"securityId": str(sec_id), "exchangeSegment": "NSE_EQ", "instrument": "EQUITY", "oi": False}
    parts, start = [], today - timedelta(days=days)
    while start <= today:
        end = min(start + timedelta(days=89), today + timedelta(days=1))
        part = _dhan_post("/charts/intraday", dict(common, interval=interval, fromDate=start.isoformat(),
                                                    toDate=end.isoformat()), cid, tok)
        if part is not None and len(part):
            parts.append(part)
        start = end + timedelta(days=1)
        time.sleep(0.22)
    if not parts:
        return None
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df[(df.index.time >= dtime(9, 15)) & (df.index.time <= dtime(15, 29))]
    _save(key, df)
    return df


def load_dhan_equity_frames(symbols: Sequence[str], tfs: Sequence[str],
                            days_map: Optional[Dict[str, int]] = None, progress=None) -> Dict[str, Dict[str, pd.DataFrame]]:
    """{symbol: {tf: cash DataFrame}} Dhan से (छोटे TF का लंबा इतिहास) — secrets न हों तो {}।"""
    cid, tok = dhan_creds()
    if not cid or not tok:
        _DHAN_STATUS["status"] = "not configured"
        return {}
    ids = _dhan_eq_ids([s for s in symbols if s.upper() not in GLOBAL_MAP and s.upper() not in IDX_SYMBOLS])
    bases = sorted({TF_FETCH[tf][0] for tf in tfs if tf in TF_FETCH and TF_FETCH[tf][0] in DHAN_EQ_INTERVAL})
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for i, sym in enumerate(list(ids)):
        raw = {b: _dhan_eq_base(ids[sym], b, cid, tok, (days_map or {}).get(b)) for b in bases}
        per = {}
        for tf in tfs:
            spec = TF_FETCH.get(tf)
            if not spec or raw.get(spec[0]) is None:
                continue
            df = raw[spec[0]]
            per[tf] = _resample_session(df, spec[1]) if spec[1] else df
        if per:
            out[sym] = per
        if progress:
            progress((i + 1) / max(1, len(ids)), sym)
    _DHAN_STATUS["status"] = f"ok · cash {len(out)}/{len(ids)}" if out else _DHAN_STATUS.get("status", "no data")
    return out


# ═══════════════════════════════════════════════════════════════════════
#  भाग 3 · ज़ोन इंजन — Pine v7 "Zone MTF (KTP rules v7)" (© iincomesense) का पोर्ट
# ═══════════════════════════════════════════════════════════════════════

# सभी 13 time frame (Pine tf1…tf9 + ऐप के अतिरिक्त)
ALL_TFS = ["3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M"]
TF_SECONDS = {"3m": 180, "5m": 300, "10m": 600, "15m": 900, "30m": 1800, "75m": 4500,
              "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400,
              "1w": 604800, "1M": 2592000}
TF_LABEL_HI = {"3m": "3 मिनट", "5m": "5 मिनट", "10m": "10 मिनट", "15m": "15 मिनट",
               "30m": "30 मिनट", "75m": "75 मिनट", "1h": "1 घंटा", "2h": "2 घंटे",
               "4h": "4 घंटे", "6h": "6 घंटे", "1d": "1 दिन", "1w": "1 हफ़्ता",
               "1M": "1 महीना"}


def stretch(fn, *a, **k):
    try:
        return fn(*a, width="stretch", **k)
    except TypeError:
        return fn(*a, use_container_width=True, **k)

# ═══════════════════════════════════════════════════════════════════════
#  भाग 3 · ज़ोन इंजन — Pine v6 "Zone MTF (KTP rules)" v24 जैसा ही
#           CBLI → Leg-in → Boring → Leg-out (1–3) → 1-2-4 → Formation →
#           White area → Volume/Imbalance (optional) · Quality score (रैंकिंग) ·
#           Fresh → Tested (entry) → Exhausted (max entries) / Broken (SL)
# ═══════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# 1. INPUTS — Pine के input.* — वही नाम, वही default
# ---------------------------------------------------------------------------
TRADER_TYPES = ["Auto", "Aggressive", "Hourly", "Daily", "Weekly/Swing", "Monthly"]
BUFFER_MODES = ["DATR % (course)", "Chart ATR ×"]
DIST_MODES = ["Pattern lowest/highest (course)", "Boring only"]
MAX_ZONES = 200            # Pine: const int MAX_ZONES (पहले "Max chart zones" input था)
POWER_MIN_RR = 3.0         # Pine: leg-out candles (1–3) के अंदर ही Entry→XRPT ≥ 1:3
PROX_MAX_TOUCH = 1         # Pine: proximal 1 से ज़्यादा बार टच = Break zone
SWING_AREA_ATR = 0.5       # Pine: swing area = swing से उसकी wick तक, कम से कम ½ ATR
POWER_BEST_RR = 5.0        # Pine: leg-out (1–3) के अंदर ही ≥ 1:5 = ⚡ हाइलाइट (सिर्फ़ निशान)


@dataclass
class PineInputs:
    # ---- 0 · Capital · Risk · SL buffer ----
    accountCapital: float = 25000.0
    riskPct: float = 0.5                 # course: 0.5
    targetRR: float = 5.0                # course minimum 1:5
    traderType: str = "Auto"
    bufferMode: str = "Chart ATR ×"
    bufAggrPct: float = 2.0
    bufDailyPct: float = 2.0
    bufSwingPct: float = 2.0
    slBufferAtr: float = 0.3             # 'Chart ATR ×' या DATR न मिले तब
    # ---- 1 · Leg-in / Boring / Leg-out (1-2-4) ----
    atrPeriod: int = 14
    minBaseCountInput: int = 1
    maxBaseCountInput: int = 3           # course: >4 = trap
    boringAtrMult: float = 1.0           # Boring: TR ≤ ATR ×
    legInAtrMult: float = 1.0            # Leg-in: TR ≥ ATR ×
    legInMinBody: float = 0.5            # Leg-in: min body % (Filled = body > wick)
    legInMaxCloseWick: float = 0.2       # Leg-in: max closing-side wick %
    legInToBaseSizeMult: float = 1.6     # 1-2-4: leg-in ≥ boring ×
    legOutToBaseSizeMult: float = 2.6    # 1-2-4: leg-out ≥ boring ×
    maxLegOut: int = 3                   # course: 3
    legOutJoinTolAtr: float = 0.25       # Leg-out 2/3: |open − prev close| ≤ ATR ×
    legOutAtrMult: float = 1.2           # First leg-out: TR ≥ ATR ×
    legOutMaxWick: float = 0.3           # First leg-out: max wick %
    # ---- 2 · Validation rules ----
    useCBLI: bool = True                 # उल्टे रंग की पिछली candle = trap (body बड़ी हो तो)
    cbliMaxBody: float = 0.5             # CBLI: max body vs leg-in body
    useFormation: bool = True            # DBR/RBD: leg-out open, leg-in body के अंदर (tolerance नहीं)
    useWhiteArea: bool = False           # leg-out body, boring की पूरी wick न ढके
    maxEntries: int = 3                  # course: 1–3
    distalMode: str = "Pattern lowest/highest (course)"
    reqVolume: bool = False              # leg-out volume > leg-in volume
    reqImbalance: bool = False           # gap या leg-in के छोर के पार close
    # ---- 3 · Quality score (ranking only) ----
    volSmaPeriod: int = 20
    hqLegOutMult: float = 2.0            # HQ: leg-out TR ≥ leg-in TR ×
    hqLegInAtr: float = 1.1              # HQ: leg-in TR ≥ ATR ×
    bodyHeavyPct: float = 0.6            # Leg-out body-heavy %
    # ---- 4 · Display ----
    showInfo: bool = True
    showMcap: bool = True
    minMcapCr: float = 50000.0
    # ---- 5 · MTF lines ----
    showMTF: bool = True
    filterDistantMTF: bool = True
    mtfNearPct: float = 8.0
    # ---- 6 · Shadows & swings ----
    showShadow: bool = True
    showFlags: bool = True
    shadowAtr: float = 0.08
    swingLeft: int = 6                   # NSE अध्ययन: best N = 6 (tools/swing_study.py)
    swingRight: int = 6
    # ---- app layer (Pine में नहीं — स्कैनर के लिए) ----
    scanBars: int = 500

    def sanitized(self) -> "PineInputs":
        d = asdict(self)
        d["minBaseCountInput"] = int(max(1, min(4, self.minBaseCountInput)))
        d["maxBaseCountInput"] = int(max(1, min(4, self.maxBaseCountInput)))
        d["maxLegOut"] = int(max(1, min(3, self.maxLegOut)))
        d["atrPeriod"] = int(max(1, self.atrPeriod))
        d["volSmaPeriod"] = int(max(1, self.volSmaPeriod))
        d["swingLeft"] = int(max(1, min(30, self.swingLeft)))
        d["swingRight"] = int(max(1, min(30, self.swingRight)))
        d["maxEntries"] = int(max(1, min(10, self.maxEntries)))
        d["scanBars"] = int(max(200, min(self.scanBars, 5000)))
        if d["traderType"] not in TRADER_TYPES:
            d["traderType"] = "Auto"
        if d["bufferMode"] not in BUFFER_MODES:
            d["bufferMode"] = BUFFER_MODES[1]
        if d["distalMode"] not in DIST_MODES:
            d["distalMode"] = DIST_MODES[0]
        return PineInputs(**d)

    # Pine: minBaseCount = max(1, min(minIn, maxIn)); maxBaseCount = max(minBaseCount, maxIn)
    @property
    def minBaseCount(self) -> int:
        return max(1, min(int(self.minBaseCountInput), int(self.maxBaseCountInput)))

    @property
    def maxBaseCount(self) -> int:
        return max(self.minBaseCount, int(self.maxBaseCountInput))


# ---------------------------------------------------------------------------
# 2. छोटे helpers — Pine: ta.rma / ta.sma / ta.tr(true)
# ---------------------------------------------------------------------------
def _rma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n or n < 1:
        return out
    seed = np.nanmean(x[:n])
    out[n - 1] = seed
    a = 1.0 / n
    for i in range(n, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n or n < 1:
        return out
    c = np.concatenate(([0.0], np.cumsum(x)))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Pine का gap-aware TR: max(h-l, |h-c[1]|, |l-c[1]|)"""
    prev = np.concatenate(([np.nan], c[:-1]))
    r = h - l
    r = np.where(np.isnan(prev), r, np.maximum(r, np.maximum(np.abs(h - prev), np.abs(l - prev))))
    return r


def _grade(bc: int) -> str:
    return {1: "Great", 2: "Very good", 3: "Good"}.get(bc, "So-so")


_GRADE_HI = {1: "बेहतरीन", 2: "बहुत अच्छा", 3: "अच्छा", 4: "ठीक-ठाक"}


def tf_type(tf: str) -> int:
    """Pine f_type(): 1 Aggressive · 2 Hourly · 3 Daily · 4 Weekly/Swing · 5 Monthly"""
    s = TF_SECONDS.get(tf, 0)
    return 1 if s <= 60 else 2 if s <= 300 else 3 if s <= 1800 else 4 if s < 86400 else 5


def trader_type(tf: str, I: "PineInputs") -> int:
    if I.traderType in TRADER_TYPES and I.traderType != "Auto":
        return TRADER_TYPES.index(I.traderType)
    return tf_type(tf)


def trend_tf(tf: str) -> str:
    """25× नियम: ज़ोन TF × 25 ≈ ट्रेंड TF (सिर्फ़ जानकारी के लिए — Pulse/Trend कॉलम)"""
    s = TF_SECONDS.get(tf, 0)
    return "15m" if s <= 60 else "75m" if s <= 300 else "1d" if s <= 1800 else "1w" if s < 86400 else "1M"


TT_NAME = {1: "Aggressive", 2: "Hourly", 3: "Daily", 4: "Weekly/Swing", 5: "Monthly"}


def _buf_pct(tt: int, I: "PineInputs") -> float:
    return I.bufAggrPct if tt <= 2 else I.bufDailyPct if tt == 3 else I.bufSwingPct


# ---------------------------------------------------------------------------
# 3. SERIES — एक symbol+TF के arrays (position = absolute bar index)
# ---------------------------------------------------------------------------
class _S:
    def __init__(self, df: pd.DataFrame):
        self.o = df["open"].to_numpy(dtype="float64")
        self.h = df["high"].to_numpy(dtype="float64")
        self.l = df["low"].to_numpy(dtype="float64")
        self.c = df["close"].to_numpy(dtype="float64")
        self.v = (np.nan_to_num(df["volume"].to_numpy(dtype="float64")) if "volume" in df.columns
                  else np.zeros(len(df)))
        idx = pd.DatetimeIndex(df.index)
        self.index = idx
        try:
            if idx.tz is not None:
                wall_idx = idx.tz_convert(IST).tz_localize(None)
                self.t = idx.as_unit("ns").asi8 // 1_000_000
            else:
                wall_idx = idx
                self.t = (idx.as_unit("ns").asi8 - 19800 * 10 ** 9) // 1_000_000   # naive = IST मानें
        except Exception:                                      # noqa: BLE001
            wall_idx = idx
            self.t = idx.as_unit("ns").asi8 // 1_000_000
        wall = pd.DatetimeIndex(wall_idx).as_unit("ns").asi8
        self.day_id = wall // 86_400_000_000_000
        self.n = len(df)

    def prepare(self, I: PineInputs, daily: Optional[pd.DataFrame] = None) -> None:
        o, h, l, c = self.o, self.h, self.l, self.c
        self.tr = _true_range(h, l, c)
        self.atr = _rma(self.tr, I.atrPeriod)
        self.vol_sma = _sma(self.v, I.volSmaPeriod)
        self.bull = c > o
        self.bear = c < o
        fin = np.isfinite(self.atr)
        rng = h - l
        with np.errstate(divide="ignore", invalid="ignore"):
            bp = np.where(rng > 0, np.abs(c - o) / rng, 0.0)
        self.bp = bp
        # तेज़ी के लिए vectorized prefilter (वही शर्तें जो _candidate दोबारा जाँचता है)
        self.li_ok = (fin & (rng > 0) & (self.bull | self.bear) & (bp >= I.legInMinBody)
                      & (self.tr >= I.legInAtrMult * np.where(fin, self.atr, np.inf)))
        self.lo_ok = (fin & (rng > 0) & (self.bull | self.bear) & ((1.0 - bp) <= I.legOutMaxWick)
                      & (self.tr >= I.legOutAtrMult * np.where(fin, self.atr, np.inf)))
        boring_ok = fin & (self.tr <= I.boringAtrMult * np.where(fin, self.atr, -np.inf))
        self.nb_cum = np.concatenate(([0], np.cumsum(~boring_ok))).astype("int64")
        # DATR — पिछले पूरे दिन का Daily ATR (Pine: request.security("D", rma(tr)[1]))
        self.datr = np.full(self.n, np.nan)
        if daily is not None and len(daily) > I.atrPeriod + 2:
            try:
                dh = daily["high"].to_numpy(dtype="float64")
                dl = daily["low"].to_numpy(dtype="float64")
                dc = daily["close"].to_numpy(dtype="float64")
                datr = _rma(_true_range(dh, dl, dc), I.atrPeriod)
                didx = pd.DatetimeIndex(daily.index)
                if didx.tz is not None:
                    didx = didx.tz_convert(IST).tz_localize(None)
                dday = didx.as_unit("ns").asi8 // 86_400_000_000_000
                pos = np.searchsorted(dday, self.day_id, side="left") - 1   # पिछला पूरा दिन
                ok = pos >= 0
                self.datr[ok] = datr[pos[ok]]
            except Exception:                                  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 4. CANDIDATE — Pine f_candidate(bc, nOut) · bar p = आख़िरी leg-out (Pine का bar 0)
# ---------------------------------------------------------------------------
def _candidate(p: int, bc: int, nout: int, S: _S, I: PineInputs):
    """सफल → dict ; असफल → (stage, reason)"""
    fo = p - (nout - 1)                    # पहली leg-out
    b0 = p - nout                          # सबसे नई boring
    bl = p - (nout + bc - 1)               # सबसे पुरानी boring
    li = bl - 1                            # leg-in
    pi = li - 1                            # leg-in के पीछे वाली candle (CBLI)
    if pi < 1:
        return (0, "data")
    atr = S.atr
    if not (np.isfinite(atr[pi]) and np.isfinite(atr[fo])):
        return (0, "data")
    o, h, l, c, tr = S.o, S.h, S.l, S.c, S.tr
    # ---- 1. Leg-in: filled · TR ≥ ATR × · closing-side wick छोटी ----
    in_h, in_l, in_o, in_c = h[li], l[li], o[li], c[li]
    in_range = in_h - in_l
    in_bull, in_bear = in_c > in_o, in_c < in_o
    in_body = abs(in_c - in_o)
    in_body_hi, in_body_lo = max(in_o, in_c), min(in_o, in_c)
    if in_range <= 0 or not (in_bull or in_bear):
        return (1, "Leg-in doji")
    if in_body / in_range < I.legInMinBody:
        return (1, "Leg-in not filled")
    if tr[li] < I.legInAtrMult * atr[li]:
        return (1, "Leg-in TR < ATR")
    if ((in_h - in_c) if in_bull else (in_c - in_l)) / in_range > I.legInMaxCloseWick:
        return (1, "Leg-in closing wick")
    # ---- 2. CBLI: उल्टे रंग की पिछली candle की body बड़ी = trap ----
    if I.useCBLI:
        opp = (c[pi] < o[pi]) if in_bull else (c[pi] > o[pi])
        if opp and abs(c[pi] - o[pi]) > I.cbliMaxBody * in_body:
            return (2, "CBLI")
    # ---- 3. Boring: हर candle TR ≤ ATR × ----
    base_h, base_l = -1e20, 1e20
    any_bull = any_bear = False
    for k in range(b0, bl - 1, -1):
        if not np.isfinite(atr[k]) or tr[k] > I.boringAtrMult * atr[k]:
            return (3, "Boring TR > ATR")
        base_h = max(base_h, h[k])
        base_l = min(base_l, l[k])
        any_bull = any_bull or c[k] > o[k]
        any_bear = any_bear or c[k] < o[k]
    base_range = base_h - base_l
    if base_range <= 0:
        return (3, "Boring size 0")
    # ---- 4. पहली leg-out: रंग = दिशा · TR ≥ ATR × · wick छोटी ----
    demand, supply = c[fo] > o[fo], c[fo] < o[fo]
    fo_range = h[fo] - l[fo]
    if not (demand or supply):
        return (4, "Leg-out doji")
    if tr[fo] < I.legOutAtrMult * atr[fo]:
        return (4, "Leg-out TR < ATR")
    if fo_range <= 0 or (fo_range - abs(c[fo] - o[fo])) / fo_range > I.legOutMaxWick:
        return (4, "Leg-out wick")
    # ---- 5. Leg-out 2/3: उसी रंग की, open ≈ पिछली close ----
    out_h, out_l = h[fo], l[fo]
    ob_hi, ob_lo = max(o[fo], c[fo]), min(o[fo], c[fo])
    for k in range(fo + 1, p + 1):
        same = (c[k] > o[k]) if demand else (c[k] < o[k])
        if (not same) or abs(o[k] - c[k - 1]) > I.legOutJoinTolAtr * atr[k - 1]:
            return (5, "Leg-outs not continuous")
        out_h, out_l = max(out_h, h[k]), min(out_l, l[k])
        ob_hi, ob_lo = max(ob_hi, o[k], c[k]), min(ob_lo, o[k], c[k])
    # ---- 6. 1-2-4 ----
    if in_range < I.legInToBaseSizeMult * base_range:
        return (6, f"1-2-4: leg-in < {I.legInToBaseSizeMult:g}× boring")
    if (ob_hi - ob_lo) < I.legOutToBaseSizeMult * base_range:
        return (6, f"1-2-4: leg-out < {I.legOutToBaseSizeMult:g}× boring")
    pat = ("RBR" if in_bull else "DBR") if demand else ("DBD" if in_bear else "RBD")
    reversal = pat in ("DBR", "RBD")
    # ---- 7. Formation (DBR/RBD): leg-out open, leg-in body के अंदर — कोई tolerance नहीं ----
    if I.useFormation and reversal and (o[fo] > in_body_hi or o[fo] < in_body_lo):
        return (7, "Formation: leg-out open leg-in body के बाहर")
    # ---- 8. White area: पहली leg-out की body, boring की पूरी wick (high–low) न ढके ----
    fo_body_hi, fo_body_lo = max(o[fo], c[fo]), min(o[fo], c[fo])
    white_ok = not (fo_body_lo <= base_l and fo_body_hi >= base_h)
    if I.useWhiteArea and not white_ok:
        return (8, "No white area")
    # ---- 9. Optional: volume · imbalance ----
    if I.reqVolume and not (S.v[fo] > S.v[li]):
        return (9, "Leg-out volume ≤ leg-in")
    imbalance = bool((l[fo] > base_h or ob_hi > in_h) if demand else (h[fo] < base_l or ob_lo < in_l))
    if I.reqImbalance and not imbalance:
        return (9, "No imbalance")
    # ---- Levels ----
    course = I.distalMode == DIST_MODES[0]
    if demand:
        prox = base_h
        dist = (min(base_l, in_l, out_l) if reversal else min(base_l, out_l)) if course else base_l
        xrpt = ob_hi
    else:
        prox = base_l
        dist = (max(base_h, in_h, out_h) if reversal else max(base_h, out_h)) if course else base_h
        xrpt = ob_lo
    # ---- Power of leg-out: leg-out candles (1–3, एक ही दिशा) के अंदर ही ≥ 1:3 ----
    p_sl = dist - S.slbuf[p] if demand else dist + S.slbuf[p]
    p_risk = abs(prox - p_sl)
    p_rr = ((xrpt - prox) if demand else (prox - xrpt)) / p_risk if p_risk > 0 else 0.0
    if p_rr < POWER_MIN_RR:
        return (10, f"Power < 1:{POWER_MIN_RR:g}")
    # ---- Quality score (0–100, सिर्फ़ रैंकिंग) ----
    hq_in = tr[li] >= I.hqLegInAtr * atr[li]
    hq_out = tr[fo] >= I.hqLegOutMult * tr[li]
    vs = S.vol_sma[fo]
    vol_ok = bool(np.isfinite(vs) and S.v[fo] > vs)
    heavy = bool(fo_range > 0 and abs(c[fo] - o[fo]) / fo_range >= I.bodyHeavyPct)
    fav = bool(any_bear if demand else any_bull)
    score = ((15 if bc == 1 else 10 if bc == 2 else 5) + 15 * int(hq_in) + 20 * int(hq_out)
             + 15 * int(vol_ok) + 15 * int(heavy) + 10 * int(fav) + 10 * int(imbalance))
    return dict(
        bc=bc, nout=nout, demand=bool(demand), pattern=pat,
        category="Reversal" if reversal else "Continuation",
        prox=float(prox), dist=float(dist), xrpt=float(xrpt),
        score=int(score), hq=bool(hq_in and hq_out), white=bool(white_ok), imbalance=imbalance,
        q_hq_in=bool(hq_in), q_hq_out=bool(hq_out), q_vol=vol_ok, q_heavy=heavy, q_fav=fav,
        m_in_atr=float(tr[li] / atr[li]), m_out_atr=float(tr[fo] / atr[fo]),
        m_li_ratio=float(in_range / base_range), m_lo_ratio=float((ob_hi - ob_lo) / base_range),
        base_h=float(base_h), base_l=float(base_l),
        b0=b0, bl=bl, li=li,
    )


def _find(p: int, S: _S, I: PineInputs):
    """Pine f_find(): nOut 1→max, boring min→max — पहला मान्य candidate।"""
    best_stage, best_reason = -1, ""
    for nout in range(1, I.maxLegOut + 1):
        fo = p - (nout - 1)
        if fo < 0 or not S.lo_ok[fo]:
            continue
        for bc in range(I.minBaseCount, I.maxBaseCount + 1):
            li = p - (nout + bc)
            if li - 1 < 1 or not S.li_ok[li]:
                continue
            bl, b0 = li + 1, p - nout
            if S.nb_cum[b0 + 1] - S.nb_cum[bl] != 0:
                continue
            cand = _candidate(p, bc, nout, S, I)
            if isinstance(cand, tuple):
                if cand[0] > best_stage:
                    best_stage, best_reason = cand
                continue
            return cand, best_stage, best_reason
    return None, best_stage, best_reason


# ---------------------------------------------------------------------------
# 5. ENGINE — ज़ोन + states + swings + shadows + MTF snapshot (Pine का chart engine)
# ---------------------------------------------------------------------------
STATE_FRESH, STATE_TRIG = "Fresh", "Tested"
STATE_BROKEN, STATE_EXHAUSTED = "Broken", "Exhausted"
ALIVE_STATES = (STATE_FRESH, STATE_TRIG)


def _bar_incomplete(S: _S, tf: str) -> bool:
    """आख़िरी candle अभी बन रही है? (Pine: barstate.isconfirmed नहीं)"""
    try:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        sec = TF_SECONDS.get(tf, 0)
        if sec < 86400:
            return bool(S.t[-1] + sec * 1000 > now_ms)
        return bool(market_open_now() and S.t[-1] + sec * 1000 > now_ms)
    except Exception:                                          # noqa: BLE001
        return False


def engine(df: pd.DataFrame, I: PineInputs, tf: str, htf_daily: Optional[pd.DataFrame] = None,
           return_all: bool = False, want_snapshot: bool = True,
           snap_out: Optional[dict] = None, ctx: Optional[dict] = None) -> List[dict]:
    """Pine chart engine — हर confirmed बार पर नया ज़ोन, फिर state update।"""
    S = _S(df)
    S.prepare(I, htf_daily)
    n = S.n
    if n < 20:
        return []
    tt = trader_type(tf, I)
    bpct = _buf_pct(tt, I)
    S.slbuf = np.where((I.bufferMode == BUFFER_MODES[0]) & np.isfinite(S.datr),
                       S.datr * bpct / 100.0, I.slBufferAtr * S.atr)
    start = max(0, n - max(I.scanBars, 50))
    min_p = max(I.atrPeriod, I.maxBaseCount + I.maxLegOut + 3, 11)
    live_last = _bar_incomplete(S, tf)
    tf_sec = TF_SECONDS.get(tf, 0)
    L, R = I.swingLeft, I.swingRight

    zones: List[dict] = []
    rej: Dict[str, int] = {}
    created = 0
    sw_h = sw_l = None
    sw_h_body = sw_l_body = None
    sw_h_bar = sw_l_bar = None
    h_brk = l_brk = None

    for p in range(start, n):
        # ---------- confirmed swings (ta.pivothigh / ta.pivotlow) ----------
        cpv = p - R
        if cpv - L >= 0:
            hc, lc = S.h[cpv], S.l[cpv]
            if hc > S.h[cpv - L:cpv].max() and hc > S.h[cpv + 1:p + 1].max():
                sw_h, sw_h_bar, h_brk = hc, cpv, None
                sw_h_body = max(S.o[cpv], S.c[cpv])
            if lc < S.l[cpv - L:cpv].min() and lc < S.l[cpv + 1:p + 1].min():
                sw_l, sw_l_bar, l_brk = lc, cpv, None
                sw_l_body = min(S.o[cpv], S.c[cpv])
        if sw_h is not None and h_brk is None and S.c[p] > sw_h:
            h_brk = p
        if sw_l is not None and l_brk is None and S.c[p] < sw_l:
            l_brk = p

        # ---------- नया ज़ोन (bar p = आख़िरी leg-out) ----------
        if p >= min_p and np.isfinite(S.atr[p]) and not (p == n - 1 and live_last):
            cand, stage, reason = _find(p, S, I)
            if cand is None:
                if stage >= 5 and reason:
                    rej[reason] = rej.get(reason, 0) + 1
            else:
                dem = cand["demand"]
                dup = False
                checked = 0
                for old in reversed(zones):
                    if (old["state"] in ALIVE_STATES and old["demand"] == dem
                            and abs(old["prox"] - cand["prox"]) < S.atr[p] * 0.25):
                        dup = True
                        break
                    checked += 1
                    if checked >= 11:
                        break
                slbuf = S.slbuf[p]
                sl = cand["dist"] - slbuf if dem else cand["dist"] + slbuf
                entry = cand["prox"]
                risk = abs(entry - sl)
                if dup:
                    rej["Duplicate"] = rej.get("Duplicate", 0) + 1
                elif risk > 0:
                    li, nout = cand["li"], cand["nout"]
                    tp = entry + risk * I.targetRR if dem else entry - risk * I.targetRR
                    xrr = ((cand["xrpt"] - entry) if dem else (entry - cand["xrpt"])) / risk
                    qty = int(math.floor(I.accountCapital * I.riskPct / 100.0 / risk))
                    # ---- पुराने ज़ोन: behind · BASE BREAK (उल्टा ज़ोन तोड़ा) ----
                    behind = base_brk = False
                    for old in reversed(zones):
                        if old["created_bar"] >= li:
                            continue
                        if old["state"] in ALIVE_STATES:
                            behind = True
                        if old["demand"] != dem and (old["state"] in ALIVE_STATES or (
                                old["state"] == STATE_BROKEN and old.get("broken_at") is not None
                                and old["broken_at"] >= p - (nout - 1))):
                            for k in range(p - (nout - 1), p + 1):
                                if (dem and S.c[k] > old["dist"]) or ((not dem) and S.c[k] < old["dist"]):
                                    base_brk = True
                                    break
                    # ---- SWING: DZ ने swing high तोड़ा · SZ ने swing low ----
                    if dem:
                        swing_brk = h_brk is not None and sw_h_bar is not None and sw_h_bar < li and h_brk >= li
                    else:
                        swing_brk = l_brk is not None and sw_l_bar is not None and sw_l_bar < li and l_brk >= li
                    shadow = bool(I.showShadow and (behind or base_brk or swing_brk))
                    # ---- FLIP (High quality): boring उल्टे ज़ोन की shadow में बनी और leg-out ने उसी का SL तोड़ा ----
                    flip = False
                    lo_hi = float(S.h[p - (nout - 1):p + 1].max())
                    lo_lo = float(S.l[p - (nout - 1):p + 1].min())
                    pad = I.shadowAtr * S.atr[p]
                    for old in reversed(zones):
                        old_ok = (old["demand"] != dem and old["created_bar"] < li and (
                            old["state"] in ALIVE_STATES or (
                                old["state"] == STATE_BROKEN and old.get("sl_hit")
                                and old.get("broken_at") is not None and old["broken_at"] >= p - (nout - 1))))
                        if old_ok:
                            s_top = max(old["prox"], old["sl"]) + pad
                            s_bot = min(old["prox"], old["sl"]) - pad
                            base_in = cand["base_l"] <= s_top and cand["base_h"] >= s_bot
                            out_breaks = (lo_hi >= old["sl"]) if dem else (lo_lo <= old["sl"])
                            if base_in and out_breaks:
                                flip = True
                                break
                    # ---- SWING BRK (High quality): swing area में boring, leg-out ने वही swing तोड़ा ----
                    sw_brk = False
                    sw_pad = I.shadowAtr * S.atr[p]
                    if dem and sw_h is not None and h_brk is not None and sw_h_bar < li and h_brk >= p - (nout - 1):
                        a_top = sw_h + sw_pad
                        a_bot = min(sw_h_body if sw_h_body is not None else sw_h, sw_h - SWING_AREA_ATR * S.atr[p]) - sw_pad
                        sw_brk = cand["base_l"] <= a_top and cand["base_h"] >= a_bot
                    if (not dem) and sw_l is not None and l_brk is not None and sw_l_bar < li and l_brk >= p - (nout - 1):
                        b_bot = sw_l - sw_pad
                        b_top = max(sw_l_body if sw_l_body is not None else sw_l, sw_l + SWING_AREA_ATR * S.atr[p]) + sw_pad
                        sw_brk = cand["base_l"] <= b_top and cand["base_h"] >= b_bot
                    tags = " ".join(t for t, on in (("🔁FLIP", flip), ("SW-BRK", sw_brk), ("⚡1:5", xrr >= POWER_BEST_RR), ("★HQ", cand["hq"]), ("BASE BREAK", base_brk),
                                                    ("SWING", swing_brk), ("Shadow", shadow and not (base_brk or swing_brk)))
                                    if on)
                    bc = cand["bc"]
                    z = dict(
                        demand=dem, side="Demand" if dem else "Supply",
                        prox=cand["prox"], dist=cand["dist"], entry=float(entry),
                        sl=float(sl), tp=float(tp),
                        t3=float(entry + risk * POWER_MIN_RR if dem else entry - risk * POWER_MIN_RR), t5=float(cand["xrpt"]), xrpt=float(cand["xrpt"]),
                        risk=float(risk), qty=qty, xrr=float(xrr), pow_rr=float(xrr),
                        score=int(cand["score"]), hq=bool(cand["hq"]), best=bool(xrr >= POWER_BEST_RR), flip=bool(flip), sw_brk=bool(sw_brk),
                        tags=tags, pattern=cand["pattern"], category=cand["category"],
                        reversal=cand["category"] == "Reversal",
                        base_count=bc, leg_outs=nout, single_lo=nout == 1, grade=_grade(bc),
                        grade_hi=_GRADE_HI.get(bc, "ठीक-ठाक"), white=cand["white"],
                        imbalance=cand["imbalance"], behind=behind, base_break=base_brk,
                        swing_break=swing_brk, shadow=shadow,
                        q_hq_in=cand["q_hq_in"], q_hq_out=cand["q_hq_out"], q_vol=cand["q_vol"],
                        q_heavy=cand["q_heavy"], q_fav=cand["q_fav"],
                        m_in_atr=cand["m_in_atr"], m_out_atr=cand["m_out_atr"],
                        m_li_ratio=cand["m_li_ratio"], m_lo_ratio=cand["m_lo_ratio"],
                        trader_type=TT_NAME.get(tt, ""), t1_rr=I.targetRR,
                        state=STATE_FRESH, entries=0, touches=0, outside=False,
                        created_bar=p, bar=p, atr_c=float(S.atr[p]),
                        left_bar=li, time=int(S.t[p]), start_time=int(S.t[li]),
                        broken_at=None, prox_touches=0, prox_out=True, tf=tf, tf_sec=tf_sec, symbol=None,
                    )
                    zones.append(z)
                    created += 1
                    if len(zones) > MAX_ZONES:
                        zones.pop(0)

        # ---------- state update: Fresh → Tested → Exhausted / Broken ----------
        lo_, hi_ = S.l[p], S.h[p]
        for z in zones:
            if z["state"] not in ALIVE_STATES or p <= z["created_bar"]:
                continue
            dem = z["demand"]
            touch = (lo_ <= z["entry"]) if dem else (hi_ >= z["entry"])
            hit_sl = (lo_ <= z["sl"]) if dem else (hi_ >= z["sl"])
            if touch and z["prox_out"]:
                z["prox_touches"] += 1            # नया टच (बीच में proximal से बाहर गया था)
            z["prox_out"] = not touch
            if hit_sl or z["prox_touches"] > PROX_MAX_TOUCH:
                z["state"] = STATE_BROKEN
                z["broken_at"] = p
                z["sl_hit"] = bool(hit_sl)
            elif z["state"] == STATE_FRESH:
                if touch:
                    z["state"] = STATE_TRIG
                    z["entries"] = z["touches"] = 1
                    z["outside"] = False
                    z["entry_bar"] = p
            else:
                if not touch:
                    z["outside"] = True
                elif z["outside"]:
                    z["entries"] += 1
                    z["touches"] = z["entries"]
                    z["outside"] = False
                    if z["entries"] > I.maxEntries:
                        z["state"] = STATE_EXHAUSTED
            if z["state"] not in ALIVE_STATES:
                z["dead_bar"] = p
                z["dead_time"] = int(S.t[p])

    alive = [z for z in zones if z["state"] in ALIVE_STATES]
    snap = _snap_of(alive)
    _LAST_SNAP["snap"] = snap
    if snap_out is not None:
        snap_out["snap"] = snap
        snap_out["rej"] = rej
        snap_out["created"] = created
    out = zones if return_all else alive
    for z in out:
        z["ltp"] = float(S.c[-1])
        z["dist_pct"] = abs(z["prox"] - z["ltp"]) / z["ltp"] * 100.0 if z["ltp"] else None
        z["age_bars"] = (n - 1) - z["created_bar"]
        z["in_zone"] = bool((S.l[-1] <= z["prox"]) if z["demand"] else (S.h[-1] >= z["prox"]))
        z["atr_now"] = float(S.atr[-1]) if np.isfinite(S.atr[-1]) else float("nan")
    return out

_LAST_SNAP: dict = {}


def _snap_of(alive: List[dict]) -> dict:
    """Pine: latest active DEMAND और SUPPLY proximal (array के अंत से पहला)।"""
    d = s = None
    dt = st_ = 0
    for z in reversed(alive):
        if z["demand"] and d is None:
            d, dt = z["prox"], z["start_time"]
        if (not z["demand"]) and s is None:
            s, st_ = z["prox"], z["start_time"]
        if d is not None and s is not None:
            break
    return {"demand": d, "supply": s, "demand_time": dt, "supply_time": st_}


# ---------------------------------------------------------------------------
# 5b. PULSE · TREND · ACTION · WHOLESALE (25× नियम वाले ट्रेंड TF से)
# ---------------------------------------------------------------------------
PULSE_EMA = 20          # सिर्फ़ जानकारी (Pulse · Trend कॉलम) — ज़ोन को नहीं रोकता
TREND_THR = 0.5


def _htf_stats(df: Optional[pd.DataFrame], I: PineInputs) -> Optional[dict]:
    """ट्रेंड TF का [close[1], open[1], ema[1], ema[7], atr[1]] — सिर्फ़ Pulse/Trend जानकारी"""
    if df is None or len(df) < max(PULSE_EMA, I.atrPeriod) + 9:
        return None
    c = df["close"].astype("float64")
    e = c.ewm(span=PULSE_EMA, adjust=False).mean().to_numpy()
    atr = _rma(_true_range(df["high"].to_numpy(dtype="float64"), df["low"].to_numpy(dtype="float64"),
                           c.to_numpy()), I.atrPeriod)
    return dict(c=float(c.iloc[-2]), o=float(df["open"].iloc[-2]), e=float(e[-2]), ep=float(e[-8]),
                atr=float(atr[-2]))


def htf_context(frames: Dict[str, pd.DataFrame], tf: str, I: PineInputs) -> dict:
    tt = trader_type(tf, I)
    ttf = trend_tf(tf)
    t = _htf_stats(frames.get(ttf), I)
    w = _htf_stats(frames.get("1d" if tt == 2 else ttf), I)
    out = dict(trend_tf=ttf, pulse_pos=False, pulse_neg=False, trend_up=False, trend_down=False,
               action_long=False, action_short=False, ws_buy=None, ws_sell=None)
    if t is None:
        return out
    pp = t["c"] > t["o"] and t["c"] > t["e"]
    pn = t["c"] < t["o"] and t["c"] < t["e"]
    if tt == 2 and w is not None:
        pp = pp and w["c"] > w["o"] and w["c"] > w["e"]
        pn = pn and w["c"] < w["o"] and w["c"] < w["e"]
    slope = (t["e"] - t["ep"]) / t["atr"] if t["atr"] and np.isfinite(t["atr"]) and t["atr"] > 0 else 0.0
    up, dn = slope > TREND_THR, slope < -TREND_THR
    out.update(pulse_pos=bool(pp), pulse_neg=bool(pn), trend_up=bool(up), trend_down=bool(dn),
               action_long=bool(pp and up), action_short=bool(pn and dn))
    if w is not None and np.isfinite(w["atr"]):
        out.update(ws_buy=w["c"] - w["atr"], ws_sell=w["c"] + w["atr"])
    return out


# ---------------------------------------------------------------------------
# 6. MTF confirmation (score/तालिका के लिए)
# ---------------------------------------------------------------------------
def adjacent_tfs(chart_tf: str, peers: Sequence[str]) -> List[str]:
    """Pine f_adj(): chart TF छोड़कर बाकी; अनुपात 0.2×–5× के भीतर।"""
    base = TF_SECONDS.get(chart_tf, 0)
    out = []
    for p in peers:
        if p == chart_tf:
            continue
        r = TF_SECONDS.get(p, 0) / base if base else 0
        if 0.2 <= r <= 5.0:
            out.append(p)
    return out


def mtf_hits(demand: bool, prox: float, dist: float, atr_val: float, chart_tf: str,
             snapshots: Dict[str, dict], peers: Optional[Sequence[str]] = None):
    """कितने पड़ोसी TF का level इस ज़ोन के भीतर है (±0.5×ATR)।"""
    a = atr_val if np.isfinite(atr_val) else 0.0
    top = max(prox, dist) + 0.5 * a
    bot = min(prox, dist) - 0.5 * a
    keys = [k for k in snapshots if (peers is None or k in peers)]
    hits = []
    for peer in adjacent_tfs(chart_tf, keys):
        lvl = (snapshots.get(peer) or {}).get("demand" if demand else "supply")
        if lvl is not None and bot <= lvl <= top:
            hits.append(peer)
    return len(hits), hits


def verdict(z: dict) -> str:
    """Quality score सिर्फ़ रैंकिंग है — ★ HQ = दोनों HQ शर्तें पूरी"""
    return "★ HQ" if z.get("hq") else ("Strong" if int(z.get("score", 0)) >= 70 else "Normal")


def info_text(z: dict, target_rr: float) -> str:
    s = (f"{z['pattern']} · {z['category']} · {z['base_count']} boring · Score {z['score']}"
         + (" ★" if z.get("hq") else ""))
    s += f"\nEntry {z['entry']:.2f}  SL {z['sl']:.2f}"
    s += f"\nT1 1:{target_rr:g} {z['tp']:.2f}"
    s += f"\nXRPT {z['xrpt']:.2f} (1:{z.get('xrr', 0):.1f})"
    s += f"\nQty {z['qty']} · {z['state']}"
    return s


# ---------------------------------------------------------------------------
# 7. EOD बैंड — High + ATR14(RMA) … Low − ATR14(RMA)
# ---------------------------------------------------------------------------
def _atr_last(df: pd.DataFrame, period: int = 14) -> float:
    try:
        h = df["high"].to_numpy(dtype="float64")
        l = df["low"].to_numpy(dtype="float64")
        c = df["close"].to_numpy(dtype="float64")
        a = _rma(_true_range(h, l, c), max(1, int(period)))
        return float(a[-1]) if len(a) else float("nan")
    except Exception:                                          # noqa: BLE001
        return float("nan")


def eod_band(df: Optional[pd.DataFrame], tf: str, atr_period: int = 14) -> Optional[dict]:
    """पूरी हो चुकी EOD candle का बैंड: High+ATR14 … Low−ATR14 (RMA)।"""
    if df is None or len(df) < atr_period + 3:
        return None
    h = df["high"].to_numpy(dtype="float64")
    l = df["low"].to_numpy(dtype="float64")
    c = df["close"].to_numpy(dtype="float64")
    atr = _rma(_true_range(h, l, c), max(1, int(atr_period)))
    idx = pd.DatetimeIndex(df.index)

    def pstart(ts):
        ts = ts.tz_convert(IST) if getattr(ts, "tzinfo", None) else ts.tz_localize("UTC").tz_convert(IST)
        if tf == "1w":
            return (ts - pd.Timedelta(days=int(ts.weekday()))).normalize()
        if tf == "1M":
            return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return ts.normalize()

    i = len(df) - 1
    try:
        now = datetime.now(IST)
        closed = (not market_open_now()) and (now.hour * 60 + now.minute) >= 15 * 60 + 30
        if pstart(idx[-1]) >= pstart(datetime.now(IST)) and not (closed and tf == "1d"):
            i -= 1
    except Exception:                                          # noqa: BLE001
        pass
    if i < 0:
        return None
    a = float(atr[i])
    if not np.isfinite(a):
        return None
    return dict(low=float(l[i]) - a, high=float(h[i]) + a, atr=a,
                bar_low=float(l[i]), bar_high=float(h[i]),
                bar_time=int(idx[i].value // 1_000_000))


# ---------------------------------------------------------------------------
# 8. SCAN — एक symbol के सभी TF + MTF snapshot + Pulse/Trend + Room
# ---------------------------------------------------------------------------
_EMPTY_SNAP = {"demand": None, "supply": None, "demand_time": 0, "supply_time": 0}
_ZCACHE: Dict[tuple, tuple] = {}
_ZVER = "z25"           # इंजन बदले तो बढ़ाएँ — पुराना कैश अपने आप बेकार


def _inputs_hash(I: PineInputs) -> str:
    blob = json.dumps(I.__dict__, default=str, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _bar_id(df: pd.DataFrame) -> int:
    """उस TF के आख़िरी बार की पहचान — नया बार बना तो बदल जाएगी।"""
    try:
        return int(pd.Timestamp(df.index[-1]).value // 10 ** 9)
    except Exception:                                          # noqa: BLE001
        return 0


def _zones_for(symbol: str, tf: str, df: pd.DataFrame, I: PineInputs, ihash: str,
               store: dict, daily: Optional[pd.DataFrame] = None, ctx: Optional[dict] = None):
    """Pine इंजन का नतीजा — जब तक उसी TF का नया बार न बने, दोबारा नहीं चलता।"""
    bid = _bar_id(df)
    ent = store.get(tf)
    if isinstance(ent, dict) and ent.get("bid") == bid and ent.get("rows") is not None:
        return ent["rows"], (ent.get("snap") or _EMPTY_SNAP), (ent.get("diag") or {}), True
    mem = _ZCACHE.get((symbol, tf, ihash))
    if mem and mem[0] == bid:
        return mem[1], mem[2], mem[3], True
    bag: dict = {}
    zs = engine(df, I, tf, htf_daily=daily, return_all=False, snap_out=bag, ctx=ctx)
    snap = bag.get("snap") or dict(_EMPTY_SNAP)
    diag = {"rej": bag.get("rej", {}), "created": bag.get("created", 0)}
    _ZCACHE[(symbol, tf, ihash)] = (bid, zs, snap, diag)
    store[tf] = {"bid": bid, "rows": zs, "snap": snap, "diag": diag}
    return zs, snap, diag, False


def scan_symbol(symbol: str,
                frames: Dict[str, pd.DataFrame],
                daily: Optional[pd.DataFrame],
                inputs: PineInputs,
                range_pct: float,
                tfs: Sequence[str] = ALL_TFS,
                ltp: Optional[float] = None,
                fut_frames: Optional[Dict[str, pd.DataFrame]] = None) -> dict:
    """एक symbol के सभी TF — Pine इंजन के ज़ोन + MTF snapshot + Pulse/Trend जानकारी।
    Pine की तरह कोई अतिरिक्त फ़िल्टर नहीं: Quality score सिर्फ़ क्रम (रैंकिंग) के लिए।"""
    I = inputs.sanitized()
    ihash = _inputs_hash(I)
    skey = _ckey(_ZVER, ihash, symbol)
    store = _load(skey, 7 * 86400) or {}
    dirty = False
    per_tf: Dict[str, List[dict]] = {}
    atr_map: Dict[str, float] = {}
    snaps: Dict[str, dict] = {}
    ctxs: Dict[str, dict] = {}
    rej: Dict[str, int] = {}
    created = 0
    daily = daily if daily is not None else frames.get("1d")
    for tf in tfs:
        df = frames.get(tf)
        ctxs[tf] = htf_context(frames, tf, I)
        if df is None or len(df) < 30:
            per_tf[tf] = []
            atr_map[tf] = float("nan")
            snaps[tf] = dict(_EMPTY_SNAP)
            continue
        zs, snap, diag, hit = _zones_for(symbol, tf, df, I, ihash, store, daily, ctxs[tf])
        dirty = dirty or not hit
        per_tf[tf] = [dict(z) for z in zs]
        snaps[tf] = snap
        atr_map[tf] = _atr_last(df, I.atrPeriod)
        created += int(diag.get("created", 0))
        for k, v in (diag.get("rej") or {}).items():
            rej[k] = rej.get(k, 0) + int(v)
    if dirty:
        _save(skey, store)

    # ---- Future चार्ट के ज़ोन (Dhan) → उसी TF, उसी दिन, उसी तरफ़ = C+F (सिर्फ़ निशान) ----
    fut_days: Dict[str, set] = {}
    for tf in tfs:
        fdf = (fut_frames or {}).get(tf)
        if fdf is None or len(fdf) < 30:
            continue
        fkey = (symbol + "|FUT", tf, ihash)
        mem = _ZCACHE.get(fkey)
        bid = _bar_id(fdf)
        if mem and mem[0] == bid:
            fz = mem[1]
        else:
            fz = engine(fdf, I, tf, htf_daily=(fut_frames or {}).get("1d"), return_all=True)
            _ZCACHE[fkey] = (bid, fz, None, None)
        fut_days[tf] = {(z["demand"], datetime.fromtimestamp(z["time"] / 1000, IST).date()) for z in fz}

    rows: List[dict] = []
    px = ltp if ltp else _last_close(frames, tfs)
    for tf in tfs:
        cx = ctxs.get(tf) or {}
        for z in per_tf[tf]:
            dem = z["demand"]
            z["symbol"] = symbol
            # ---- MTF: पड़ोसी TF का proximal इस ज़ोन में (Pine: Show other-TF proximal lines) ----
            if I.showMTF:
                z["mtf_n"], z["mtf_tfs"] = mtf_hits(dem, z["prox"], z["dist"], z["atr_now"], tf, snaps)
            else:
                z["mtf_n"], z["mtf_tfs"] = 0, []
            z["mtf"] = z["mtf_n"] >= 1
            # ---- Pulse / Trend (25× ट्रेंड TF) — सिर्फ़ जानकारी ----
            z["action_ok"] = bool(cx.get("action_long") if dem else cx.get("action_short"))
            z["trend_tf"] = cx.get("trend_tf", "")
            z["pulse"] = "P+" if cx.get("pulse_pos") else "P−" if cx.get("pulse_neg") else "P~"
            z["trend"] = "T↑" if cx.get("trend_up") else "T↓" if cx.get("trend_down") else "T→"
            z["verdict"] = verdict(z)
            z["cf"] = (dem, datetime.fromtimestamp(z["time"] / 1000, IST).date()) in fut_days.get(tf, set())
            extra = [t for t, on in (("C+F", z["cf"]), ("MTF", z["mtf"])) if on]
            z["tags"] = " ".join([t for t in [z.get("tags", "")] + extra if t])
            z["dist_pct"] = abs(z["prox"] - px) / px * 100.0 if px else None
            if range_pct and z["dist_pct"] is not None and z["dist_pct"] > range_pct:
                continue
            rows.append(z)
    rows.sort(key=lambda r: (r["dist_pct"] if r["dist_pct"] is not None else 9e9,
                             -r["score"], TF_SECONDS.get(r["tf"], 0)))
    return {"rows": rows, "snapshots": snaps, "ltp": px, "atr": atr_map,
            "rej": rej, "created": created}


def _last_close(frames: Dict[str, pd.DataFrame], tfs: Sequence[str]) -> float:
    for tf in ("1d", "1h", "15m", "5m", "3m"):
        df = frames.get(tf)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
    for tf in tfs:
        df = frames.get(tf)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
    return 0.0



COVER_BARS = 1            # covered leg-out: सिर्फ़ ज़ोन के ठीक बाद की candle
MIS_MARGIN = 0.20          # SEBI: intraday पर कम से कम 20% margin (5×)


# ═══════════════════════════════════════════════════════════════════════
#  भाग 3d · INSTITUTIONAL फ़िल्टर — Location (Curve) और Zone-in-Zone
#   • Curve: ट्रेंड TF (25×) की पिछली 50 candles का दायरा; DZ निचले हिस्से
#     (discount) में, SZ ऊपरी हिस्से (premium) में — बिना भविष्य देखे
#   • HTF: छोटा ज़ोन, ट्रेंड TF के जीवित (Fresh/Tested) उसी तरफ़ के ज़ोन से टकराता हो
# ═══════════════════════════════════════════════════════════════════════
CURVE_LEN = 50
CURVE_MODES = {"off": None, "half": 0.5, "third": 1.0 / 3.0}
CURVE_LABEL = {"off": "बंद", "half": "आधे में (DZ नीचे 50%, SZ ऊपर 50%)",
               "third": "तिहाई में (DZ नीचे 33%, SZ ऊपर 33%)"}


def curve_series(frames: Dict[str, pd.DataFrame], tf: str) -> Optional[pd.DataFrame]:
    df = frames.get(trend_tf(tf))
    if df is None or len(df) < CURVE_LEN + 2:
        return None
    hh = df["high"].astype("float64").rolling(CURVE_LEN).max().shift(1)
    ll = df["low"].astype("float64").rolling(CURVE_LEN).min().shift(1)
    out = pd.DataFrame({"hh": hh, "ll": ll}).dropna()
    ix = pd.DatetimeIndex(out.index)
    out.index = ix.tz_localize(IST) if ix.tz is None else ix.tz_convert(IST)
    return out


def loc_at(series: Optional[pd.DataFrame], ts: pd.Timestamp, price: float) -> float:
    """0 = दायरे का निचला सिरा, 1 = ऊपरी सिरा; पता न हो तो nan।"""
    if series is None or series.empty:
        return float("nan")
    # Yahoo index की इकाई (s/ms) और now() (ns) अलग → pandas "Cannot losslessly convert units" देता था,
    # जिससे ज़्यादातर स्टॉक का पूरा स्कैन फेल हो रहा था। int64 ns पर तुलना करें।
    try:
        ix_ns = pd.DatetimeIndex(series.index).tz_convert("UTC").tz_localize(None).values.astype("datetime64[ns]").astype("int64")
        t_ns = pd.Timestamp(ts).tz_convert("UTC").tz_localize(None).to_datetime64().astype("datetime64[ns]").astype("int64")
        pos = int(np.searchsorted(ix_ns, t_ns, side="right")) - 1
    except Exception:                                            # noqa: BLE001
        return float("nan")
    if pos < 0:
        return float("nan")
    hh, ll = series["hh"].iat[pos], series["ll"].iat[pos]
    if not (hh > ll):
        return float("nan")
    return float(min(1.0, max(0.0, (price - ll) / (hh - ll))))


def curve_ok(mode: str, demand: bool, loc: float) -> bool:
    lim = CURVE_MODES.get(mode)
    if lim is None:
        return True
    if not np.isfinite(loc):
        return False
    return loc <= lim if demand else loc >= 1.0 - lim


def htf_conf(spans: Optional[dict], demand: bool, top: float, bot: float, ts: pd.Timestamp) -> bool:
    if spans is None:
        return False
    ms = ts.value // 1_000_000
    m = ((spans["dem"] == demand) & (spans["t0"] <= ms) & (ms < spans["t1"])
         & (spans["bot"] <= top) & (spans["top"] >= bot))
    return bool(m.any())


# ═══════════════════════════════════════════════════════════════════════
#  भाग 4 · Streamlit UI — ऊपर एक पंक्ति (toolbar), नीचे Google-Sheets टेबल
# ═══════════════════════════════════════════════════════════════════════

APP_BUILD = "v44 · Pine v24.10: SWING BRK कॉलम · 🔁FLIP · 2026-10-29"

st.set_page_config(page_title="ज़ोन MTF स्कैनर", page_icon="📐", layout="wide",
                   initial_sidebar_state="collapsed")

STATE_HI = {"Fresh": "Fresh", "Tested": "Tested"}
ALL_STATES = ["Fresh", "Tested"]
SIDE_HI = {"Demand": "मांग (Demand)", "Supply": "आपूर्ति (Supply)"}
CAT_HI = {"Reversal": "पलटाव", "Continuation": "निरंतरता"}

UNIVERSES = ["NSE All Futures", "NIFTY 50", "Global Instruments", "Futures + Global"]
BANDS = ["Daily (EOD)", "Weekly (EOD)", "Monthly (EOD)"]
BAND_LETTER = {"1d": "D", "1w": "W", "1M": "M"}
BAND_TF = {"Daily (EOD)": "1d", "Weekly (EOD)": "1w", "Monthly (EOD)": "1M"}
TV_TF = {"3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30", "75m": "75",
         "1h": "60", "2h": "120", "4h": "240", "6h": "360", "1d": "D", "1w": "W",
         "1M": "M"}
_STATE_CLS = {"Fresh": "gs-fresh", "Tested": "gs-trig", "Exhausted": "gs-no", "Broken": "gs-no"}


def cols(spec, gap="small"):
    try:
        return st.columns(spec, gap=gap, vertical_alignment="center")
    except TypeError:
        return st.columns(spec, gap=gap)


def put(col, fn, *a, **k):
    try:
        return getattr(col, fn)(*a, width="stretch", **k)
    except TypeError:
        return getattr(col, fn)(*a, use_container_width=True, **k)


try:
    import inspect as _inspect
    _JS_SORT = "unsafe_allow_javascript" in _inspect.signature(st.html).parameters
except Exception:                                              # noqa: BLE001
    _JS_SORT = False

SORT_COLS = ["दूरी %", "स्कोर", "स्क्रिप्ट", "TF", "पैटर्न", "स्थिति", "Proximal",
             "Distal", "LTP", "SL", "T1", "Qty", "Mkt Cap"]
_SORT_MAP = {"दूरी %": "dist_pct", "स्कोर": "score", "स्क्रिप्ट": "symbol", "TF": "tf",
             "पैटर्न": "pattern", "स्थिति": "state", "Proximal": "prox", "Distal": "dist",
             "LTP": "ltp", "SL": "sl", "T1": "tp", "Qty": "qty", "Mkt Cap": "mcap_cr"}


def sort_df(df: pd.DataFrame, col: str, desc: bool) -> pd.DataFrame:
    """fallback — जब सिर-क्लिक सॉर्ट (JS) उपलब्ध न हो।"""
    key = _SORT_MAP.get(col, "dist_pct")
    if key not in df.columns:
        return df
    out = df.copy()
    if key == "tf":
        out["_k"] = out["tf"].map(lambda t: TF_SECONDS.get(str(t), 0))
        key = "_k"
    try:
        return out.sort_values(key, ascending=not desc, kind="mergesort").reset_index(drop=True)
    except Exception:                                          # noqa: BLE001
        return out


def _html(src: str, js: bool = False) -> None:
    """कच्चा HTML/CSS — markdown पार्सर से नहीं गुज़रता।"""
    if js:
        try:
            st.html(src, unsafe_allow_javascript=True)
            return
        except TypeError:
            pass
        except Exception:                                      # noqa: BLE001
            pass
    try:
        st.html(src)
    except Exception:                                          # noqa: BLE001
        st.markdown(src, unsafe_allow_html=True)


def is_dark() -> bool:
    try:
        t = st.context.theme.type
        if t:
            return str(t).lower() == "dark"
    except Exception:                                          # noqa: BLE001
        pass
    try:
        return str(st.get_option("theme.base")).lower() == "dark"
    except Exception:                                          # noqa: BLE001
        pass
    return False


SHEET_CSS = r"""
.gs-wrap{border-radius:6px;overflow:hidden;}
.gs-scroll{max-height:min(72vh,760px);overflow:auto;}
table.gs{border-collapse:separate;border-spacing:0;width:100%;
         font-family:Roboto,'Noto Sans Devanagari',Arial,sans-serif;font-size:12.5px;}
table.gs th,table.gs td{padding:4px 9px;white-space:nowrap;vertical-align:middle;}
table.gs thead th{position:sticky;top:0;font-weight:600;text-align:left;z-index:2;
         padding-top:7px;padding-bottom:7px;}
table.gs th.gs-rn,table.gs td.gs-rn{position:sticky;left:0;z-index:1;
         text-align:center;font-size:10.5px;padding:4px 6px;min-width:32px;}
table.gs thead th.gs-rn{z-index:4;}
table.gs th.gs-rn{color:transparent;}
table.gs td.num{text-align:right;font-variant-numeric:tabular-nums;}
table.gs td.ctr{text-align:center;}
table.gs a{text-decoration:none;}
table.gs a:hover{text-decoration:underline;}
table.gs a.fut{font-weight:700;}
.gs-dot{font-size:13px;line-height:1;}
.gs-track{display:inline-block;width:56px;height:6px;border-radius:4px;overflow:hidden;
          vertical-align:middle;margin-right:6px;}
.gs-fill{display:block;height:6px;border-radius:4px;}
.gs-sm{font-size:11px;opacity:.85;}
table.gs thead th.srt{cursor:pointer;}
table.gs thead th.srt:hover{filter:brightness(1.18);}
table.gs thead th.srt::after{content:"\21C5";font-size:9px;opacity:.40;margin-left:5px;}
table.gs thead th.srt.s-asc::after{content:"\25B2";opacity:.95;}
table.gs thead th.srt.s-desc::after{content:"\25BC";opacity:.95;}
"""

SHEET_CSS_DARK = """
.gs-wrap{border:1px solid #2f3542;background:#0e1117;box-shadow:0 1px 3px rgba(0,0,0,.35);}
table.gs{color:#e6e9ef;background:#0e1117;}
table.gs th,table.gs td{border-right:1px solid #262b36;border-bottom:1px solid #262b36;}
table.gs thead th{background:#161b25;color:#c3cad6;box-shadow:inset 0 -1px 0 #2f3542;}
table.gs th.gs-rn,table.gs td.gs-rn{background:#12161f;color:#6b7482;border-right:1px solid #2f3542;}
table.gs tbody tr:hover td{background:#141a24;}
table.gs tbody tr:hover td.gs-rn{background:#161b25;}
table.gs a{color:#58a6ff;} table.gs a.fut{color:#79b8ff;}
.gs-dot-s{color:#ff4d4f;} .gs-dot-d{color:#3fb950;}
.gs-fresh{color:#3fb950;} .gs-trig{color:#58a6ff;} .gs-no{color:#f85149;}
.gs-ok{color:#3fb950;} .gs-star{color:#e3b341;} .gs-track{background:#21262d;}
"""

SHEET_CSS_LIGHT = """
.gs-wrap{border:1px solid #dadce0;background:#fff;box-shadow:0 1px 3px rgba(60,64,67,.10);}
table.gs{color:#202124;background:#fff;}
table.gs th,table.gs td{border-right:1px solid #e8eaed;border-bottom:1px solid #e8eaed;}
table.gs thead th{background:#f1f3f4;color:#3c4043;box-shadow:inset 0 -1px 0 #dadce0;}
table.gs th.gs-rn,table.gs td.gs-rn{background:#f8f9fa;color:#80868b;border-right:1px solid #dadce0;}
table.gs tbody tr:hover td{background:#f1f8ff;}
table.gs tbody tr:hover td.gs-rn{background:#f1f3f4;}
table.gs a{color:#1a73e8;} table.gs a.fut{color:#0b57d0;}
.gs-dot-s{color:#e53935;} .gs-dot-d{color:#2e7d32;}
.gs-fresh{color:#0b6b3a;} .gs-trig{color:#1a73e8;} .gs-no{color:#d93025;}
.gs-ok{color:#0b6b3a;} .gs-star{color:#f9ab00;} .gs-track{background:#e8eaed;}
"""


def css():
    base = """
.block-container{padding-top:3.2rem;padding-bottom:1rem;max-width:100%;}
div[data-testid="stHorizontalBlock"]{gap:.45rem;}
h1,h2,h3{letter-spacing:.2px;}
/* ⚙️ इनपुट: tablet/मोबाइल पर हर इनपुट पूरी चौड़ाई के आधे या पूरे में, शब्द टूटें नहीं */
.inp-panel div[data-testid="stExpander"] label p{word-break:normal;overflow-wrap:normal;white-space:normal;line-height:1.3}
div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"]{flex-wrap:wrap;row-gap:.6rem}
div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"] > div[data-testid="column"]{min-width:220px;flex:0 1 calc(33.333% - .45rem) !important}
div[data-testid="stExpander"] label p{word-break:keep-all;overflow-wrap:break-word}
div[data-testid="stExpander"] div[data-baseweb="tab-list"]{flex-wrap:wrap;gap:.25rem}
@media (max-width: 760px){
  div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
  div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"] > div[data-testid="column"]{min-width:100%;flex:1 1 100% !important}
}
"""
    extra = SHEET_CSS_DARK if is_dark() else SHEET_CSS_LIGHT
    _html("<style>" + base + SHEET_CSS + extra + "</style>")


def hard_reset():
    """सारा कैश (डेटा + ज़ोन) साफ़ — अगला स्कैन बिल्कुल ताज़ा।"""
    try:
        clear_cache()
    except Exception:                                          # noqa: BLE001
        pass
    try:
        _ZCACHE.clear()
    except Exception:                                          # noqa: BLE001
        pass
    for fn in (cached_scan, cached_frames, cached_caps, cached_fno):
        try:
            fn.clear()
        except Exception:                                      # noqa: BLE001
            pass
    st.session_state.pop("scan", None)


def reset_inputs():
    for k in list(st.session_state.keys()):
        if str(k).startswith("in_"):
            del st.session_state[k]


def preset_loose():
    """Pine default से ढीला (सिर्फ़ खोज के लिए): Validation के चेकबॉक्स बंद।"""
    for k, v in {"in_cbli": False, "in_form": False, "in_white": False, "in_reqvol": False,
                 "in_reqimb": False}.items():
        st.session_state[k] = v


# ---------------------------------------------------------------------------
# 1 · ऊपर की एक पंक्ति
# ---------------------------------------------------------------------------
def top_bar():
    c = cols([1.5, 2.0, .8, .85, 1.05, 1.15, .95])
    if st.session_state.get("in_band") not in BANDS:          # पुराना विकल्प (v33) → default Weekly
        st.session_state.pop("in_band", None)
    uni = c[0].selectbox("यूनिवर्स", UNIVERSES, key="in_uni", label_visibility="collapsed")
    tfs = c[1].multiselect("TF", list(ALL_TFS), default=list(ALL_TFS),
                           key="in_tfs", label_visibility="collapsed")
    side = c[2].selectbox("पक्ष", ["All", "Demand", "Supply"], key="in_side",
                          label_visibility="collapsed")
    status = c[3].selectbox("स्थिति", ["All"] + ALL_STATES, index=1, key="in_status",
                            label_visibility="collapsed")
    band = c[4].selectbox("EOD रेंज", BANDS, index=1, key="in_band",
                          label_visibility="collapsed")
    sym_txt = c[5].text_input("स्क्रिप्ट", key="in_symtxt", label_visibility="collapsed",
                              placeholder="RELIANCE, SBIN…")
    go = put(c[6], "button", "⚡ Scan", type="primary", key="btn_scan")
    return dict(universe=uni, tfs=tuple(tfs), side=side, status=status, band=band,
                symbols_txt=sym_txt.strip(), go=go)


# ---------------------------------------------------------------------------
# 2 · Pine v6 इनपुट
# ---------------------------------------------------------------------------
def pine_panel(slot=None):
    """Pine "Zone MTF (KTP rules)" v24 के इनपुट — वही क्रम, वही नाम, वही default।"""
    host = slot if slot is not None else st
    D = PineInputs()
    with host.expander("⚙️ इनपुट", expanded=False):
        b1, b2, b3, _ = st.columns([1, 1, 1.15, 3])
        put(b1, "button", "↺ Pine default", on_click=reset_inputs)
        put(b2, "button", "🔓 ढीला", on_click=preset_loose)
        put(b3, "button", "♻️ कैश साफ़", on_click=hard_reset)
        t0, t1, t2, t3, t4 = st.tabs(["0 · Capital · SL", "1 · Leg-in / Boring / Leg-out",
                                      "2 · Validation", "3 · Quality score", "4–6 · Display · MTF · Swing"])
        with t0:
            s1, s2, s3, s4 = st.columns(4)
            cap = s1.number_input("Account Capital (₹)", 1000.0, 1e9, D.accountCapital, 1000.0, key="in_cap")
            risk = s2.number_input("Risk % per trade (course: 0.5)", 0.01, 10.0, D.riskPct, 0.05, key="in_risk")
            rr = s3.number_input("Target RR (course minimum 1:5)", 1.0, 50.0, D.targetRR, 0.5, key="in_rr")
            ttype = s4.selectbox("Trader type", TRADER_TYPES, index=TRADER_TYPES.index(D.traderType), key="in_ttype",
                                 help="Auto = चार्ट TF से: ≤1m Aggressive · ≤5m Hourly · ≤30m Daily · <1D Weekly/Swing · 1D+ Monthly")
            s5, s6, s7, s8 = st.columns(4)
            bmode = s5.selectbox("SL buffer method", BUFFER_MODES, index=BUFFER_MODES.index(D.bufferMode), key="in_bmode")
            b_aggr = s6.number_input("Buffer % of DATR — Aggressive/Hourly", 0.0, 50.0, D.bufAggrPct, 0.5, key="in_baggr")
            b_daily = s7.number_input("Buffer % of DATR — Daily", 0.0, 50.0, D.bufDailyPct, 0.5, key="in_bdaily")
            b_swing = s8.number_input("Buffer % of DATR — Weekly/Monthly", 0.0, 50.0, D.bufSwingPct, 0.5, key="in_bswing")
            s9, s10, s11, _ = st.columns(4)
            slb = s9.number_input("SL Buffer × chart ATR (if 'Chart ATR ×' or DATR missing)", 0.0, 5.0,
                                  D.slBufferAtr, 0.05, key="in_slb")
            scanbars = s10.number_input("कितने बार पीछे (scanBars)", 200, 5000, D.scanBars, 100, key="in_scanbars")
            maxsyms = s11.number_input("अधिकतम स्क्रिप्ट (0 = सभी)", 0, 3000, 0, 50, key="in_maxsyms")
        with t1:
            c1, c2, c3, c4 = st.columns(4)
            atrp = c1.number_input("ATR Period (course: 14)", 1, 200, D.atrPeriod, 1, key="in_atr")
            minbc = c2.number_input("Min boring candles", 1, 4, D.minBaseCountInput, 1, key="in_minbc")
            maxbc = c3.number_input("Max boring candles (course: >4 = trap)", 1, 4, D.maxBaseCountInput, 1, key="in_maxbc")
            bo_atr = c4.number_input("Boring: TR must be ≤ ATR ×", 0.1, 5.0, D.boringAtrMult, 0.05, key="in_boatr")
            c5, c6, c7, c8 = st.columns(4)
            li_atr = c5.number_input("Leg-in: TR ≥ ATR ×", 0.0, 5.0, D.legInAtrMult, 0.05, key="in_liatr")
            li_body = c6.number_input("Leg-in: min body % (Filled = body > wick)", 0.0, 1.0, D.legInMinBody, 0.05, key="in_libody")
            li_cw = c7.number_input("Leg-in: max closing-side wick %", 0.0, 1.0, D.legInMaxCloseWick, 0.05, key="in_licw")
            li_base = c8.number_input("1-2-4: Leg-in ≥ boring ×", 1.0, 10.0, D.legInToBaseSizeMult, 0.1, key="in_libase")
            c9, c10, c11, c12 = st.columns(4)
            lo_base = c9.number_input("1-2-4: Leg-out ≥ boring ×", 1.0, 20.0, D.legOutToBaseSizeMult, 0.1, key="in_lobase")
            maxlo = c10.number_input("Max leg-out candles counted (course: 3)", 1, 3, D.maxLegOut, 1, key="in_maxlo")
            join_tol = c11.number_input("Leg-out 2/3: |open − prev close| ≤ ATR ×", 0.0, 2.0, D.legOutJoinTolAtr, 0.05, key="in_jointol")
            lo_atr = c12.number_input("First leg-out: TR ≥ ATR ×", 0.0, 5.0, D.legOutAtrMult, 0.05, key="in_loatr")
            c13, _, _, _ = st.columns(4)
            lo_wick = c13.number_input("First leg-out: max wick %", 0.0, 1.0, D.legOutMaxWick, 0.05, key="in_lowick")
        with t2:
            v1, v2, v3, v4 = st.columns(4)
            cbli = v1.checkbox("CBLI: reject if candle behind leg-in is opposite colour", D.useCBLI, key="in_cbli")
            cbli_body = v2.number_input("CBLI: max body vs leg-in body", 0.0, 5.0, D.cbliMaxBody, 0.05, key="in_cblibody")
            form = v3.checkbox("Formation of leg-out (DBR/RBD: leg-out open inside leg-in body)", D.useFormation,
                               key="in_form", help="कोई tolerance नहीं — open ठीक leg-in की body के अंदर")
            white = v4.checkbox("White area (leg-out body must not cover whole boring wick)", D.useWhiteArea, key="in_white")
            v5, v6, v7, v8 = st.columns(4)
            max_ent = v5.number_input("Max entries before zone is exhausted (course: 1–3)", 1, 10, D.maxEntries, 1, key="in_maxent")
            dist_mode = v6.selectbox("Distal (SL) line", DIST_MODES, index=DIST_MODES.index(D.distalMode), key="in_distmode")
            req_vol = v7.checkbox("Require leg-out volume > leg-in volume (optional)", D.reqVolume, key="in_reqvol")
            req_imb = v8.checkbox("Require imbalance: gap or close beyond leg-in extreme (optional)", D.reqImbalance, key="in_reqimb")
        with t3:
            st.caption("Quality score (0–100) सिर्फ़ क्रम के लिए — ज़ोन को नहीं रोकता। ★ HQ = दोनों HQ शर्तें पूरी।")
            q1, q2, q3, q4 = st.columns(4)
            vol_sma = q1.number_input("Volume SMA Period", 1, 200, D.volSmaPeriod, 1, key="in_volsma")
            hq_out = q2.number_input("HQ: leg-out TR ≥ leg-in TR ×", 0.0, 10.0, D.hqLegOutMult, 0.1, key="in_hqout")
            hq_in = q3.number_input("HQ: leg-in TR ≥ ATR ×", 0.0, 10.0, D.hqLegInAtr, 0.05, key="in_hqin")
            heavy = q4.number_input("Leg-out body-heavy %", 0.0, 1.0, D.bodyHeavyPct, 0.05, key="in_heavy")
        with t4:
            d1, d2, d3, d4 = st.columns(4)
            show_info = d1.checkbox("Show zone info label (entry, SL, T1, XRPT, qty)", D.showInfo, key="in_showinfo",
                                    help="टेबल में '➕ कॉलम' से Entry/SL/T1/XRPT/Qty दिखते हैं")
            show_mcap = d2.checkbox("Show market-cap check table", D.showMcap, key="in_showmcap")
            min_mcap = d3.number_input("Min market cap (₹ Cr)", 0.0, 1e7, D.minMcapCr, 1000.0, key="in_minmcap")
            showmtf = d4.checkbox("Show other-TF proximal lines (MTF गिनती)", D.showMTF, key="in_showmtf")
            d5, d6, d7, d8 = st.columns(4)
            f_dist = d5.checkbox("Hide distant MTF lines (display only)", D.filterDistantMTF, key="in_fdist")
            near = d6.number_input("Show lines within % of last price", 0.1, 100.0, D.mtfNearPct, 0.5, key="in_near")
            shadow = d7.checkbox("Shadow when prior zone behind / broken / swing broken", D.showShadow, key="in_shadow")
            flags = d8.checkbox("Show BASE BREAK / SWING labels", D.showFlags, key="in_flags")
            d9, d10, d11, _ = st.columns(4)
            sh_atr = d9.number_input("Shadow padding × ATR", 0.0, 1.0, D.shadowAtr, 0.01, key="in_shatr")
            swl = d10.number_input("Confirmed swing: bars on left", 1, 30, D.swingLeft, 1, key="in_swl",
                                   help="NSE अध्ययन (40 F&O stocks, 5m–1W): best = 6")
            swr = d11.number_input("Confirmed swing: bars on right", 1, 30, D.swingRight, 1, key="in_swr")

    inp = PineInputs(
        accountCapital=float(cap), riskPct=float(risk), targetRR=float(rr), traderType=str(ttype),
        bufferMode=str(bmode), bufAggrPct=float(b_aggr), bufDailyPct=float(b_daily), bufSwingPct=float(b_swing),
        slBufferAtr=float(slb),
        atrPeriod=int(atrp), minBaseCountInput=int(minbc), maxBaseCountInput=int(maxbc),
        boringAtrMult=float(bo_atr), legInAtrMult=float(li_atr), legInMinBody=float(li_body),
        legInMaxCloseWick=float(li_cw), legInToBaseSizeMult=float(li_base), legOutToBaseSizeMult=float(lo_base),
        maxLegOut=int(maxlo), legOutJoinTolAtr=float(join_tol), legOutAtrMult=float(lo_atr),
        legOutMaxWick=float(lo_wick),
        useCBLI=bool(cbli), cbliMaxBody=float(cbli_body), useFormation=bool(form), useWhiteArea=bool(white),
        maxEntries=int(max_ent), distalMode=str(dist_mode), reqVolume=bool(req_vol), reqImbalance=bool(req_imb),
        volSmaPeriod=int(vol_sma), hqLegOutMult=float(hq_out), hqLegInAtr=float(hq_in), bodyHeavyPct=float(heavy),
        showInfo=bool(show_info), showMcap=bool(show_mcap), minMcapCr=float(min_mcap),
        showMTF=bool(showmtf), filterDistantMTF=bool(f_dist), mtfNearPct=float(near),
        showShadow=bool(shadow), showFlags=bool(flags), shadowAtr=float(sh_atr),
        swingLeft=int(swl), swingRight=int(swr), scanBars=int(scanbars),
    ).sanitized()
    uo = dict(max_syms=int(maxsyms), peers=tuple(ALL_TFS), show_mcap=bool(show_mcap),
              min_mcap=float(min_mcap), show_info=bool(show_info))
    return inp, uo


# ---------------------------------------------------------------------------
# 3 · कैश + स्कैन
# ---------------------------------------------------------------------------
@st.cache_data(ttl=21600, show_spinner=False)
def cached_fno():
    try:
        u = live_fno_universe()
        return list(u) if u else list(FALLBACK_FNO)
    except Exception:                                          # noqa: BLE001
        return list(FALLBACK_FNO)


@st.cache_resource(ttl=3600, max_entries=4, show_spinner=False)
def cached_frames(symbols: tuple, tfs: tuple):
    return load_frames(list(symbols), list(tfs))


@st.cache_resource(ttl=1800, max_entries=4, show_spinner=False)
def cached_fut_frames(symbols: tuple, tfs: tuple):
    try:
        return load_future_frames(list(symbols), list(tfs))
    except Exception as e:                                     # noqa: BLE001
        _DHAN_STATUS["status"] = f"error: {type(e).__name__}"
        return {}


@st.cache_data(ttl=21600, show_spinner=False)
def cached_caps(symbols: tuple):
    try:
        return market_caps(list(symbols))
    except Exception:                                          # noqa: BLE001
        return {}


def run_scan(symbols, inp, opts):
    return cached_scan(tuple(symbols),
                       json.dumps(inp.__dict__, default=str, sort_keys=True),
                       json.dumps({k: (list(v) if isinstance(v, tuple) else v)
                                   for k, v in opts.items()}, default=str, sort_keys=True))


@st.cache_data(ttl=1800, show_spinner=False)
def cached_scan(symbols: tuple, inp_key: str, opts_key: str):
    inp = PineInputs(**json.loads(inp_key)).sanitized()
    return _scan_all(list(symbols), inp, json.loads(opts_key))


def _scan_all(symbols, inp, opts):
    tfs = list(opts["tfs"]) or list(ALL_TFS)
    need = list(dict.fromkeys(list(tfs) + ["75m", "1d", "1w", "1M"]))
    frames = cached_frames(tuple(symbols), tuple(need))
    fut_all = cached_fut_frames(tuple(symbols), tuple(tfs)) if all(dhan_creds()) else {}
    rows, snaps, atrs, ltps, errs = [], {}, {}, {}, {}
    rej_all: Dict[str, int] = {}
    created_all = 0
    jobs = [(s, frames[s]) for s in symbols if s in frames]
    for s in symbols:
        if s not in frames:
            errs[s] = "डेटा नहीं मिला"
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            futs = {ex.submit(scan_symbol, s, f, f.get("1d"), inp, 1e9, tfs, None,
                              fut_all.get(s)): s
                    for s, f in jobs}
            for fu in as_completed(futs):
                s = futs[fu]
                try:
                    r = fu.result()
                except Exception as e:                          # noqa: BLE001
                    errs[s] = f"{type(e).__name__}: {e}"
                    continue
                snaps[s] = r["snapshots"]
                atrs[s] = r["atr"]
                ltps[s] = r["ltp"]
                rows.extend(r["rows"])
                created_all += int(r.get("created", 0))
                for k, v in (r.get("rej") or {}).items():
                    rej_all[k] = rej_all.get(k, 0) + int(v)

    # Daily / Weekly / Monthly EOD candle का High+ATR … Low−ATR — तीनों हमेशा, फ़िल्टर _main में
    btf = BAND_TF.get(opts.get("band") or "")
    bands3: Dict[str, Dict[str, dict]] = {"1d": {}, "1w": {}, "1M": {}}
    for s in symbols:
        for t3 in bands3:
            try:
                b = eod_band(frames.get(s, {}).get(t3), t3, inp.atrPeriod)
            except Exception:                                    # noqa: BLE001
                b = None
            if b:
                bands3[t3][s] = b
    bands = bands3.get(btf, {}) if btf else {}

    df = pd.DataFrame(rows)
    if not df.empty:
        df["side_hi"] = df["side"].map(SIDE_HI)
        df["cat_hi"] = df["category"].map(CAT_HI).fillna(df["category"])
        df["created_ist"] = df["time"].map(
            lambda ms: datetime.fromtimestamp(ms / 1000, IST).strftime("%d-%b %H:%M") if ms else "—")
        df = df.sort_values(["dist_pct", "score"], ascending=[True, False]).reset_index(drop=True)
    return dict(rows=df, snaps=snaps, atr=atrs, ltp=ltps, errs=errs, bands=bands, bands3=bands3,
                band_tf=btf, at=datetime.now(IST), scanned=len(jobs),
                rej=rej_all, created=created_all, dhan=_DHAN_STATUS.get("status", ""))


# ---------------------------------------------------------------------------
# 4 · TradingView लिंक + शीट
# ---------------------------------------------------------------------------
def _tv_sym(sym: str) -> str:
    """TradingView NSE नाम: & और - की जगह _ (M&M → NSE:M_M, BAJAJ-AUTO → NSE:BAJAJ_AUTO)।"""
    sym = str(sym)
    if sym.startswith("NSE:"):
        ex, s2 = sym.split(":", 1)
        return ex + ":" + s2.replace("&", "_").replace("-", "_")
    return sym


def tv_url(symbol: str, tf: str) -> str:
    from urllib.parse import quote
    sym = _tv_sym(sym_meta(symbol)["cash"] or f"NSE:{symbol}")
    return f"https://www.tradingview.com/chart/?symbol={quote(sym)}&interval={TV_TF.get(str(tf), '15')}"


def fut_url(symbol: str, tf: str) -> str:
    from urllib.parse import quote
    sym = sym_meta(symbol)["fut"]
    if not sym:
        return tv_url(symbol, tf)
    return f"https://www.tradingview.com/chart/?symbol={quote(_tv_sym(sym))}&interval={TV_TF.get(str(tf), '15')}"


def _lerp(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _score_bg(sc: float) -> str:
    low, mid, high = (252, 232, 230), (254, 247, 224), (206, 234, 214)
    t = max(0.0, min(1.0, (float(sc) - 30.0) / 70.0))
    c = _lerp(low, mid, t / 0.5) if t <= 0.5 else _lerp(mid, high, (t - 0.5) / 0.5)
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _dist_bar(pct: float, rng: float) -> str:
    r = max(0.01, float(rng))
    w = max(3.0, min(100.0, float(pct) / r * 100.0))
    col = "#3fb950" if pct <= r * 0.2 else ("#d29922" if pct <= r * 0.5 else "#f85149")
    return (f'<span class="gs-track"><span class="gs-fill" style="width:{w:.0f}%;'
            f'background:{col}"></span></span><span class="gs-sm">{pct:.2f}%</span>')


_SORT_JS = r"""
<script>
(function(){
  function num(v){
    return (v !== null && v !== '' && /^[-+]?\d*\.?\d+$/.test(v)) ? parseFloat(v) : null;
  }
  function val(td){
    if(!td) return '';
    var v = td.getAttribute('data-v');
    return (v === null) ? td.textContent.trim() : v;
  }
  function wire(t){
    if(t.dataset.srtWired === '1') return;
    t.dataset.srtWired = '1';
    var ths = Array.prototype.slice.call(t.querySelectorAll('thead th'));
    var tb = t.querySelector('tbody');
    if(!tb) return;
    ths.forEach(function(th, idx){
      if(!th.hasAttribute('data-sort')) return;
      th.addEventListener('click', function(){
        var dir = th.classList.contains('s-asc') ? 'desc' : 'asc';
        ths.forEach(function(o){ o.classList.remove('s-asc','s-desc'); });
        th.classList.add(dir === 'asc' ? 's-asc' : 's-desc');
        var rows = Array.prototype.slice.call(tb.querySelectorAll('tr'));
        rows.sort(function(a, b){
          var av = val(a.cells[idx]), bv = val(b.cells[idx]);
          var an = num(av), bn = num(bv), c;
          if(an !== null && bn !== null){ c = an - bn; }
          else { c = String(av).localeCompare(String(bv), 'en', {numeric:true}); }
          return dir === 'asc' ? c : -c;
        });
        rows.forEach(function(r, i){
          tb.appendChild(r);
          var rn = r.cells[0];
          if(rn && rn.classList.contains('gs-rn')){
            rn.textContent = i;
            rn.setAttribute('data-v', i);
          }
        });
      });
    });
  }
  function boot(){ Array.prototype.forEach.call(document.querySelectorAll('table.gs'), wire); }
  boot(); setTimeout(boot, 250); setTimeout(boot, 1000);
})();
</script>
"""

_STATE_ORDER = {"Fresh": 0, "Tested": 1, "Exhausted": 2, "Broken": 3}


def _th(label: str, sortable: bool = True, dir0: str = "") -> str:
    if not sortable:
        return f"<th>{label}</th>"
    cls = "srt" + (f" s-{dir0}" if dir0 else "")
    d = f' data-dir="{dir0}"' if dir0 else ""
    return f'<th class="{cls}" data-sort="1"{d}>{label}</th>'


def _td(inner: str, dv=None, cls: str = "", style: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    st = f' style="{style}"' if style else ""
    if dv is None:
        return f"<td{c}{st}>{inner}</td>"
    if isinstance(dv, float):
        dv = f"{dv:.4f}"
    return f'<td{c}{st} data-v="{dv}">{inner}</td>'


def sheet_html(df: pd.DataFrame, show_mcap=True, min_mcap=50000.0, extra=False,
               range_pct: float = 3.0, max_rows: int = 600) -> str:
    """Google-Sheets जैसी टेबल — हर हेडर पर क्लिक करते ही उस कॉलम पर सॉर्ट (▲/▼)।"""
    if df is None or df.empty:
        return ""
    d = df.head(max_rows)
    heads = [_th("", sortable=False)]
    heads += [
        _th("Equity (Cash)"), _th("Current-Month Future"),
        _th("TF"), _th("Zone Side"), _th("Pattern"), _th("Status"), _th("EOD रेंज"),
        _th("Proximal (₹)"), _th("Distal (₹)"), _th("Current LTP"),
        _th("Distance", dir0="asc"),
    ]
    if extra:
        heads += [_th("FLIP"), _th("SWING BRK"), _th("Score /100"), _th("XRPT (1:x)"), _th("निशान"), _th("Multi-TF Highlight"),
                  _th("Entry"), _th("SL"), _th("T 1:3"), _th("T1"), _th("XRPT"), _th("Qty"), _th("Boring"),
                  _th("Leg-out"), _th("Entries"), _th("Pulse · Trend"),
                  _th("Range Low"), _th("Range High")]
        if show_mcap:
            heads += [_th("Mkt Cap (₹Cr)")]
    body = []
    for n, (_, r) in enumerate(d.iterrows(), start=0):
        meta = sym_meta(r["symbol"])
        u_cash = tv_url(r["symbol"], r["tf"])
        u_fut = fut_url(r["symbol"], r["tf"])
        fut_lbl = str(meta.get("fut") or "—").split(":")[-1] if meta.get("fut") else "—"
        dem = bool(r["demand"])
        dot = ('<span class="gs-dot gs-dot-d">●</span>' if dem
               else '<span class="gs-dot gs-dot-s">●</span>')
        side = (f'{dot} <span class="{"gs-dot-d" if dem else "gs-dot-s"}">'
                f'{"Demand" if dem else "Supply"}</span>')
        state = str(r["state"])
        mtf_n = int(r.get("mtf_n", 0) or 0)
        mtf_cell = (f'<span title="पुष्टि करने वाले TF: {r.get("mtf_tfs", "")}">'
                    f'<span class="gs-star">★</span> Multi-TF ({mtf_n}×)</span>'
                    if mtf_n >= 1 else '<span class="gs-sm">—</span>')
        ltp = float(r["ltp"]) if pd.notna(r.get("ltp")) else float("nan")
        ltp_txt = f"₹{ltp:,.2f}" if np.isfinite(ltp) else "—"
        cells = [
            _td(str(n), n, "gs-rn"),
            _td(f'<a href="{u_cash}" target="_blank" rel="noopener" '
                f'title="TradingView · {meta.get("cash")} · {r["tf"]}">{r["symbol"]}</a>',
                r["symbol"]),
            (_td(f'<a class="fut" href="{u_fut}" target="_blank" rel="noopener" '
                 f'title="TradingView · {meta.get("fut")} · {r["tf"]}">{fut_lbl}</a>', fut_lbl)
             if meta.get("fut") else _td('<span class="gs-sm">—</span>', "~")),
            _td(str(r["tf"]), TF_SECONDS.get(str(r["tf"]), 0)),
            _td(side, "Demand" if dem else "Supply"),
            _td(str(r["pattern"]), r["pattern"], "ctr"),
            _td(f'<span class="{_STATE_CLS.get(state, "")}">{state}</span>',
                _STATE_ORDER.get(state, 9)),
            _td(f'<b>{r.get("eod") or "—"}</b>', str(r.get("eod") or "~"), "ctr"),
            _td(f'{float(r["prox"]):,.2f}', float(r["prox"]), "num"),
            _td(f'{float(r["dist"]):,.2f}', float(r["dist"]), "num"),
            _td(ltp_txt, ltp if np.isfinite(ltp) else -1e18, "num"),
            _td(_dist_bar(float(r["dist_pct"]), range_pct), float(r["dist_pct"])),
        ]
        if extra:
            fl = bool(r.get("flip"))
            cells += [
                _td('<b style="color:#7F77DD">🔁 FLIP</b>' if fl else '<span class="gs-sm">—</span>', 1 if fl else 0, "ctr"),
                (_td(f'<b style="color:#1D9E75">{"🔺" if r["demand"] else "🔻"} SW-BRK</b>', 1, "ctr") if bool(r.get("sw_brk"))
                 else _td('<span class="gs-sm">—</span>', 0, "ctr")),
                _td(f'{int(r["score"])}', int(r["score"]), "ctr",
                    style=f"background:{_score_bg(float(r['score']))};color:#111;font-weight:700",),
                _td(f'{"⚡ " if r.get("best") else ""}1:{float(r.get("xrr", 0)):.1f}',
                    float(r.get("xrr", 0)), "ctr" + (" gs-star" if r.get("best") else "")),
                _td(f'<span class="gs-star">{r.get("tags") or ""}</span>' if r.get("tags") else '<span class="gs-sm">—</span>',
                    str(r.get("tags") or "~")),
                _td(mtf_cell, mtf_n),
                _td(f'{float(r["entry"]):,.2f}', float(r["entry"]), "num"),
                _td(f'{float(r["sl"]):,.2f}', float(r["sl"]), "num"),
                _td(f'<b>{float(r["t3"]):,.2f}</b>', float(r["t3"]), "num"),
                _td(f'{float(r["tp"]):,.2f}', float(r["tp"]), "num"),
                _td(f'{float(r["t5"]):,.2f}', float(r["t5"]), "num"),
                _td(f'{int(r["qty"])}', int(r["qty"]), "num"),
                _td(f'{int(r["base_count"])}', int(r["base_count"]), "ctr"),
                _td(f'{int(r.get("leg_outs", 1))}', int(r.get("leg_outs", 1)), "ctr"),
                _td(f'{int(r.get("entries", 0))}', int(r.get("entries", 0)), "ctr"),
                _td(f'{r.get("trend_tf", "")} {r.get("pulse", "")} {r.get("trend", "")}',
                    str(r.get("pulse", "")), "ctr " + ("gs-ok" if r.get("action_ok") else "gs-sm")),
                (_td(f'{float(r["band_low"]):,.2f}', float(r["band_low"]), "num gs-sm")
                 if pd.notna(r.get("band_low")) else _td("—", -1e18, "num gs-sm")),
                (_td(f'{float(r["band_high"]):,.2f}', float(r["band_high"]), "num gs-sm")
                 if pd.notna(r.get("band_high")) else _td("—", -1e18, "num gs-sm")),
            ]
            if show_mcap:
                mv = r.get("mcap_cr", float("nan"))
                if mv is None or (isinstance(mv, float) and pd.isna(mv)):
                    cells.append(_td('<span class="gs-sm">—</span>', -1e18, "num gs-sm"))
                else:
                    ok = float(mv) >= float(min_mcap)
                    cells.append(_td(
                        f'{float(mv):,.0f} <span class="{"gs-ok" if ok else "gs-no"}">'
                        f'{"✓" if ok else "✗"}</span>', float(mv), "num"))
        body.append("<tr>" + "".join(cells) + "</tr>")
    return ('<div class="gs-wrap"><div class="gs-scroll"><table class="gs">'
            f'<thead><tr>{"".join(heads)}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div></div>' + _SORT_JS)


def zone_table(df: pd.DataFrame, show_mcap=True, min_mcap=50000.0) -> pd.DataFrame:
    d = pd.DataFrame({
        "स्क्रिप्ट": df["symbol"],
        "TF": df["tf"],
        "पक्ष": df["side_hi"],
        "पैटर्न": df["pattern"],
        "बेस": df["base_count"].map(lambda b: f"{int(b)} · {_GRADE_HI.get(int(b), '')}"),
        "Proximal": df["prox"].round(2),
        "एंट्री": df["entry"].round(2),
        "डिस्टल": df["dist"].round(2),
        "SL": df["sl"].round(2),
        "T 1:3": df["t3"].round(2),
        "T1": df["tp"].round(2),
        "दूरी %": df["dist_pct"].round(2),
        "FLIP": df["flip"].map(lambda v: "🔁 FLIP" if v else "") if "flip" in df.columns else "",
        "SWING BRK": df["sw_brk"].map(lambda v: "SW-BRK" if v else "") if "sw_brk" in df.columns else "",
        "स्कोर /100": df["score"].astype(int),
        "निशान": df["tags"].fillna(""),
        "फ़ैसला": df["verdict"],
        "XRPT": df["xrpt"].round(2),
        "XRPT (1:x)": df["xrr"].round(1),
        "★ HQ": df["hq"].map(lambda v: "★" if v else ""),
        "Leg-out": df["leg_outs"].astype(int),
        "Entries": df["entries"].astype(int),
        "Pulse·Trend": df["pulse"].astype(str) + " " + df["trend"].astype(str),
        "MTF": df["mtf_n"].map(lambda n: f"★{int(n)}" if n else "–"),
        "स्थिति": df["state"],
        "मात्रा": df["qty"].astype(int),
        "बना (IST)": df["created_ist"],
    })
    return d


def mtf_panel(snap: dict, tfs, peers):
    rows = []
    for tf in tfs:
        s = snap.get(tf) or {}
        rows.append({
            "TF": tf,
            "मांग proximal": s.get("demand"),
            "आपूर्ति proximal": s.get("supply"),
            "MTF में गिना": "✓" if tf in peers else "",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5 · यूनिवर्स
# ---------------------------------------------------------------------------
def _diag_text(res: dict) -> str:
    rej = res.get("rej") or {}
    errs = res.get("errs") or {}
    out = ""
    bad = {k: v for k, v in errs.items() if v != "डेटा नहीं मिला"}
    if bad:
        out += f" · ⚠️ {len(bad)} स्टॉक में त्रुटि: " + ", ".join(f"{k} ({v[:60]})" for k, v in list(bad.items())[:2])
    if rej:
        top = sorted(rej.items(), key=lambda kv: -kv[1])[:3]
        out += " · सबसे ज़्यादा reject: " + ", ".join(f"{k} ({v})" for k, v in top)
    return out


def pick_symbols(tb: dict):
    txt = (tb.get("symbols_txt") or "").strip().replace("/", ",")
    if txt:
        return [s.strip().upper() for s in txt.replace("\n", ",").split(",") if s.strip()]
    uni = tb["universe"]
    if uni == "NIFTY 50":
        return list(dict.fromkeys(NIFTY50))
    if uni == "Global Instruments":
        return list(dict.fromkeys(GLOBAL_SYMBOLS))
    if uni == "Futures + Global":
        return list(dict.fromkeys(cached_fno() + GLOBAL_SYMBOLS))
    return list(dict.fromkeys(cached_fno()))


# ---------------------------------------------------------------------------
# 6 · MAIN
# ---------------------------------------------------------------------------
REQ_TXT = """streamlit
yfinance
pandas
numpy
plotly
requests"""


def deps_guard() -> bool:
    missing = []
    if yf is None:
        missing.append("yfinance")
    if not missing:
        return True
    st.error("### ⚠️ ज़रूरी पैकेज इंस्टॉल नहीं हैं: " + ", ".join(missing))
    st.markdown("GitHub repo में `requirements.txt` ठीक करके **Manage app → Reboot app**:")
    st.code(REQ_TXT, language="text")
    return False


def main():
    """कुछ भी गड़बड़ हो तो स्क्रीन खाली न दिखे — पूरा विवरण दिखाएँ।"""
    try:
        _main()
    except Exception:                                          # noqa: BLE001
        st.error("⚠️ ऐप में त्रुटि — नीचे पूरा विवरण (यह पंक्ति स्क्रीनशॉट के साथ भेजें)")
        st.code(traceback.format_exc(), language="text")


def _main():
    st.caption(f"build {APP_BUILD} · streamlit {st.__version__}")
    css()
    if not deps_guard():
        return

    tb = top_bar()
    ctrl = cols([.01, .85, .7, .7, 4.9])
    inp, uo = pine_panel(None)          # ⚙️ इनपुट पूरी चौड़ाई में (tablet पर साफ़ दिखे)

    # ── स्कैन ────────────────────────────────────────────────────────────
    if tb["go"]:
        syms = pick_symbols(tb)
        lim = int(uo.get("max_syms") or 0)
        if lim and len(syms) > lim:
            syms = syms[:lim]
        if not syms:
            st.error("कोई स्क्रिप्ट नहीं चुनी — यूनिवर्स चुनें या स्क्रिप्ट बॉक्स में नाम लिखें।")
        else:
            opts = dict(tfs=tb["tfs"], peers=tuple(ALL_TFS), band=tb["band"])
            with st.spinner(f"{len(syms)} स्क्रिप्ट · {len(tb['tfs'])} TF स्कैन हो रहा है…"):
                res = run_scan(syms, inp, opts)
            res.update(inputs=inp, opts=opts, ui=uo, symbols=syms)
            st.session_state["scan"] = res

    res = st.session_state.get("scan")
    if not res:
        st.info("यूनिवर्स चुनें और **⚡ Scan** दबाएँ — या स्क्रिप्ट बॉक्स में नाम लिखें "
                "(जैसे `JSWSTEEL, RELIANCE`)। पहला स्कैन धीमा होता है, फिर कैश से तेज़।")
        return

    df_all: pd.DataFrame = res["rows"]
    uo = res.get("ui", uo)
    if df_all is None or df_all.empty:
        st.warning("इन सेटिंग पर अभी कोई जीवित (Fresh/Tested) ज़ोन नहीं है। सबसे ज़्यादा ज़ोन ये रोकते हैं: "
                   "1-2-4 (leg-out ≥ boring ×), CBLI, Formation, White area, Volume/Imbalance। "
                   "⚙️ इनपुट → 🔓 ढीला दबाएँ (Validation के चेकबॉक्स बंद हो जाएँगे)।" + _diag_text(res))
        return

    # ── स्कैन रेंज: EOD candle का High+ATR14 … Low−ATR14 बैंड ──
    bands3 = res.get("bands3") or {}
    sel_band = tb.get("band") or "Weekly (EOD)"                 # ऊपर जो रेंज चुनी है, वही तुरंत लागू
    n_before = len(df_all)
    if bands3:
        def _eod_tags(r):
            tags, blo, bhi = [], None, None
            for t3 in ("1d", "1w", "1M"):
                b = (bands3.get(t3) or {}).get(r["symbol"])
                if b and b["low"] <= float(r["prox"]) <= b["high"]:
                    tags.append(BAND_LETTER[t3])
                    if blo is None:
                        blo, bhi = b["low"], b["high"]
            return pd.Series({"eod": " ".join(tags), "band_low": blo, "band_high": bhi})
        df_all = pd.concat([df_all.reset_index(drop=True), df_all.apply(_eod_tags, axis=1).reset_index(drop=True)], axis=1)
        want = {"Daily (EOD)": "D", "Weekly (EOD)": "W", "Monthly (EOD)": "M"}.get(sel_band)
        if want:
            df_all = df_all[df_all["eod"].str.contains(want)].copy()
    if df_all.empty:
        st.warning(f"{n_before} ज़ोन मिले, पर कोई भी **{sel_band}** रेंज (EOD candle High+ATR … Low−ATR) के अंदर नहीं है। "
                   "ऊपर 'EOD रेंज' में Monthly चुनें या ⚙️ इनपुट → 🔓 ढीला दबाएँ।" + _diag_text(res))
        return

    f = df_all.copy()
    if tb.get("tfs"):                                           # ऊपर चुने TF ही दिखें
        f = f[f["tf"].isin(list(tb["tfs"]))]
    if tb["side"] != "All":
        f = f[f["side"] == tb["side"]]
    if tb["status"] != "All":
        f = f[f["state"] == tb["status"]]

    with ctrl[1]:
        extra_cols = st.toggle("➕ कॉलम", value=bool(uo.get("show_info", True)), key="in_extra")
    if not _JS_SORT:
        with ctrl[4]:
            s1, s2 = st.columns([2.3, 1])
            _sc = s1.selectbox("क्रम", SORT_COLS, key="srt_col",
                               label_visibility="collapsed")
            _sd = s2.selectbox("दिशा", ["▲", "▼"], key="srt_dir",
                               label_visibility="collapsed")
            f = sort_df(f, _sc, _sd == "▼")
    if extra_cols and not f.empty:
        try:
            f["mcap_cr"] = f["symbol"].map(cached_caps(tuple(sorted(f["symbol"].unique())[:120])))
        except Exception:                                        # noqa: BLE001
            f["mcap_cr"] = np.nan
    tbl = zone_table(f)
    put(ctrl[2], "download_button", "⬇️ CSV",
        tbl.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"zone_mtf_{datetime.now(IST):%Y%m%d_%H%M}.csv", mime="text/csv")
    put(ctrl[3], "download_button", "⬇️ TSV",
        tbl.to_csv(index=False, sep="\t").encode("utf-8-sig"),
        file_name=f"zone_mtf_{datetime.now(IST):%Y%m%d_%H%M}.tsv",
        mime="text/tab-separated-values")

    st.caption(f"**{sel_band} रेंज के अंदर ज़ोन: {len(f)}** · TF: {', '.join(tb.get('tfs') or ALL_TFS)}")
    if not f.empty:
        try:
            scale = float(max(1.0, f["dist_pct"].quantile(0.90)))
        except Exception:                                        # noqa: BLE001
            scale = 3.0
        _html(sheet_html(f, uo.get("show_mcap", True), float(uo.get("min_mcap", 50000.0)), extra=extra_cols,
                         range_pct=scale), js=_JS_SORT)

    # ── MTF (बंद खंड) ───────────────────────────────────────────────────
    with st.expander("🔗 MTF", expanded=False):
        if res["snaps"]:
            sym2 = st.selectbox("स्क्रिप्ट", sorted(res["snaps"].keys()), key="m_sym")
            mdf = mtf_panel(res["snaps"].get(sym2, {}), list(res["opts"]["tfs"]), set(uo["peers"]))
            stretch(st.dataframe, mdf, hide_index=True, height=460,
                    column_config={
                        "मांग proximal": st.column_config.NumberColumn(format="%.2f"),
                        "आपूर्ति proximal": st.column_config.NumberColumn(format="%.2f"),
                    })


main()
