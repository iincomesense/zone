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
import io
import json
import math
import os
import pickle
import time
import traceback
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

TTL = {"1m": 300, "5m": 900, "15m": 900, "30m": 1200, "60m": 1800,
       "1d": 3600, "1wk": 21600, "1mo": 21600}

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
    cached = _load(key, TTL.get(base, 300))
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
#  भाग 3 · ज़ोन इंजन — Pine v6 "Zone MTF" (© iincomesense) का हूबहू पोर्ट
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
#           f_candidate (bc 1..3) · leg-out hierarchy · CLV pattern ·
#           imbalance/gap · score · Fresh → Tested → Broken · MTF snapshot
# ═══════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# 1. INPUTS — Pine के input.* बिल्कुल वैसे ही, वही default
# ---------------------------------------------------------------------------
@dataclass
class PineInputs:
    # ---- मूल inputs ----
    accountCapital: float = 25000.0
    riskPct: float = 0.5
    targetRR: float = 5.0
    slBufferAtr: float = 0.1
    atrPeriod: int = 14
    volSmaPeriod: int = 20
    legOutTrMult: float = 1.2
    legOutMinTrRatio: float = 1.0
    hqLegOutTrMult: float = 2.0
    hqLegInAtrMult: float = 1.5
    maxBaseAtrMult: float = 1.0
    maxWickPct: float = 0.30
    minBaseCountInput: int = 1
    maxBaseCountInput: int = 3
    legInMinAtrMult: float = 1.0
    minClvPct: float = 0.60
    legInToBaseSizeMult: float = 2.0
    legInMinBodyPct: float = 0.60
    useImbalance: bool = True
    maxImbalanceMult: float = 1.0
    relaxGapCapOvernight: bool = True
    genuineGapBonus: int = 10
    overnightGapBonus: int = 15
    rejectOppositeCoverPct: float = 0.50
    minValidScore: int = 40
    hqScoreThreshold: int = 90
    legOutBodyHeavyPct: float = 0.60
    testedLegOutRetracePct: float = 1.00
    maxTestedCount: int = 1

    # ---- parity inputs (Pine में हैं, पर zone validation में निष्क्रिय) ----
    legOutToLegInBodyMult: float = 1.0
    baseBoringMaxBodyPct: float = 0.55
    eodHighBufferPct: float = 10.0
    eodLowBufferPct: float = 10.0
    scanAfterCandleComplete: bool = True
    scanMonthlyOnce: bool = True
    scanWeeklyOnce: bool = True
    scanDailyOnce: bool = True
    enableClosingWickCheck: bool = True
    legInMinClosingWickPct: float = 0.1
    enableLegOutCoverCheck: bool = True
    legOutMaxCoverPct: float = 90.0
    enableHQBaseColourCheck: bool = True
    hqBaseColourProbabilityPct: float = 90.0
    enableWhiteAreaCheck: bool = True

    # ---- MTF / shadows ----
    showMTF: bool = True
    filterDistantMTF: bool = True
    mtfNearPct: float = 8.0
    showShadow: bool = True
    showFlags: bool = True
    shadowAtr: float = 0.08
    swingLeft: int = 3
    swingRight: int = 3
    maxStored: int = 220

    # ---- app layer (Pine में नहीं — स्कैनर के लिए) ----
    scanBars: int = 500

    def sanitized(self) -> "PineInputs":
        d = asdict(self)
        d["minBaseCountInput"] = int(max(1, self.minBaseCountInput))
        d["maxBaseCountInput"] = int(max(1, self.maxBaseCountInput))
        d["atrPeriod"] = int(max(1, self.atrPeriod))
        d["volSmaPeriod"] = int(max(1, self.volSmaPeriod))
        d["maxStored"] = int(max(10, min(self.maxStored, 240)))
        d["scanBars"] = int(max(200, min(self.scanBars, 5000)))
        return PineInputs(**d)

    # Pine: minBaseCount = max(1, min(minInput, maxInput)); maxBaseCount = min(maxInput, 3)
    @property
    def minBaseCount(self) -> int:
        return max(1, min(int(self.minBaseCountInput), int(self.maxBaseCountInput)))

    @property
    def maxBaseCount(self) -> int:
        return min(int(self.maxBaseCountInput), 3)


HARD_MAX_BASE_COUNT = 3


# ---------------------------------------------------------------------------
# 2. छोटे helpers — Pine: ta.rma / ta.sma / true_range / wick_pct / body_pct
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


def _wick_pct(o, h, l, c):
    r = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        w = ((h - np.maximum(o, c)) + (np.minimum(o, c) - l)) / r
    return np.where(r == 0, 0.0, w)


def _body_pct(o, h, l, c):
    r = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        b = np.abs(c - o) / r
    return np.where(r == 0, 0.0, b)


def _grade(bc: int) -> str:
    return {1: "Great", 2: "Very good", 3: "Good"}.get(bc, "So-so")


_GRADE_HI = {1: "बेहतरीन", 2: "बहुत अच्छा", 3: "अच्छा"}


# ---------------------------------------------------------------------------
# 3. SERIES — एक symbol+TF के arrays (position = absolute bar index)
# ---------------------------------------------------------------------------
class _S:
    __slots__ = ("o", "h", "l", "c", "v", "t", "dow", "day", "n", "tr", "atr",
                 "vol_sma", "wp", "bp", "index", "filled", "nb_cum", "overnight",
                 "_clv_bull", "_clv_bear")

    def __init__(self, df: pd.DataFrame):
        self.o = df["open"].to_numpy(dtype="float64")
        self.h = df["high"].to_numpy(dtype="float64")
        self.l = df["low"].to_numpy(dtype="float64")
        self.c = df["close"].to_numpy(dtype="float64")
        self.v = (df["volume"].to_numpy(dtype="float64") if "volume" in df.columns
                  else np.zeros(len(df)))
        idx = pd.DatetimeIndex(df.index)
        self.index = idx
        vals = idx.asi8
        self.t = vals // 1_000_000
        try:
            wall = idx.tz_localize(None).asi8 if idx.tz is not None else vals
        except Exception:                                      # noqa: BLE001
            wall = vals
        day_id = wall // 86_400_000_000_000
        self.dow = ((day_id + 3) % 7).astype("int64")          # 1970-01-01 = Thu
        self.day = np.concatenate(([0], np.cumsum(np.diff(day_id) != 0))).astype("int64")
        self.n = len(df)

    def prepare(self, inp: PineInputs) -> None:
        self.tr = _true_range(self.h, self.l, self.c)
        self.atr = _rma(self.tr, inp.atrPeriod)
        self.vol_sma = _sma(self.v, inp.volSmaPeriod)
        self.wp = _wick_pct(self.o, self.h, self.l, self.c)
        self.bp = _body_pct(self.o, self.h, self.l, self.c)
        # Pine: isOvernightGap() => dayofweek != dayofweek[1] or time - time[1] > 86400000
        prev_dow = np.concatenate(([np.nan], self.dow[:-1]))
        prev_t = np.concatenate(([np.nan], self.t[:-1]))
        self.overnight = (self.dow != prev_dow) | ((self.t - prev_t) > 86_400_000)
        # तेज़ी के लिए vectorized prefilter
        rng = self.h - self.l
        clv_bull = np.where(rng > 0, (self.c - self.l) / np.where(rng > 0, rng, 1), 0.0)
        clv_bear = np.where(rng > 0, (self.h - self.c) / np.where(rng > 0, rng, 1), 0.0)
        self.filled = ((rng > 0) & (self.bp >= inp.legInMinBodyPct)
                       & np.isfinite(self.atr))
        boring_ok = self.tr <= inp.maxBaseAtrMult * self.atr
        self.nb_cum = np.concatenate(([0], np.cumsum(~boring_ok))).astype("int64")
        self._clv_bull, self._clv_bear = clv_bull, clv_bear


# ---------------------------------------------------------------------------
# 4. CANDIDATE — Pine का f_candidate(bc)
# ---------------------------------------------------------------------------
def _candidate(p: int, bc: int, S: _S, I: PineInputs):
    """पूरी तरह Pine के f_candidate जैसा। bar p = leg-out (index 0)।"""
    li = p - (bc + 1)                     # leg-in
    pi = p - (bc + 2)                     # leg-in के पीछे वाली candle
    if li < 0 or pi < 0:
        return None
    if not np.isfinite(S.atr[li]):
        return None
    o, h, l, c, v = S.o, S.h, S.l, S.c, S.v
    in_tr = S.tr[li]
    in_low, in_high, in_close = l[li], h[li], c[li]
    in_vol = v[li]
    in_range = in_high - in_low
    in_bull = in_close > o[li]
    in_bear = o[li] > in_close
    # inRange == 0 or body_pct(li) < legInMinBodyPct
    if in_range == 0 or S.bp[li] < I.legInMinBodyPct:
        return None
    # opposite + overlap cover → reject
    opposite = (in_bull and o[pi] > c[pi]) or (in_bear and c[pi] > o[pi])
    prev_hi = max(o[pi], c[pi])
    prev_lo = min(o[pi], c[pi])
    overlap = max(0.0, min(prev_hi, in_high) - max(prev_lo, in_low))
    if opposite and overlap / in_range >= I.rejectOppositeCoverPct:
        return None
    bull_clv = (in_close - in_low) / in_range
    bear_clv = (in_high - in_close) / in_range

    # ---- boring base: b = 1..bc (bars ago) ----
    base_ok = True
    base_tr = 0.0
    base_high, base_low = -1.0, 1e9
    for b in range(1, bc + 1):
        i = p - b
        if i < 0 or not np.isfinite(S.atr[i]):
            base_ok = False
            break
        bt = S.tr[i]
        if bt > I.maxBaseAtrMult * S.atr[i]:
            base_ok = False
            break
        base_tr = max(base_tr, bt)
        base_high = max(base_high, h[i])
        base_low = min(base_low, l[i])
    if not base_ok or base_tr == 0:
        return None

    # leg-in vs base
    eff_mult = 1.5 if bc == 1 else I.legInToBaseSizeMult
    if in_tr < eff_mult * base_tr or not (in_tr >= I.legInMinAtrMult * S.atr[li]):
        return None

    # ---- leg-out (bar p) ----
    out_tr = S.tr[p]
    demand = c[p] > o[p]
    supply = o[p] > c[p]
    if not (demand or supply):
        return None
    explosive = out_tr >= I.legOutTrMult * S.atr[p]
    wick_ok = S.wp[p] <= I.maxWickPct
    hierarchy = (out_tr >= I.legOutMinTrRatio * in_tr) and (in_tr > base_tr)
    volume_ok = v[p] > in_vol
    overnight = bool(S.overnight[p])

    gap = False
    imbalance = True
    if I.useImbalance:
        if demand:
            gap = l[p] > base_high
            imbalance = gap or (c[p] > in_high)
        else:
            gap = h[p] < base_low
            imbalance = gap or (c[p] < in_low)
    body_engulf = min(o[p], c[p]) <= base_low and max(o[p], c[p]) >= base_high
    if body_engulf and not gap:
        return None

    rbr = in_bull and bull_clv >= I.minClvPct and demand
    dbr = in_bear and bear_clv >= I.minClvPct and demand
    dbd = in_bear and bear_clv >= I.minClvPct and supply
    rbd = in_bull and bull_clv >= I.minClvPct and supply
    if not ((rbr or dbr or dbd or rbd) and explosive and wick_ok and hierarchy
            and volume_ok and imbalance):
        return None

    # ---- score ----
    score = 0
    if bc == 1:
        score += 15
    if in_tr >= I.hqLegInAtrMult * S.atr[li]:
        score += 10
    if out_tr >= I.hqLegOutTrMult * in_tr:
        score += 15
    if in_tr >= 2.0 * base_tr and out_tr >= 2.0 * in_tr:
        score += 15
    if v[p] > S.vol_sma[p]:
        score += 10
    out_range = h[p] - l[p]
    body_pos = (((c[p] - l[p]) if demand else (h[p] - c[p])) / out_range) if out_range > 0 else 0.0
    if demand:
        if dbr:
            if body_pos >= 0.80 or S.bp[p] >= I.legOutBodyHeavyPct:
                score += 15
        else:
            if body_pos >= 0.80:
                score += 15
    else:
        if body_pos >= 0.80:
            score += 15
    opposite_base = False
    for b in range(1, bc + 1):
        i = p - b
        if (demand and o[i] > c[i]) or (supply and c[i] > o[i]):
            opposite_base = True
            break
    if opposite_base:
        score += 10
    score += 10
    if gap:
        score += I.genuineGapBonus
    if overnight and gap:
        score += I.overnightGapBonus
    if score < I.minValidScore:
        return None

    prox = base_high if demand else base_low
    dist = base_low if demand else base_high
    mid = ((h[p] - I.testedLegOutRetracePct * (h[p] - l[p])) if demand
           else (l[p] + I.testedLegOutRetracePct * (h[p] - l[p])))
    return dict(
        valid=True, bc=bc, demand=demand, prox=float(prox), dist=float(dist), mid=float(mid),
        score=int(score),
        pattern="RBR" if rbr else ("DBR" if dbr else ("DBD" if dbd else "RBD")),
        category="Continuation" if (rbr or dbd) else "Reversal",
        leg_in_tr=float(in_tr), leg_out_tr=float(out_tr),
        out_high=float(h[p]), out_low=float(l[p]), overnight=overnight,
        base_high=float(base_high), base_low=float(base_low), gap=bool(gap),
    )


# ---------------------------------------------------------------------------
# 5. ENGINE — zones + states + MTF snapshot (Pine का chart engine + f_mtf_engine)
# ---------------------------------------------------------------------------
STATE_FRESH, STATE_TESTED, STATE_BROKEN = "Fresh", "Tested", "Broken"


def engine(df: pd.DataFrame, I: PineInputs, tf: str, htf_daily: Optional[pd.DataFrame] = None,
           return_all: bool = False, want_snapshot: bool = True) -> List[dict]:
    """Pine के chart engine जैसा — हर बार पर नया ज़ोन, फिर state update।

    return_all=True → Broken ज़ोन भी लौटें (गिनती/दिखाने के लिए)।
    """
    S = _S(df)
    S.prepare(I)
    n = S.n
    if n < 20:
        return []
    atr_p = max(I.atrPeriod, I.maxBaseCount + 3, 11)
    start = max(0, n - max(I.scanBars, 50))

    zones: List[dict] = []          # chart zones (dict)
    mtf: List[dict] = []            # MTF zones (separate list, Pine जैसा)
    tf_sec = TF_SECONDS.get(tf, 0)
    snap_prev = {"demand": None, "supply": None, "demand_time": 0, "supply_time": 0}

    for p in range(start, n):
        # ---------- नया ज़ोन ----------
        if p >= atr_p and np.isfinite(S.atr[p]):
            for bc in range(I.minBaseCount, I.maxBaseCount + 1):
                li = p - (bc + 1)
                if li < 0 or not S.filled[li]:
                    continue
                bl = p - bc + 1                     # सबसे पहला base bar? (p-bc … p-1)
                if S.nb_cum[p] - S.nb_cum[bl - 1 if bl - 1 >= 0 else 0] != 0:
                    continue
                cand = _candidate(p, bc, S, I)
                if cand is None:
                    continue
                # duplicate: same side & |old.prox - prox| < atr*0.25
                dup = False
                checked = 0
                for old in reversed(zones):
                    if old["state"] == STATE_BROKEN:
                        continue
                    if old["demand"] == cand["demand"] and abs(old["prox"] - cand["prox"]) < S.atr[p] * 0.25:
                        dup = True
                        break
                    checked += 1
                    if checked >= 11:
                        break
                if not dup:
                    sl = (cand["dist"] - I.slBufferAtr * S.atr[p]) if cand["demand"] \
                        else (cand["dist"] + I.slBufferAtr * S.atr[p])
                    risk = abs(cand["prox"] - sl)
                    tp = (cand["prox"] + risk * I.targetRR) if cand["demand"] \
                        else (cand["prox"] - risk * I.targetRR)
                    qty = int(math.floor(I.accountCapital * I.riskPct / 100.0 / risk)) if risk > 0 else 0
                    z = dict(
                        demand=cand["demand"], side="Demand" if cand["demand"] else "Supply",
                        prox=cand["prox"], dist=cand["dist"], mid=cand["mid"],
                        sl=float(sl), tp=float(tp), risk=float(risk), qty=qty,
                        score=cand["score"], hq=cand["score"] >= I.hqScoreThreshold,
                        pattern=cand["pattern"], category=cand["category"],
                        base_count=cand["bc"], grade=_grade(cand["bc"]),
                        grade_hi=_GRADE_HI.get(cand["bc"], "ठीक-ठाक"),
                        leg_in_tr=cand["leg_in_tr"], leg_out_tr=cand["leg_out_tr"],
                        gap=cand["gap"], overnight=cand["overnight"],
                        base_high=cand["base_high"], base_low=cand["base_low"],
                        state=STATE_FRESH, touches=1 if False else 0,
                        created_bar=p, created_day=int(S.day[p]), bar=p,
                        left_bar=p - cand["bc"] - 1, time=int(S.t[p]),
                        broken_at=None, tf=tf, tf_sec=tf_sec, symbol=None,
                    )
                    zones.append(z)
                    if len(zones) > I.maxStored:
                        zones.pop(0)          # Pine: array.shift()
                    # MTF list (Pine का f_mtf_engine)
                    mtf.append(dict(demand=cand["demand"], prox=cand["prox"], dist=cand["dist"],
                                    state=STATE_FRESH, touches=0, created_time=int(S.t[p]),
                                    created_idx=p))
                break            # found → bc loop बंद

        # ---------- state update (chart) ----------
        for z in zones:
            if z["state"] == STATE_BROKEN:
                continue
            crossed = (S.l[p] <= z["dist"]) if z["demand"] else (S.h[p] >= z["dist"])
            touched = (p > z["created_bar"]) and ((S.l[p] <= z["prox"]) if z["demand"]
                                                  else (S.h[p] >= z["prox"]))
            if crossed:
                z["state"] = STATE_BROKEN
                z["broken_at"] = p
            elif touched:
                z["state"] = STATE_TESTED
                z["touches"] += 1

        # ---------- state update (MTF) ----------
        for j in range(len(mtf) - 1, -1, -1):
            z = mtf[j]
            crossed = (S.l[p] <= z["dist"]) if z["demand"] else (S.h[p] >= z["dist"])
            touched = (p > z["created_idx"]) and ((S.l[p] <= z["prox"]) if z["demand"]
                                                  else (S.h[p] >= z["prox"]))
            if crossed:
                z["state"] = STATE_BROKEN
            elif touched:
                z["state"] = STATE_TESTED
                z["touches"] += 1
            if z["state"] == STATE_BROKEN:
                mtf.pop(j)

        # Pine f_mtf_confirmed() → previous bar का मान
        if p == n - 2:
            snap_prev = _snap_of(mtf)

    # अंतिम snapshot (n-1 बार के बाद) भी रखें
    snap_last = _snap_of(mtf)
    out = zones if return_all else [z for z in zones if z["state"] != STATE_BROKEN]
    for z in out:
        z["ltp"] = float(S.c[-1])
        z["dist_pct"] = abs(z["prox"] - z["ltp"]) / z["ltp"] * 100.0 if z["ltp"] else None
        z["age_bars"] = (n - 1) - z["created_bar"]
        z["in_zone"] = bool((S.l[-1] <= z["prox"]) if z["demand"] else (S.h[-1] >= z["prox"]))
        z["atr_now"] = float(S.atr[-1]) if np.isfinite(S.atr[-1]) else float("nan")
    snap = snap_prev if want_snapshot else snap_last
    _LAST_SNAP["snap"] = snap
    return out


_LAST_SNAP: dict = {}


def _snap_of(mtf: List[dict]) -> dict:
    """Pine: latest active DEMAND और SUPPLY (array के अंत से पहला)।"""
    d = s = None
    dt = st = 0
    for z in reversed(mtf):
        if z["demand"] and d is None:
            d, dt = z["prox"], z["created_time"]
        if (not z["demand"]) and s is None:
            s, st = z["prox"], z["created_time"]
        if d is not None and s is not None:
            break
    return {"demand": d, "supply": s, "demand_time": dt, "supply_time": st}


# ---------------------------------------------------------------------------
# 6. MTF confirmation (सिर्फ़ दिखाने के लिए — Pine इसे rule नहीं बनाता)
# ---------------------------------------------------------------------------
def adjacent_tfs(chart_tf: str, peers: Sequence[str]) -> List[str]:
    """Pine f_remote(): chart TF छोड़कर बाकी सभी; अनुपात 0.2×–5× के भीतर।"""
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


def info_text(z: dict, target_rr: float) -> str:
    s = (f"{z['pattern']} · {z['category']} · {z['base_count']} boring "
         f"({_grade(z['base_count'])})")
    s += f"\nScore {z['score']}" + (" HQ" if z["hq"] else "")
    s += f"\nE {z['prox']:.2f}  SL {z['sl']:.2f}"
    s += f"\nT1 1:{target_rr:g} {z['tp']:.2f}"
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
        if pstart(idx[-1]) >= pstart(datetime.now(IST)):
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
# 8. SCAN — एक symbol के सभी TF + MTF snapshot
# ---------------------------------------------------------------------------
def scan_symbol(symbol: str,
                frames: Dict[str, pd.DataFrame],
                daily: Optional[pd.DataFrame],
                inputs: PineInputs,
                range_pct: float,
                tfs: Sequence[str] = ALL_TFS,
                ltp: Optional[float] = None) -> dict:
    I = inputs.sanitized()
    per_tf: Dict[str, List[dict]] = {}
    atr_map: Dict[str, float] = {}
    snaps: Dict[str, dict] = {}
    for tf in tfs:
        df = frames.get(tf)
        if df is None or len(df) < 30:
            per_tf[tf] = []
            atr_map[tf] = float("nan")
            snaps[tf] = {"demand": None, "supply": None, "demand_time": 0, "supply_time": 0}
            continue
        zs = engine(df, I, tf, return_all=False)
        for z in zs:
            z["symbol"] = symbol
        per_tf[tf] = zs
        snaps[tf] = _LAST_SNAP.get("snap") or {"demand": None, "supply": None,
                                               "demand_time": 0, "supply_time": 0}
        atr_map[tf] = _atr_last(df, I.atrPeriod)

    rows: List[dict] = []
    px = ltp if ltp else _last_close(frames, tfs)
    for tf in tfs:
        for z in per_tf[tf]:
            z["mtf_n"], z["mtf_tfs"] = mtf_hits(z["demand"], z["prox"], z["dist"],
                                                z["atr_now"], tf, snaps)
            z["mtf"] = z["mtf_n"] >= 1
            z["dist_pct"] = abs(z["prox"] - px) / px * 100.0 if px else None
            if range_pct and z["dist_pct"] is not None and z["dist_pct"] > range_pct:
                continue
            rows.append(z)
    rows.sort(key=lambda r: (r["dist_pct"] if r["dist_pct"] is not None else 9e9,
                             -r["score"], TF_SECONDS.get(r["tf"], 0)))
    return {"rows": rows, "snapshots": snaps, "ltp": px, "atr": atr_map}


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


# ═══════════════════════════════════════════════════════════════════════
#  भाग 4 · Streamlit UI — ऊपर एक पंक्ति (toolbar), नीचे Google-Sheets टेबल
# ═══════════════════════════════════════════════════════════════════════

APP_BUILD = "v4 · 2026-10-27"

st.set_page_config(page_title="ज़ोन MTF स्कैनर", page_icon="📐", layout="wide",
                   initial_sidebar_state="collapsed")

STATE_HI = {"Fresh": "Fresh", "Tested": "Tested", "Broken": "Broken"}
ALL_STATES = ["Fresh", "Tested", "Broken"]
SIDE_HI = {"Demand": "मांग (Demand)", "Supply": "आपूर्ति (Supply)"}
CAT_HI = {"Reversal": "पलटाव", "Continuation": "निरंतरता"}

UNIVERSES = ["NSE All Futures", "NIFTY 50", "Global Instruments", "Futures + Global"]
BANDS = ["Daily (EOD)", "Weekly (EOD)", "Monthly (EOD)"]
BAND_TF = {"Daily (EOD)": "1d", "Weekly (EOD)": "1w", "Monthly (EOD)": "1M"}
TV_TF = {"3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30", "75m": "75",
         "1h": "60", "2h": "120", "4h": "240", "6h": "360", "1d": "D", "1w": "W",
         "1M": "M"}
_STATE_CLS = {"Fresh": "gs-fresh", "Tested": "gs-trig", "Broken": "gs-no"}


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


def _html(src: str) -> None:
    """कच्चा HTML/CSS — markdown पार्सर से नहीं गुज़रता।"""
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


SHEET_CSS = """
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
.block-container{padding-top:.55rem;padding-bottom:1rem;max-width:100%;}
div[data-testid="stHorizontalBlock"]{gap:.45rem;}
h1,h2,h3{letter-spacing:.2px;}
"""
    extra = SHEET_CSS_DARK if is_dark() else SHEET_CSS_LIGHT
    _html("<style>" + base + SHEET_CSS + extra + "</style>")


def reset_inputs():
    for k in list(st.session_state.keys()):
        if str(k).startswith("in_"):
            del st.session_state[k]


def preset_loose():
    """Pine default से थोड़ा ढीला (सिर्फ़ खोज के लिए)।"""
    for k, v in {"in_minvalid": 30, "in_leginbody": 0.50, "in_minclv": 0.50}.items():
        st.session_state[k] = v


# ---------------------------------------------------------------------------
# 1 · ऊपर की एक पंक्ति
# ---------------------------------------------------------------------------
def top_bar():
    c = cols([1.5, 2.0, .8, .85, 1.05, 1.15, .95])
    uni = c[0].selectbox("यूनिवर्स", UNIVERSES, key="in_uni", label_visibility="collapsed")
    tfs = c[1].multiselect("TF", list(ALL_TFS), default=list(ALL_TFS),
                           key="in_tfs", label_visibility="collapsed")
    side = c[2].selectbox("पक्ष", ["All", "Demand", "Supply"], key="in_side",
                          label_visibility="collapsed")
    status = c[3].selectbox("स्थिति", ["All"] + ALL_STATES, key="in_status",
                            label_visibility="collapsed")
    band = c[4].selectbox("रेंज", BANDS, index=1, key="in_band",
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
    host = slot if slot is not None else st
    with host.expander("⚙️ इनपुट", expanded=False):
        b1, b2, _ = st.columns([1, 1, 4])
        put(b1, "button", "↺ Pine default", on_click=reset_inputs)
        put(b2, "button", "🔓 ढीला", on_click=preset_loose)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown("**मूल (Pine)**")
            cap = st.number_input("Account Capital", 1000.0, 1e9, 25000.0, 1000.0, key="in_cap")
            risk = st.number_input("Risk %", 0.05, 10.0, 0.5, 0.05, key="in_risk")
            rr = st.number_input("Target RR", 0.5, 20.0, 5.0, 0.5, key="in_rr")
            slb = st.number_input("SL Buffer ATR", 0.0, 5.0, 0.1, 0.05, key="in_slb")
            atrp = st.number_input("ATR Period", 2, 200, 14, 1, key="in_atr")
            volsma = st.number_input("Volume SMA Period", 2, 200, 20, 1, key="in_volsma")
            lotr = st.number_input("Leg-Out TR Multiplier", 0.0, 5.0, 1.2, 0.1, key="in_lotr")
            lomintr = st.number_input("Leg-Out Min TR Ratio vs Leg-In", 0.0, 5.0, 1.0, 0.1, key="in_lomintr")
        with c2:
            st.markdown("**1-2-4 (Pine)**")
            hqlotr = st.number_input("HQ Leg-Out TR Multiplier", 0.0, 10.0, 2.0, 0.1, key="in_hqlotr")
            hqli = st.number_input("HQ Leg-In ATR Multiplier", 0.0, 10.0, 1.5, 0.1, key="in_hqli")
            baseatr = st.number_input("Max Base TR ATR Multiplier", 0.1, 5.0, 1.0, 0.1, key="in_baseatr")
            maxwick = st.number_input("Max Wick %", 0.0, 1.0, 0.30, 0.05, key="in_maxwick")
            minbc = st.number_input("Min Base Count", 1, 4, 1, 1, key="in_minbc")
            maxbc = st.number_input("Max Base Count (≤3)", 1, 4, 3, 1, key="in_maxbc")
            li_atr = st.number_input("Leg-In Min ATR Multiplier", 0.0, 5.0, 1.0, 0.1, key="in_liatr")
            clv = st.number_input("Min CLV %", 0.0, 1.0, 0.60, 0.05, key="in_minclv")
        with c3:
            st.markdown("**सत्यापन (Pine)**")
            libase = st.number_input("Leg-In to Base Size Multiplier", 0.0, 10.0, 2.0, 0.1, key="in_libase")
            libody = st.number_input("Leg-In Min Body %", 0.0, 1.0, 0.60, 0.05, key="in_leginbody")
            useimb = st.checkbox("Use Imbalance", True, key="in_useimb")
            maximb = st.number_input("Max Imbalance vs Leg-In Mult", 0.0, 5.0, 1.0, 0.1, key="in_maximb")
            relaxgap = st.checkbox("Relax Gap Cap on Overnight", True, key="in_relaxgap")
            gapb = st.number_input("Genuine Gap Score Bonus", 0, 50, 10, 1, key="in_gapb")
            onb = st.number_input("Overnight Gap Score Bonus", 0, 50, 15, 1, key="in_onb")
            rej = st.number_input("Reject Opposite Cover %", 0.0, 1.0, 0.50, 0.05, key="in_rej")
        with c4:
            st.markdown("**Score / Tested**")
            minval = st.number_input("Min Valid Score", 0, 200, 40, 1, key="in_minvalid")
            hqthr = st.number_input("HQ Score Threshold", 0, 300, 90, 1, key="in_hqthr")
            heavy = st.number_input("Leg-Out Body Heavy Pressure %", 0.0, 1.0, 0.60, 0.05, key="in_heavy")
            retr = st.number_input("Tested Leg-Out Retrace %", 0.0, 3.0, 1.00, 0.05, key="in_retr")
            maxtest = st.number_input("Max Tested Count", 0, 20, 1, 1, key="in_maxtest")
            scanbars = st.number_input("कितने बार पीछे (scanBars)", 200, 5000, 500, 100, key="in_scanbars")
            maxsyms = st.number_input("अधिकतम स्क्रिप्ट (0 = सभी)", 0, 3000, 0, 50, key="in_maxsyms")
            showmtf = st.checkbox("MTF गणना (Show other-TF proximal)", True, key="in_showmtf")

        st.markdown("**MTF / Shadows — Pine के parity inputs (zone validation में निष्क्रिय)**")
        d1, d2, d3, d4 = st.columns(4)
        near = d1.number_input("Show lines within % (mtfNearPct)", 0.1, 100.0, 8.0, 0.5, key="in_near")
        fdist = d2.checkbox("Hide distant MTF lines", True, key="in_fdist")
        shadow = d3.checkbox("Shadow", True, key="in_shadow")
        flags = d4.checkbox("BASE BREAK / SWING", True, key="in_flags")
        e1, e2, e3, e4 = st.columns(4)
        shatr = e1.number_input("Shadow padding × ATR", 0.0, 1.0, 0.08, 0.01, key="in_shatr")
        swl = e2.number_input("Swing left", 1, 20, 3, 1, key="in_swl")
        swr = e3.number_input("Swing right", 1, 20, 3, 1, key="in_swr")
        maxst = e4.number_input("Max chart zones", 10, 240, 220, 10, key="in_maxst")
        p1, p2, p3, p4 = st.columns(4)
        p1.checkbox("Leg-Out to Leg-In Body Mult", True, key="in_parity1", disabled=True)
        p2.checkbox("Use Closing-Side Wick Guard", True, key="in_parity2", disabled=True)
        p3.checkbox("Use Leg-Out Coverage Guard", True, key="in_parity3", disabled=True)
        p4.checkbox("Use White Area Check", True, key="in_parity4", disabled=True)
        st.caption("ऊपर के चार parity inputs Pine में हैं पर zone validation में निष्क्रिय हैं — "
                   "इसलिए यहाँ भी सिर्फ़ दिखाने के लिए (बदले नहीं जा सकते)।")

    inp = PineInputs(
        accountCapital=float(cap), riskPct=float(risk), targetRR=float(rr),
        slBufferAtr=float(slb), atrPeriod=int(atrp), volSmaPeriod=int(volsma),
        legOutTrMult=float(lotr), legOutMinTrRatio=float(lomintr),
        hqLegOutTrMult=float(hqlotr), hqLegInAtrMult=float(hqli),
        maxBaseAtrMult=float(baseatr), maxWickPct=float(maxwick),
        minBaseCountInput=int(minbc), maxBaseCountInput=int(maxbc),
        legInMinAtrMult=float(li_atr), minClvPct=float(clv),
        legInToBaseSizeMult=float(libase), legInMinBodyPct=float(libody),
        useImbalance=bool(useimb), maxImbalanceMult=float(maximb),
        relaxGapCapOvernight=bool(relaxgap), genuineGapBonus=int(gapb),
        overnightGapBonus=int(onb), rejectOppositeCoverPct=float(rej),
        minValidScore=int(minval), hqScoreThreshold=int(hqthr),
        legOutBodyHeavyPct=float(heavy), testedLegOutRetracePct=float(retr),
        maxTestedCount=int(maxtest),
        showMTF=bool(showmtf), filterDistantMTF=bool(fdist), mtfNearPct=float(near),
        showShadow=bool(shadow), showFlags=bool(flags), shadowAtr=float(shatr),
        swingLeft=int(swl), swingRight=int(swr), maxStored=int(maxst),
        scanBars=int(scanbars),
    ).sanitized()
    uo = dict(max_syms=int(maxsyms), peers=tuple(ALL_TFS), show_mcap=True, min_mcap=50000.0)
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


@st.cache_resource(ttl=1800, max_entries=4, show_spinner=False)
def cached_frames(symbols: tuple, tfs: tuple):
    return load_frames(list(symbols), list(tfs))


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


@st.cache_data(ttl=300, show_spinner=False)
def cached_scan(symbols: tuple, inp_key: str, opts_key: str):
    inp = PineInputs(**json.loads(inp_key)).sanitized()
    return _scan_all(list(symbols), inp, json.loads(opts_key))


def _scan_all(symbols, inp, opts):
    tfs = list(opts["tfs"]) or list(ALL_TFS)
    need = list(dict.fromkeys(list(tfs) + ["1d", "1w", "1M"]))
    frames = cached_frames(tuple(symbols), tuple(need))
    rows, snaps, atrs, ltps, errs = [], {}, {}, {}, {}
    jobs = [(s, frames[s]) for s in symbols if s in frames]
    for s in symbols:
        if s not in frames:
            errs[s] = "डेटा नहीं मिला"
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            futs = {ex.submit(scan_symbol, s, f, f.get("1d"), inp, 1e9, tfs): s
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

    btf = BAND_TF.get(opts.get("band", "Weekly (EOD)"), "1w")
    bands = {}
    for s in symbols:
        b = eod_band(frames.get(s, {}).get(btf), btf, inp.atrPeriod)
        if b:
            bands[s] = b

    df = pd.DataFrame(rows)
    if not df.empty:
        df["side_hi"] = df["side"].map(SIDE_HI)
        df["cat_hi"] = df["category"].map(CAT_HI).fillna(df["category"])
        df["created_ist"] = df["time"].map(
            lambda ms: datetime.fromtimestamp(ms / 1000, IST).strftime("%d-%b %H:%M") if ms else "—")
        df = df.sort_values(["dist_pct", "score"], ascending=[True, False]).reset_index(drop=True)
    return dict(rows=df, snaps=snaps, atr=atrs, ltp=ltps, errs=errs, bands=bands,
                band_tf=btf, at=datetime.now(IST), scanned=len(jobs))


# ---------------------------------------------------------------------------
# 4 · TradingView लिंक + शीट
# ---------------------------------------------------------------------------
def tv_url(symbol: str, tf: str) -> str:
    from urllib.parse import quote
    sym = sym_meta(symbol)["cash"] or f"NSE:{symbol}"
    return f"https://www.tradingview.com/chart/?symbol={quote(str(sym))}&interval={TV_TF.get(str(tf), '15')}"


def fut_url(symbol: str, tf: str) -> str:
    from urllib.parse import quote
    sym = sym_meta(symbol)["fut"]
    if not sym:
        return tv_url(symbol, tf)
    return f"https://www.tradingview.com/chart/?symbol={quote(str(sym))}&interval={TV_TF.get(str(tf), '15')}"


def _lerp(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _score_bg(sc: float) -> str:
    low, mid, high = (252, 232, 230), (254, 247, 224), (206, 234, 214)
    t = max(0.0, min(1.0, (float(sc) - 30.0) / 90.0))
    c = _lerp(low, mid, t / 0.5) if t <= 0.5 else _lerp(mid, high, (t - 0.5) / 0.5)
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _dist_bar(pct: float, rng: float) -> str:
    r = max(0.01, float(rng))
    w = max(3.0, min(100.0, float(pct) / r * 100.0))
    col = "#3fb950" if pct <= r * 0.2 else ("#d29922" if pct <= r * 0.5 else "#f85149")
    return (f'<span class="gs-track"><span class="gs-fill" style="width:{w:.0f}%;'
            f'background:{col}"></span></span><span class="gs-sm">{pct:.2f}%</span>')


def sheet_html(df: pd.DataFrame, show_mcap=True, min_mcap=50000.0, extra=False,
               range_pct: float = 3.0, max_rows: int = 600) -> str:
    if df is None or df.empty:
        return ""
    d = df.head(max_rows)
    cols_ = ["Equity (Cash)", "Current-Month Future", "TF", "Zone Side", "Pattern", "Status",
             "Multi-TF Highlight", "Proximal (₹)", "Distal (₹)", "Current LTP", "Distance"]
    if extra:
        cols_ += ["Score", "SL", "T1", "Qty", "Range Low", "Range High"]
        if show_mcap:
            cols_ += ["Mkt Cap (₹Cr)"]
    head = "".join(f"<th>{c}</th>" for c in cols_)
    body = []
    for n, (_, r) in enumerate(d.iterrows(), start=0):
        meta = sym_meta(r["symbol"])
        u_cash = tv_url(r["symbol"], r["tf"])
        u_fut = fut_url(r["symbol"], r["tf"])
        fut_lbl = str(meta.get("fut") or "—").split(":")[-1] if meta.get("fut") else "—"
        dem = bool(r["demand"])
        dot = ('<span class="gs-dot gs-dot-d">●</span>' if dem
               else '<span class="gs-dot gs-dot-s">●</span>')
        side = f'{dot} <span class="{"gs-dot-d" if dem else "gs-dot-s"}">{"Demand" if dem else "Supply"}</span>'
        state = str(r["state"])
        mtf_n = int(r.get("mtf_n", 0) or 0)
        mtf_cell = (f'<span title="पुष्टि करने वाले TF: {r.get("mtf_tfs", "")}">'
                    f'<span class="gs-star">★</span> Multi-TF ({mtf_n}×)</span>'
                    if mtf_n >= 1 else '<span class="gs-sm">—</span>')
        ltp = float(r["ltp"]) if pd.notna(r.get("ltp")) else float("nan")
        ltp_txt = f"₹{ltp:,.2f}" if np.isfinite(ltp) else "—"
        cells = [
            f'<td><a href="{u_cash}" target="_blank" rel="noopener" '
            f'title="TradingView · {meta.get("cash")} · {r["tf"]}">{r["symbol"]}</a></td>',
            (f'<td><a class="fut" href="{u_fut}" target="_blank" rel="noopener" '
             f'title="TradingView · {meta.get("fut")} · {r["tf"]}">{fut_lbl}</a></td>'
             if meta.get("fut") else '<td class="gs-sm">—</td>'),
            f'<td>{r["tf"]}</td>',
            f'<td>{side}</td>',
            f'<td class="ctr">{r["pattern"]}</td>',
            f'<td><span class="{_STATE_CLS.get(state, "")}">{state}</span></td>',
            f'<td>{mtf_cell}</td>',
            f'<td class="num">{float(r["prox"]):,.2f}</td>',
            f'<td class="num">{float(r["dist"]):,.2f}</td>',
            f'<td class="num">{ltp_txt}</td>',
            f'<td>{_dist_bar(float(r["dist_pct"]), range_pct)}</td>',
        ]
        if extra:
            sc = float(r["score"])
            cells += [
                f'<td class="ctr" style="background:{_score_bg(sc)};color:#111;font-weight:700">'
                f'{int(sc)}{" ★" if r["hq"] else ""}</td>',
                f'<td class="num">{float(r["sl"]):,.2f}</td>',
                f'<td class="num">{float(r["tp"]):,.2f}</td>',
                f'<td class="num">{int(r["qty"])}</td>',
                (f'<td class="num gs-sm">{float(r["band_low"]):,.2f}</td>'
                 if pd.notna(r.get("band_low")) else '<td class="num gs-sm">—</td>'),
                (f'<td class="num gs-sm">{float(r["band_high"]):,.2f}</td>'
                 if pd.notna(r.get("band_high")) else '<td class="num gs-sm">—</td>'),
            ]
            if show_mcap:
                mv = r.get("mcap_cr", float("nan"))
                if mv is None or (isinstance(mv, float) and pd.isna(mv)):
                    cells.append('<td class="num gs-sm">—</td>')
                else:
                    ok = float(mv) >= float(min_mcap)
                    cells.append(f'<td class="num">{float(mv):,.0f} '
                                 f'<span class="{"gs-ok" if ok else "gs-no"}">'
                                 f'{"✓" if ok else "✗"}</span></td>')
        body.append(f'<tr><td class="gs-rn">{n}</td>' + "".join(cells) + "</tr>")
    return ('<div class="gs-wrap"><div class="gs-scroll"><table class="gs">'
            f'<thead><tr><th class="gs-rn"></th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div></div>')


def zone_table(df: pd.DataFrame, show_mcap=True, min_mcap=50000.0) -> pd.DataFrame:
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
    ctrl = cols([1.55, .85, .7, .7, 3.4])
    inp, uo = pine_panel(ctrl[0])

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
        st.warning("इस रन में कोई ज़ोन नहीं बना — नियम ढीले करें (Min Valid Score / Leg-In Body %)।")
        return

    # ── स्कैन रेंज: EOD candle का High+ATR14 … Low−ATR14 बैंड ──
    bands = res.get("bands") or {}
    if bands:
        lo = df_all["symbol"].map(lambda x: (bands.get(x) or {}).get("low", -np.inf))
        hi = df_all["symbol"].map(lambda x: (bands.get(x) or {}).get("high", np.inf))
        df_all = df_all[(df_all["prox"] >= lo) & (df_all["prox"] <= hi)].copy()
        df_all["band_low"] = df_all["symbol"].map(lambda x: (bands.get(x) or {}).get("low"))
        df_all["band_high"] = df_all["symbol"].map(lambda x: (bands.get(x) or {}).get("high"))

    f = df_all.copy()
    if tb["side"] != "All":
        f = f[f["side"] == tb["side"]]
    if tb["status"] != "All":
        f = f[f["state"] == tb["status"]]

    with ctrl[1]:
        extra_cols = st.toggle("➕ कॉलम", value=False, key="in_extra")
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

    if not f.empty:
        try:
            scale = float(max(1.0, f["dist_pct"].quantile(0.90)))
        except Exception:                                        # noqa: BLE001
            scale = 3.0
        _html(sheet_html(f, uo.get("show_mcap", True), 50000.0, extra=extra_cols,
                         range_pct=scale))

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
