#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  ZONE MTF PRO TERMINAL  ·  single-file build  (v7.0.0)
  Pine Script v6  »  "Zone MTF (KTP rules v7)"  © iincomesense
───────────────────────────────────────────────────────────────────────────────
  Dr. Ravi R. Kumar (KTP / Super Trader) कोर्स के नियम — Pine v7 जैसे ही:
  अनिवार्य: Filled-Unfilled-Filled · 1-2-4 · TR vs ATR · Power of leg-out
  (Entry = Proximal, SL = Distal, लेग-आउट में ही ≥1:3; ≥1:5 = ★ Best)।
  लचीले (स्कोर): White area · Fresh · Opposite zone · Playground · MTF।
  साथ में CBLI, Formation, Maturity, Covered leg-out, DATR buffer (SETS),
  Pulse/Trend (25× नियम), Wholesale, 10-अंक Score the Trade, Institutional
  confluence (displacement, rel. volume, liquidity sweep, imbalance)।
  स्कैन सभी 13 time frame पर होता है।

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
#           f_candidate (boring 1..4, leg-out 1..3) · Power · Score/10 ·
#           Fresh → Tested (1st touch) → Broken (2nd touch / SL) · MTF snapshot
# ═══════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# 1. INPUTS — Pine v7 "Zone MTF (KTP rules v7)" के input.* — वही default
# ---------------------------------------------------------------------------
TRADER_TYPES = ["Auto (25×)", "Aggressive (1M zone)", "Hourly (3M/5M zone)",
                "Daily (10M/15M zone)", "Weekly/Swing (1H/75M/2H zone)", "Monthly (Daily zone)"]
CBLI_MODES = ["Covers >50% (notes)", "Body size >50% (lecture)", "Either (strict)"]
PROX_MODES = ["Boring wick (wick-to-wick)", "Boring body (precise)"]
DIST_MODES = ["Pattern lowest/highest (course)", "Boring only"]
POWER_MODES = ["First leg-out only", "All leg-outs (max 3)"]
MTF_CORE_MODES = ["Off (score only)", "Higher-TF not against", "Higher-TF in favour (strict)"]


@dataclass
class PineInputs:
    # ---- 0 · Capital, risk, buffer (SETS) ----
    accountCapital: float = 25000.0
    riskPct: float = 0.5
    targetRR: float = 3.0
    hourlyRR: float = 3.0
    monthlyRR: float = 3.0
    target2RR: float = 5.0
    halfQty3: bool = True
    traderType: str = "Auto (25×)"
    bufferMode: str = "DATR % (course)"
    bufAggrPct: float = 2.0
    bufDailyPct: float = 2.0
    bufSwingPct: float = 2.0
    slBufferAtr: float = 0.1
    entryBuffer: bool = False
    # ---- 1 · Strictness & core candle rules ----
    strict: bool = False
    atrPeriod: int = 14
    filledMinBody: float = 0.60
    legInAtrMult: float = 1.2
    legOutAtrMult: float = 1.2
    boringAtrMult: float = 1.0
    minBaseCountInput: int = 1
    maxBaseCountInput: int = 3
    legInToBaseSizeMult: float = 1.5
    legOutToBaseSizeMult: float = 2.5
    legOutEachMinBody: float = 0.50
    maxLegOut: int = 3
    allowTwoLegOut: bool = True
    legOutJoinTolAtr: float = 0.25
    useCBLI: bool = True
    cbliMode: str = "Covers >50% (notes)"
    cbliMaxPct: float = 0.50
    useFormation: bool = True
    formationTolAtr: float = 0.10
    proxMode: str = "Boring wick (wick-to-wick)"
    distalMode: str = "Pattern lowest/highest (course)"
    # ---- 1A · MANDATORY: Power of leg-out ----
    corePower: bool = True
    powerMode: str = "All leg-outs (max 3)"
    powerMinRR: float = 3.0
    powerBestRR: float = 5.0
    powerOnBody: bool = True
    # ---- 1B · Flexible rules (score) ----
    useWhiteArea: bool = True
    coreFresh: bool = True
    coreOpposite: bool = True
    oppositeRR: float = 5.0
    corePlayground: bool = False
    playRR: float = 10.0
    oppUseMTF: bool = True
    coreMTF: str = "Off (score only)"
    # ---- 2 · Note rules (grading; mandatory in Strict) ----
    legInMaxWick: float = 0.20
    legOutMinBody: float = 0.65
    gapTolAtr: float = 0.10
    legOutTooLong: float = 4.0
    useCoveredLegOut: bool = True
    coverPct: float = 0.50
    useMaturity: bool = True
    maturityBars: int = 3
    immatureWithTrend: bool = False
    useTimeRule: bool = False
    maxTouches: int = 3
    meltingReversalOnly: bool = True
    reRunDays: int = 30
    plusBuffer: bool = True
    commodityMode: bool = False
    # ---- 3 · Institutional confluence ----
    dispMult: float = 1.5
    volLen: int = 20
    relVolMult: float = 1.5
    sweepLen: int = 10
    # ---- 4 · Score · Pulse · Trend ----
    minScore: int = 5
    greatScore: int = 7
    hideNoTrade: bool = False
    emaFast: int = 20
    trendThr: float = 0.5
    filterAction: bool = False
    filterWholesale: bool = False
    # ---- 6/7 · MTF / swings ----
    showMTF: bool = True
    filterDistantMTF: bool = True
    mtfNearPct: float = 8.0
    swingLeft: int = 3
    swingRight: int = 3
    maxStored: int = 220
    # ---- app layer (Pine में नहीं — स्कैनर के लिए) ----
    scanBars: int = 500

    def sanitized(self) -> "PineInputs":
        d = asdict(self)
        d["minBaseCountInput"] = int(max(1, min(4, self.minBaseCountInput)))
        d["maxBaseCountInput"] = int(max(1, min(4, self.maxBaseCountInput)))
        d["maxLegOut"] = int(max(1, min(3, self.maxLegOut)))
        d["atrPeriod"] = int(max(1, self.atrPeriod))
        d["volLen"] = int(max(1, self.volLen))
        d["sweepLen"] = int(max(2, self.sweepLen))
        d["emaFast"] = int(max(1, self.emaFast))
        d["swingLeft"] = int(max(1, min(20, self.swingLeft)))
        d["swingRight"] = int(max(1, min(20, self.swingRight)))
        d["maxTouches"] = int(max(1, self.maxTouches))
        d["maturityBars"] = int(max(0, self.maturityBars))
        d["maxStored"] = int(max(10, min(self.maxStored, 240)))
        d["scanBars"] = int(max(200, min(self.scanBars, 5000)))
        return PineInputs(**d)

    # Pine: minBaseCount = max(1, min(minIn, maxIn)); maxBaseCount = max(minBaseCount, maxIn)
    @property
    def minBaseCount(self) -> int:
        return max(1, min(int(self.minBaseCountInput), int(self.maxBaseCountInput)))

    @property
    def maxBaseCount(self) -> int:
        return max(self.minBaseCount, int(self.maxBaseCountInput))


HARD_MAX_BASE_COUNT = 4


# ---------------------------------------------------------------------------
# 2. छोटे helpers — Pine: ta.rma / ta.sma / true_range / body_pct
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


def _body_pct(o, h, l, c):
    r = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        b = np.abs(c - o) / r
    return np.where(r == 0, 0.0, b)


def _grade(bc: int) -> str:
    return {1: "Great", 2: "Very good", 3: "Good (½ qty)"}.get(bc, "So-so (½ qty)")


_GRADE_HI = {1: "बेहतरीन", 2: "बहुत अच्छा", 3: "अच्छा (½ qty)", 4: "ठीक-ठाक (½ qty)"}


def tf_type(tf: str) -> int:
    """Pine f_type(): 1 Aggressive · 2 Hourly · 3 Daily · 4 Weekly/Swing · 5 Monthly"""
    s = TF_SECONDS.get(tf, 0)
    return 1 if s <= 60 else 2 if s <= 300 else 3 if s <= 1800 else 4 if s < 86400 else 5


def trader_type(tf: str, I: "PineInputs") -> int:
    if I.traderType in TRADER_TYPES and I.traderType != "Auto (25×)":
        return TRADER_TYPES.index(I.traderType)
    return tf_type(tf)


def trend_tf(tf: str) -> str:
    """25× नियम — Pine f_trend_tf(): ज़ोन TF × 25 ≈ ट्रेंड TF"""
    s = TF_SECONDS.get(tf, 0)
    return "15m" if s <= 60 else "75m" if s <= 300 else "1d" if s <= 1800 else "1w" if s < 86400 else "1M"


TT_NAME = {1: "Aggressive", 2: "Hourly", 3: "Daily", 4: "Weekly/Swing", 5: "Monthly"}


def _t1_rr(tt: int, I: "PineInputs") -> float:
    return I.hourlyRR if tt == 2 else I.monthlyRR if tt == 5 else I.targetRR


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
                self.t = idx.asi8 // 1_000_000
            else:
                wall_idx = idx
                self.t = (idx.asi8 - 19800 * 10 ** 9) // 1_000_000   # naive = IST मानें
        except Exception:                                      # noqa: BLE001
            wall_idx = idx
            self.t = idx.asi8 // 1_000_000
        wall = wall_idx.asi8
        day_id = wall // 86_400_000_000_000
        self.dow = ((day_id + 3) % 7).astype("int64")          # 0 = Mon … 4 = Fri
        self.tod = ((wall // 60_000_000_000) % 1440).astype("int64")
        self.day = np.concatenate(([0], np.cumsum(np.diff(day_id) != 0))).astype("int64")
        self.day_id = day_id
        self.n = len(df)

    def prepare(self, I: PineInputs, daily: Optional[pd.DataFrame] = None) -> None:
        o, h, l, c = self.o, self.h, self.l, self.c
        self.tr = _true_range(h, l, c)
        self.atr = _rma(self.tr, I.atrPeriod)
        self.vol_sma = _sma(self.v, I.volLen)
        self.bp = _body_pct(o, h, l, c)
        self.bull = c > o
        self.bear = o > c
        rng = h - l
        fin = np.isfinite(self.atr)
        sw = I.sweepLen
        self.low_prior = pd.Series(l).rolling(sw, min_periods=sw).min().to_numpy()
        self.high_prior = pd.Series(h).rolling(sw, min_periods=sw).max().to_numpy()
        # तेज़ी के लिए vectorized prefilter (वही शर्तें जो _candidate दोबारा जाँचता है)
        self.li_ok = (fin & (rng > 0) & (self.bull | self.bear) & (self.bp >= I.filledMinBody)
                      & (self.tr >= I.legInAtrMult * np.where(fin, self.atr, np.inf)))
        self.lo_ok = (fin & (self.bull | self.bear) & (self.bp >= I.filledMinBody)
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
                dday = didx.asi8 // 86_400_000_000_000
                pos = np.searchsorted(dday, self.day_id, side="left") - 1   # पिछला पूरा दिन
                ok = pos >= 0
                self.datr[ok] = datr[pos[ok]]
            except Exception:                                  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 4. CANDIDATE — Pine v7 का f_candidate(bc, nOut)
#    bar p = आख़िरी leg-out (Pine का bar 0)
# ---------------------------------------------------------------------------
def _candidate(p: int, bc: int, nout: int, S: _S, I: PineInputs):
    """सफल → dict ; असफल → (stage, reason)"""
    fo = p - (nout - 1)                    # पहली leg-out
    b0 = p - nout                          # सबसे नई boring
    bl = p - (nout + bc - 1)               # सबसे पुरानी boring
    li = bl - 1                            # leg-in
    pi = li - 1                            # leg-in के पीछे वाली candle (CBLI)
    if pi < 1:
        return (0, "")
    atr = S.atr
    if not (np.isfinite(atr[pi]) and np.isfinite(atr[fo])):
        return (0, "")
    o, h, l, c = S.o, S.h, S.l, S.c
    eff_two = I.allowTwoLegOut or I.commodityMode
    if nout == 2 and not eff_two:
        return (0, "2 leg-outs not allowed")
    # ---- 1. Leg-in: Filled + TR ≥ ATR ----
    in_high, in_low = h[li], l[li]
    in_range = in_high - in_low
    in_bull, in_bear = bool(S.bull[li]), bool(S.bear[li])
    if in_range <= 0 or not (in_bull or in_bear):
        return (1, "Leg-in doji")
    if S.bp[li] < I.filledMinBody:
        return (1, "Leg-in not filled")
    if S.tr[li] < I.legInAtrMult * atr[li]:
        return (1, "Leg-in TR < ATR")
    in_body_hi, in_body_lo = max(o[li], c[li]), min(o[li], c[li])
    in_body = in_body_hi - in_body_lo
    # ---- 2. CBLI ----
    if I.useCBLI and in_body > 0:
        opp = (in_bull and S.bear[pi]) or (in_bear and S.bull[pi])
        if opp:
            pi_hi, pi_lo = max(o[pi], c[pi]), min(o[pi], c[pi])
            trap_size = (pi_hi - pi_lo) / in_body > I.cbliMaxPct
            trap_cover = max(0.0, min(pi_hi, in_body_hi) - max(pi_lo, in_body_lo)) / in_body > I.cbliMaxPct
            trap = (trap_cover if I.cbliMode == CBLI_MODES[0]
                    else trap_size if I.cbliMode == CBLI_MODES[1] else (trap_cover or trap_size))
            if trap:
                return (2, "CBLI trap")
    # ---- 3. Boring: हर candle TR ≤ ATR ----
    base_high, base_low = -1e20, 1e20
    base_body_hi, base_body_lo = -1e20, 1e20
    any_bear = any_bull = False
    plus_b = bc == 1
    for i in range(bl, b0 + 1):
        if not np.isfinite(atr[i]) or S.tr[i] > I.boringAtrMult * atr[i]:
            return (3, "Boring TR > ATR")
        base_high = max(base_high, h[i])
        base_low = min(base_low, l[i])
        base_body_hi = max(base_body_hi, o[i], c[i])
        base_body_lo = min(base_body_lo, o[i], c[i])
        any_bear = any_bear or bool(S.bear[i])
        any_bull = any_bull or bool(S.bull[i])
        plus_b = plus_b and S.bp[i] <= 0.15 and l[i] < min(o[i], c[i]) and h[i] > max(o[i], c[i])
    base_size = base_high - base_low
    if base_size <= 0:
        return (3, "Boring size 0")
    # ---- 4. 1-2-4 part 1 ----
    if in_range < I.legInToBaseSizeMult * base_size:
        return (4, f"1-2-4: leg-in < {I.legInToBaseSizeMult:g}× boring")
    # ---- 5. Leg-out: Filled + TR ≥ ATR ----
    demand, supply = bool(S.bull[fo]), bool(S.bear[fo])
    if not (demand or supply):
        return (5, "Leg-out doji")
    if S.tr[fo] < I.legOutAtrMult * atr[fo]:
        return (5, "Leg-out TR < ATR")
    if S.bp[fo] < I.filledMinBody:
        return (5, "Leg-out not filled")
    # ---- 6. 3 तक leg-out (same colour, open ≈ prev close) ----
    out_high, out_low = h[fo], l[fo]
    ob_hi, ob_lo = max(o[fo], c[fo]), min(o[fo], c[fo])          # leg-out body extent
    for k in range(fo + 1, p + 1):
        same = S.bull[k] if demand else S.bear[k]
        if (not same) or abs(o[k] - c[k - 1]) > I.legOutJoinTolAtr * atr[k - 1]:
            return (6, "Leg-outs not continuous")
        if S.bp[k] < I.legOutEachMinBody:
            return (6, "Leg-out wick ≥ 50%")
        out_high = max(out_high, h[k])
        out_low = min(out_low, l[k])
        ob_hi = max(ob_hi, o[k], c[k])
        ob_lo = min(ob_lo, o[k], c[k])
    # ---- 7. 1-2-4 part 2: leg-out body से body ≥ boring (wick से wick) × N ----
    if (ob_hi - ob_lo) < I.legOutToBaseSizeMult * base_size:
        return (7, f"1-2-4: leg-out < {I.legOutToBaseSizeMult:g}× boring")
    pat = ("RBR" if in_bull else "DBR") if demand else ("DBD" if in_bear else "RBD")
    reversal = pat in ("DBR", "RBD")
    # ---- 8. Formation of leg-out (DBR/RBD) ----
    form_ok = True
    if I.useFormation and reversal:
        tol = I.formationTolAtr * atr[fo]
        form_ok = not (o[fo] > in_body_hi + tol or o[fo] < in_body_lo - tol)
    if I.useFormation and not form_ok:
        return (8, "Formation: leg-out open outside leg-in body")
    # ---- 9. White area ----
    out_body_near = min(o[fo], c[fo]) if demand else max(o[fo], c[fo])
    white_ok = (out_body_near > base_body_lo) if demand else (out_body_near < base_body_hi)
    if I.useWhiteArea and not I.commodityMode and not white_ok:
        return (9, "No white area")
    # ---- 10. नोट्स के नियम (ग्रेडिंग; Strict में अनिवार्य) ----
    in_wick = 1.0 - S.bp[li]
    closing_wick = (in_high - c[li]) if in_bull else (c[li] - in_low)
    r1 = in_wick <= I.legInMaxWick and closing_wick > 0
    r2 = S.bp[fo] >= I.legOutMinBody
    fb_hi, fb_lo = max(o[bl], c[bl]), min(o[bl], c[bl])
    g_tol = I.gapTolAtr * atr[li]
    r3 = I.commodityMode or not (fb_lo > in_body_hi + g_tol or fb_hi < in_body_lo - g_tol)
    r4 = (out_low >= base_low) if demand else (out_high <= base_high)
    gap_out = ((min(o[fo], c[fo]) > max(o[b0], c[b0])) if demand
               else (max(o[fo], c[fo]) < min(o[b0], c[b0])))
    gap_size = 0.0
    if gap_out:
        gap_size = ((min(o[fo], c[fo]) - max(o[b0], c[b0])) if demand
                    else (min(o[b0], c[b0]) - max(o[fo], c[fo])))
    r5 = gap_size <= in_range
    notes_pass = int(r1) + int(r2) + int(r3) + int(r4) + int(r5)
    if I.strict and notes_pass < 5:
        why = ("Leg-in wick rule" if not r1 else "Leg-out body < 65%" if not r2
               else "Gap leg-in ↔ boring" if not r3 else "Leg-out breaks boring" if not r4
               else "Gap > leg-in")
        return (10, why)
    # ---- Institutional confluence (सिर्फ़ स्कोर) ----
    disp = S.tr[fo] >= I.dispMult * atr[fo]
    vs = S.vol_sma[fo]
    rel_vol = bool(np.isfinite(vs) and vs > 0 and S.v[fo] >= I.relVolMult * vs)
    zone_low = min(base_low, in_low) if reversal else base_low
    zone_high = max(base_high, in_high) if reversal else base_high
    lp, hp = S.low_prior[pi], S.high_prior[pi]
    sweep = bool((np.isfinite(lp) and zone_low < lp) if demand else (np.isfinite(hp) and zone_high > hp))
    fvg = nout >= 2 and ((l[fo + 1] > h[b0]) if demand else (h[fo + 1] < l[b0]))
    imb = gap_out or fvg
    # ---- Levels ----
    course = I.distalMode == DIST_MODES[0]
    d_dist = (min(base_low, in_low, out_low) if reversal else min(base_low, out_low)) if course else base_low
    s_dist = (max(base_high, in_high, out_high) if reversal else max(base_high, out_high)) if course else base_high
    body_prox = I.proxMode == PROX_MODES[1]
    fo_top, fo_bot = max(o[fo], c[fo]), min(o[fo], c[fo])
    prox = (base_body_hi if body_prox else base_high) if demand else (base_body_lo if body_prox else base_low)
    dist = d_dist if demand else s_dist
    return dict(
        bc=bc, nout=nout, demand=demand, pattern=pat,
        category="Reversal" if reversal else "Continuation",
        prox=float(prox), dist=float(dist),
        cover=float(fo_top - I.coverPct * (fo_top - fo_bot)) if demand
        else float(fo_bot + I.coverPct * (fo_top - fo_bot)),
        xrpt=float(max(o[p], c[p]) if demand else min(o[p], c[p])),
        pow_first=float((c[fo] if I.powerOnBody else h[fo]) if demand
                        else (c[fo] if I.powerOnBody else l[fo])),
        pow_all=float((ob_hi if I.powerOnBody else out_high) if demand
                      else (ob_lo if I.powerOnBody else out_low)),
        white=bool(white_ok and form_ok), fav_colour=bool(any_bear if demand else any_bull),
        plus=bool(plus_b), long_out=bool((h[fo] - l[fo]) > I.legOutTooLong * in_range),
        notes_pass=notes_pass, inst=int(disp) + int(rel_vol) + int(sweep) + int(imb),
        b0=b0, bl=bl, li=li,
    )


def _pow_rr(cand: dict, I: PineInputs) -> float:
    zr = abs(cand["prox"] - cand["dist"])
    ext = cand["pow_first"] if I.powerMode == POWER_MODES[0] else cand["pow_all"]
    if zr <= 0:
        return 0.0
    return (ext - cand["prox"]) / zr if cand["demand"] else (cand["prox"] - ext) / zr


# ---------------------------------------------------------------------------
# 5. ENGINE — ज़ोन + states + MTF snapshot (Pine का chart engine)
# ---------------------------------------------------------------------------
STATE_FRESH, STATE_TRIG = "Fresh", "Tested"
STATE_BROKEN, STATE_COVERED, STATE_IMMATURE = "Broken", "Covered", "Immature"
STATE_SKIPPED, STATE_EXHAUSTED = "Skipped", "Exhausted"
ALIVE_STATES = (STATE_FRESH, STATE_TRIG)


def _matured(tt: int, z: dict, S: _S, p: int) -> bool:
    dt = S.t[p] - z["time"]
    if tt == 1:
        return dt >= 15 * 60_000
    if tt == 2:
        return dt >= 75 * 60_000
    if tt == 3:
        return S.day[p] > z["created_day"]
    if tt == 4:
        return S.day[p] - z["created_day"] >= 7
    return dt >= 28 * 86_400_000


def _entry_allowed(tt: int, S: _S, p: int, intraday: bool) -> bool:
    if not intraday:
        return True
    m = int(S.tod[p])
    if tt == 2:
        return m < 13 * 60
    if tt == 3:
        return m < 12 * 60 + 30
    if tt == 4:
        return not (S.dow[p] == 4 and m >= 12 * 60 + 30)
    return True


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
    """Pine v7 chart engine — हर confirmed बार पर नया ज़ोन, फिर state update।"""
    S = _S(df)
    S.prepare(I, htf_daily)
    n = S.n
    if n < 20:
        return []
    tt = trader_type(tf, I)
    t1 = _t1_rr(tt, I)
    bpct = _buf_pct(tt, I)
    intraday = TF_SECONDS.get(tf, 0) < 86400
    eff_covered = I.useCoveredLegOut or I.coreFresh
    eff_maturity = I.useMaturity and not I.commodityMode
    act_now = bool((ctx or {}).get("action_long")), bool((ctx or {}).get("action_short"))
    start = max(0, n - max(I.scanBars, 50))
    min_p = max(I.atrPeriod, I.maxBaseCount + I.maxLegOut + 3, I.sweepLen + 6, 11)
    live_last = _bar_incomplete(S, tf)
    tf_sec = TF_SECONDS.get(tf, 0)
    L, R = I.swingLeft, I.swingRight

    zones: List[dict] = []
    alive: List[dict] = []
    rej: Dict[str, int] = {}
    created = 0
    sw_h = sw_l = None
    sw_h_bar = sw_l_bar = None
    h_conf = l_conf = None
    h_brk = l_brk = None

    for p in range(start, n):
        # ---------- swing pivots (ta.pivothigh / pivotlow) ----------
        cpv = p - R
        if cpv - L >= 0:
            hc, lc = S.h[cpv], S.l[cpv]
            if hc > S.h[cpv - L:cpv].max() and hc > S.h[cpv + 1:p + 1].max():
                sw_h, sw_h_bar, h_conf, h_brk = hc, cpv, p, None
            if lc < S.l[cpv - L:cpv].min() and lc < S.l[cpv + 1:p + 1].min():
                sw_l, sw_l_bar, l_conf, l_brk = lc, cpv, p, None
        if sw_h is not None and h_brk is None and p >= h_conf and S.c[p] > sw_h:
            h_brk = p
        if sw_l is not None and l_brk is None and p >= l_conf and S.c[p] < sw_l:
            l_brk = p

        # ---------- नया ज़ोन (bar p = आख़िरी leg-out) ----------
        if p >= min_p and np.isfinite(S.atr[p]) and not (p == n - 1 and live_last):
            found = False
            best_stage, best_reason = -1, ""
            for nout in range(1, I.maxLegOut + 1):
                if found:
                    break
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
                    found = True
                    dem = cand["demand"]
                    # duplicate: same side & |old.prox − prox| < ATR × 0.25
                    dup = False
                    checked = 0
                    for old in reversed(alive):
                        if old["demand"] == dem and abs(old["prox"] - cand["prox"]) < S.atr[p] * 0.25:
                            dup = True
                            break
                        checked += 1
                        if checked >= 11:
                            break
                    if dup:
                        break
                    # ---- SETS ----
                    datr = S.datr[p]
                    slbuf = (datr * bpct / 100.0 if (I.bufferMode == "DATR % (course)" and np.isfinite(datr))
                             else I.slBufferAtr * S.atr[p])
                    sl = cand["dist"] - slbuf if dem else cand["dist"] + slbuf
                    ebuf = I.entryBuffer or (I.plusBuffer and cand["plus"])
                    entry = (cand["prox"] + slbuf if dem else cand["prox"] - slbuf) if ebuf else cand["prox"]
                    risk = abs(entry - sl)
                    if risk <= 0:
                        break
                    tp = entry + risk * t1 if dem else entry - risk * t1
                    t5 = entry + risk * I.target2RR if dem else entry - risk * I.target2RR
                    xrr = ((cand["xrpt"] - entry) if dem else (entry - cand["xrpt"])) / risk
                    qty = int(math.floor(I.accountCapital * I.riskPct / 100.0 / risk))
                    if I.halfQty3 and bc >= 3:
                        qty = qty // 2
                    # ---- पुराने ज़ोन: follow-up / base break / opposite room ----
                    behind = follow = base_brk = False
                    room = None
                    for old in zones:
                        if old["created_bar"] >= bl:
                            continue
                        if old["state"] in ALIVE_STATES:
                            behind = True
                            if old["demand"] == dem and (old["prox"] <= cand["dist"] if dem
                                                         else old["prox"] >= cand["dist"]):
                                follow = True
                            if old["demand"] != dem:
                                if dem and old["prox"] > entry:
                                    r_ = (old["prox"] - entry) / risk
                                    room = r_ if room is None else min(room, r_)
                                elif (not dem) and old["prox"] < entry:
                                    r_ = (entry - old["prox"]) / risk
                                    room = r_ if room is None else min(room, r_)
                        if (old["state"] == STATE_BROKEN and old.get("broken_at") is not None
                                and bl <= old["broken_at"] <= b0):
                            for b in range(bl, b0 + 1):
                                if ((old["demand"] and S.c[b] < old["dist"])
                                        or ((not old["demand"]) and S.c[b] > old["dist"])):
                                    base_brk = True
                                    break
                    ok_h = sw_h is not None and sw_h_bar < bl and h_conf < bl and h_brk is not None
                    ok_l = sw_l is not None and sw_l_bar < bl and l_conf < bl and l_brk is not None
                    broke_h = ok_h and h_brk >= bl
                    broke_l = ok_l and l_brk >= bl
                    swing_brk = broke_h or broke_l
                    # ---- अनिवार्य: Power of leg-out (Entry=Proximal, SL=Distal) ----
                    pw = _pow_rr(cand, I)
                    if I.corePower and pw < I.powerMinRR:
                        key = f"Power < 1:{I.powerMinRR:g}"
                        rej[key] = rej.get(key, 0) + 1
                        break
                    best = pw >= I.powerBestRR
                    # ---- Score the Trade (Room + Pulse/Trend अंक scan में जुड़ते हैं) ----
                    score_base = (2 + int(cand["white"]) + 1 + int(best) + int(bc <= 2)
                                  + int(cand["fav_colour"]) + int(follow or base_brk or swing_brk))
                    z = dict(
                        demand=dem, side="Demand" if dem else "Supply",
                        prox=cand["prox"], dist=cand["dist"], entry=float(entry),
                        sl=float(sl), tp=float(tp), t5=float(t5), risk=float(risk), qty=qty,
                        cover=cand["cover"], xrr=float(xrr), pow_rr=float(pw), best=bool(best),
                        room_tf=room, score_base=int(score_base), score=int(score_base),
                        single_lo=bool(cand["nout"] == 1),
                        hq=False, pattern=cand["pattern"], category=cand["category"],
                        reversal=cand["category"] == "Reversal",
                        base_count=bc, leg_outs=cand["nout"], grade=_grade(bc),
                        grade_hi=_GRADE_HI.get(bc, "ठीक-ठाक"),
                        white=cand["white"], fav_colour=cand["fav_colour"],
                        notes_pass=cand["notes_pass"], inst=cand["inst"],
                        long_out=cand["long_out"], follow=follow, base_break=base_brk,
                        swing_break=swing_brk, trader_type=TT_NAME.get(tt, ""), t1_rr=t1,
                        state=STATE_FRESH, touches=0, outside=True, target_hit=False,
                        six=0, entry_bar=None, first_run=None, old_retest=False,
                        created_bar=p, created_day=int(S.day[p]), bar=p,
                        left_bar=bl - 1, time=int(S.t[p]), start_time=int(S.t[bl - 1]),
                        broken_at=None, tf=tf, tf_sec=tf_sec, symbol=None,
                    )
                    zones.append(z)
                    alive.append(z)
                    created += 1
                    if len(zones) > I.maxStored:
                        gone = zones.pop(0)
                        if gone in alive:
                            alive.remove(gone)
                    break
            if not found and best_stage >= 6 and best_reason:
                rej[best_reason] = rej.get(best_reason, 0) + 1

        # ---------- state update ----------
        if alive:
            lo_, hi_, op_, cl_ = S.l[p], S.h[p], S.o[p], S.c[p]
            for z in list(alive):
                if p <= z["created_bar"]:
                    continue
                dem = z["demand"]
                hit_sl = (lo_ <= z["sl"]) if dem else (hi_ >= z["sl"])
                in_z = (lo_ <= z["entry"]) if dem else (hi_ >= z["entry"])
                touch = (lo_ <= z["prox"]) if dem else (hi_ >= z["prox"])
                if z["state"] == STATE_FRESH:
                    if touch:
                        trend_now = act_now[0] if dem else act_now[1]
                        if (eff_maturity and p - z["created_bar"] < I.maturityBars
                                and not (I.immatureWithTrend and trend_now)):
                            z["state"] = STATE_IMMATURE
                        elif I.useTimeRule and not _entry_allowed(tt, S, p, intraday):
                            z["state"] = STATE_SKIPPED
                        elif hit_sl:
                            z["state"] = STATE_BROKEN
                        else:
                            z["state"] = STATE_TRIG          # पहला टच → Tested
                            z["touches"] = 1
                            z["outside"] = False
                            z["entry_bar"] = p
                    elif eff_covered and ((min(op_, cl_) < z["cover"]) if dem else (max(op_, cl_) > z["cover"])):
                        z["state"] = STATE_COVERED
                else:
                    if hit_sl:
                        z["state"] = STATE_BROKEN
                    else:
                        if not z["target_hit"] and ((hi_ >= z["tp"]) if dem else (lo_ <= z["tp"])):
                            z["target_hit"] = True
                            z["first_run"] = int(S.t[p])
                            if z["six"] == 0:
                                z["six"] = 1 if p - z["entry_bar"] + 1 <= 6 else -1
                        if not z["target_hit"] and z["six"] == 0 and p - z["entry_bar"] + 1 > 6:
                            z["six"] = -1
                        if not touch:
                            z["outside"] = True
                        elif z["outside"]:
                            z["touches"] += 1             # दूसरा टच → Broken
                            z["outside"] = False
                            z["state"] = STATE_BROKEN
                if z["state"] == STATE_BROKEN and z["broken_at"] is None:
                    z["broken_at"] = p
                if z["state"] not in ALIVE_STATES:
                    alive.remove(z)

        if h_brk is not None and p - h_brk > I.maxBaseCount + I.maxLegOut:
            sw_h = sw_h_bar = h_brk = None
        if l_brk is not None and p - l_brk > I.maxBaseCount + I.maxLegOut:
            sw_l = sw_l_bar = l_brk = None

    snap = _snap_of(alive)
    _LAST_SNAP["snap"] = snap
    if snap_out is not None:
        snap_out["snap"] = snap
        snap_out["rej"] = rej
        snap_out["created"] = created
    out = zones if return_all else [z for z in zones if z["state"] in ALIVE_STATES]
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
def _htf_stats(df: Optional[pd.DataFrame], I: PineInputs) -> Optional[dict]:
    """Pine: request.security(..., [close[1], open[1], ema[1], ema[7], atr[1]])"""
    if df is None or len(df) < max(I.emaFast, I.atrPeriod) + 9:
        return None
    c = df["close"].astype("float64")
    e = c.ewm(span=I.emaFast, adjust=False).mean().to_numpy()
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
    up, dn = slope > I.trendThr, slope < -I.trendThr
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


def verdict(score: int, I: PineInputs) -> str:
    return "GREAT" if score >= I.greatScore else "GOOD" if score >= I.minScore else "NO TRADE"


def info_text(z: dict, target_rr: float) -> str:
    s = (f"{z['pattern']} · {z['category']} · {z['base_count']} boring "
         f"({_grade(z['base_count'])})")
    s += f"\nScore {z['score']}/10 · Rules {z.get('notes_pass', 0)}/5 · Inst {z.get('inst', 0)}/4"
    s += f"\nE {z['entry']:.2f}  SL {z['sl']:.2f}  Qty {z['qty']}"
    s += f"\nT1 1:{z.get('t1_rr', target_rr):g} {z['tp']:.2f} · Power 1:{z.get('pow_rr', 0):.1f}"
    s += (" ★" if z.get("best") else "") + f"\n{z['state']}"
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
# 8. SCAN — एक symbol के सभी TF + MTF snapshot + Pulse/Trend + Room
# ---------------------------------------------------------------------------
_EMPTY_SNAP = {"demand": None, "supply": None, "demand_time": 0, "supply_time": 0}
_ZCACHE: Dict[tuple, tuple] = {}
_ZVER = "z8"           # इंजन बदले तो बढ़ाएँ — पुराना कैश अपने आप बेकार


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
                ltp: Optional[float] = None) -> dict:
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

    def drop(key: str):
        rej[key] = rej.get(key, 0) + 1

    rows: List[dict] = []
    px = ltp if ltp else _last_close(frames, tfs)
    for tf in tfs:
        cx = ctxs.get(tf) or {}
        for z in per_tf[tf]:
            dem = z["demand"]
            z["symbol"] = symbol
            z["mtf_n"], z["mtf_tfs"] = mtf_hits(dem, z["prox"], z["dist"], z["atr_now"], tf, snaps)
            z["mtf"] = z["mtf_n"] >= 1
            # ---- Pulse / Trend / Action / Wholesale (25× ट्रेंड TF) ----
            act_ok = bool(cx.get("action_long") if dem else cx.get("action_short"))
            act_against = bool(cx.get("action_short") if dem else cx.get("action_long"))
            wb, wsl = cx.get("ws_buy"), cx.get("ws_sell")
            ws_ok = bool((wb is not None and z["entry"] <= wb) if dem else (wsl is not None and z["entry"] >= wsl))
            z["action_ok"], z["ws_ok"] = act_ok, ws_ok
            z["trend_tf"] = cx.get("trend_tf", "")
            z["pulse"] = "P+" if cx.get("pulse_pos") else "P−" if cx.get("pulse_neg") else "P~"
            z["trend"] = "T↑" if cx.get("trend_up") else "T↓" if cx.get("trend_down") else "T→"
            # ---- Opposite zone / playground room (इसी TF + दूसरे TF के ज़ोन) ----
            room = z.get("room_tf")
            if I.oppUseMTF:
                for tf2, s2 in snaps.items():
                    if tf2 == tf:
                        continue
                    lvl = (s2 or {}).get("supply" if dem else "demand")
                    if lvl is None:
                        continue
                    if (dem and lvl > z["entry"]) or ((not dem) and lvl < z["entry"]):
                        r_ = abs(lvl - z["entry"]) / z["risk"] if z["risk"] > 0 else 0.0
                        room = r_ if room is None else min(room, r_)
            z["room"] = room
            opp_ok = room is None or room >= I.oppositeRR
            play_ok = room is None or room >= I.playRR or z["xrr"] >= I.playRR
            if I.coreOpposite and not opp_ok:
                drop(f"Opposite zone < 1:{I.oppositeRR:g}")
                continue
            if I.corePlayground and not play_ok:
                drop(f"Playground < 1:{I.playRR:g}")
                continue
            if I.coreMTF == MTF_CORE_MODES[1] and act_against:
                drop("Higher-TF against")
                continue
            if I.coreMTF == MTF_CORE_MODES[2] and not act_ok:
                drop("Higher-TF not in favour")
                continue
            # ---- Score the Trade (10 अंक) ----
            room_pt = 1 if (room is None or room >= 10 or z["xrr"] >= 10) else 0
            z["score"] = int(z["score_base"] + room_pt + int(act_ok))
            z["hq"] = z["score"] >= I.greatScore
            z["verdict"] = verdict(z["score"], I)
            if I.hideNoTrade and z["score"] < I.minScore:
                continue
            if I.filterAction and not act_ok:
                continue
            if I.filterWholesale and not ws_ok:
                continue
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


# ═══════════════════════════════════════════════════════════════════════
#  भाग 4 · Streamlit UI — ऊपर एक पंक्ति (toolbar), नीचे Google-Sheets टेबल
# ═══════════════════════════════════════════════════════════════════════

APP_BUILD = "v8 · KTP rules (आपके चुने नियम) · 2026-10-27"

st.set_page_config(page_title="ज़ोन MTF स्कैनर", page_icon="📐", layout="wide",
                   initial_sidebar_state="collapsed")

STATE_HI = {"Fresh": "Fresh", "Tested": "Tested"}
ALL_STATES = ["Fresh", "Tested"]
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
.block-container{padding-top:.55rem;padding-bottom:1rem;max-width:100%;}
div[data-testid="stHorizontalBlock"]{gap:.45rem;}
h1,h2,h3{letter-spacing:.2px;}
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
    """Pine default से थोड़ा ढीला (सिर्फ़ खोज के लिए)।"""
    for k, v in {"in_powermin": 2.0, "in_filled": 0.40, "in_cbli": False,
                 "in_form": False, "in_maturity": False}.items():
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
    D = PineInputs()
    with host.expander("⚙️ इनपुट", expanded=False):
        b1, b2, b3, _ = st.columns([1, 1, 1.15, 3])
        put(b1, "button", "↺ Pine default", on_click=reset_inputs)
        put(b2, "button", "🔓 ढीला", on_click=preset_loose)
        put(b3, "button", "♻️ कैश साफ़", on_click=hard_reset)
        t0, t1, t2, t3, t4, t5 = st.tabs(["अनिवार्य नियम", "लचीले नियम", "Candle नियम",
                                          "नोट्स नियम", "SETS · जोखिम", "Score · Pulse · MTF"])
        with t0:
            st.caption("हमेशा चालू: **Filled-Unfilled-Filled · 1-2-4 · TR vs ATR · White area · "
                       "Formation · CBLI**। साथ में **Power of leg-out** — Entry = Proximal, "
                       "SL = Distal; leg-out की close तक कम से कम 1:3, 1:5 पर ★।")
            a1, a2, a3, a4 = st.columns(4)
            core_power = a1.checkbox("Power of leg-out (अनिवार्य)", D.corePower, key="in_corepower")
            power_mode = a2.selectbox("Power किस पर मापें", POWER_MODES,
                                      index=POWER_MODES.index(D.powerMode), key="in_powermode")
            power_min = a3.number_input("Power न्यूनतम 1:", 1.0, 20.0, D.powerMinRR, 0.5, key="in_powermin")
            power_best = a4.number_input("Power Best ★ 1:", 1.0, 30.0, D.powerBestRR, 0.5, key="in_powerbest")
            a5, a6, a7, a8 = st.columns(4)
            strict = a5.checkbox("Strict (नोट्स के सभी नियम अनिवार्य)", D.strict, key="in_strict")
            filled = a6.number_input("Filled candle: min body %", 0.0, 1.0, D.filledMinBody, 0.05, key="in_filled")
            li_atr = a7.number_input("Leg-in: TR ≥ ATR ×", 0.0, 5.0, D.legInAtrMult, 0.05, key="in_liatr")
            lo_atr = a8.number_input("Leg-out: TR ≥ ATR ×", 0.0, 5.0, D.legOutAtrMult, 0.05, key="in_loatr")
            a9, a10, a11, a12 = st.columns(4)
            bo_atr = a9.number_input("Boring: TR ≤ ATR ×", 0.1, 5.0, D.boringAtrMult, 0.05, key="in_boatr")
            li_base = a10.number_input("1-2-4: Leg-in ≥ boring ×", 1.0, 10.0, D.legInToBaseSizeMult, 0.25, key="in_libase")
            lo_base = a11.number_input("1-2-4: Leg-out ≥ boring ×", 1.0, 20.0, D.legOutToBaseSizeMult, 0.25, key="in_lobase")
            atrp = a12.number_input("ATR Period (कोर्स: 14)", 2, 200, D.atrPeriod, 1, key="in_atr")
            a13, a14, _, _ = st.columns(4)
            lo_each = a13.number_input("Leg-out 2/3: हर candle की min body %", 0.0, 1.0, D.legOutEachMinBody, 0.05, key="in_loeach")
            pow_body = a14.checkbox("Power: leg-out close (body) तक", D.powerOnBody, key="in_powbody")
        with t1:
            st.caption("बंद = सिर्फ़ स्कोर में गिनती · चालू = अनिवार्य (फेल हुआ तो ज़ोन नहीं दिखेगा)")
            f1, f2, f3, f4 = st.columns(4)
            white = f1.checkbox("White area → अनिवार्य", D.useWhiteArea, key="in_white")
            fresh = f2.checkbox("Fresh zone only (पहला टच)", D.coreFresh, key="in_fresh")
            opp = f3.checkbox("Opposite zone → अनिवार्य", D.coreOpposite, key="in_opp")
            opp_rr = f4.number_input("Opposite zone: 1: के अंदर नहीं", 1.0, 30.0, D.oppositeRR, 0.5, key="in_opprr")
            f5, f6, f7, f8 = st.columns(4)
            play = f5.checkbox("Clear playground → अनिवार्य", D.corePlayground, key="in_play")
            play_rr = f6.number_input("Playground: 1: तक साफ़", 1.0, 50.0, D.playRR, 0.5, key="in_playrr")
            opp_mtf = f7.checkbox("दूसरे TF के ज़ोन भी गिनें", D.oppUseMTF, key="in_oppmtf")
            core_mtf = f8.selectbox("MTF analysis", MTF_CORE_MODES,
                                    index=MTF_CORE_MODES.index(D.coreMTF), key="in_coremtf")
        with t2:
            c1, c2, c3, c4 = st.columns(4)
            minbc = c1.number_input("Min boring candles", 1, 4, D.minBaseCountInput, 1, key="in_minbc")
            maxbc = c2.number_input("Max boring candles", 1, 4, D.maxBaseCountInput, 1, key="in_maxbc")
            maxlo = c3.number_input("Max leg-out candles", 1, 3, D.maxLegOut, 1, key="in_maxlo")
            two_lo = c4.checkbox("2 leg-out मान्य", D.allowTwoLegOut, key="in_twolo")
            c5, c6, c7, c8 = st.columns(4)
            join_tol = c5.number_input("Leg-out 2/3: |open−prev close| ≤ ATR ×", 0.0, 2.0, D.legOutJoinTolAtr, 0.05, key="in_jointol")
            cbli = c6.checkbox("CBLI (trap)", D.useCBLI, key="in_cbli")
            cbli_mode = c7.selectbox("CBLI नियम", CBLI_MODES, index=CBLI_MODES.index(D.cbliMode), key="in_cblimode")
            cbli_pct = c8.number_input("CBLI सीमा", 0.0, 2.0, D.cbliMaxPct, 0.05, key="in_cblipct")
            c9, c10, c11, c12 = st.columns(4)
            form = c9.checkbox("Formation of leg-out (DBR/RBD)", D.useFormation, key="in_form")
            form_tol = c10.number_input("Formation tolerance × ATR", 0.0, 2.0, D.formationTolAtr, 0.05, key="in_formtol")
            prox_mode = c11.selectbox("Proximal (Entry) लाइन", PROX_MODES, index=PROX_MODES.index(D.proxMode), key="in_proxmode")
            dist_mode = c12.selectbox("Distal (SL) लाइन", DIST_MODES, index=DIST_MODES.index(D.distalMode), key="in_distmode")
        with t3:
            n1, n2, n3, n4 = st.columns(4)
            li_wick = n1.number_input("Leg-in: max wick %", 0.0, 0.5, D.legInMaxWick, 0.05, key="in_liwick")
            lo_body = n2.number_input("Leg-out: min body % (65:35)", 0.0, 1.0, D.legOutMinBody, 0.05, key="in_lobody")
            gap_tol = n3.number_input("Leg-in↔boring gap tolerance × ATR", 0.0, 2.0, D.gapTolAtr, 0.05, key="in_gaptol")
            lo_long = n4.number_input("⚠ अगर leg-out > leg-in ×", 1.0, 20.0, D.legOutTooLong, 0.5, key="in_lolong")
            n5, n6, n7, n8 = st.columns(4)
            covered = n5.checkbox("Covered leg-out = trap", D.useCoveredLegOut, key="in_covered")
            cover_pct = n6.number_input("Covered: पहली leg-out body का %", 0.1, 1.0, D.coverPct, 0.05, key="in_coverpct")
            maturity = n7.checkbox("Zone maturity", D.useMaturity, key="in_maturity")
            mat_bars = n7.number_input("Maturity: कितनी candle बाद", 0, 50, D.maturityBars, 1, key="in_matbars")
            imm_trend = n8.checkbox("Pulse/Trend साथ हो तो immature भी", D.immatureWithTrend, key="in_immtrend")
            n9, n10, n11, n12 = st.columns(4)
            time_rule = n9.checkbox("Entry time नियम (intraday)", D.useTimeRule, key="in_timerule")
            max_touch = n10.number_input("Max entries", 1, 10, D.maxTouches, 1, key="in_maxtouch")
            melting = n11.checkbox("Re-entry सिर्फ़ DBR/RBD (melting meter)", D.meltingReversalOnly, key="in_melting")
            rerun = n12.number_input("Re-test ⚠ दिनों बाद", 1, 365, D.reRunDays, 1, key="in_rerun")
            n13, n14, _, _ = st.columns(4)
            plus_buf = n13.checkbox("Plus boring → entry पर भी buffer", D.plusBuffer, key="in_plusbuf")
            commodity = n14.checkbox("Commodity mode", D.commodityMode, key="in_commodity")
        with t4:
            s1, s2, s3, s4 = st.columns(4)
            cap = s1.number_input("Account Capital (₹)", 1000.0, 1e9, D.accountCapital, 1000.0, key="in_cap")
            risk = s2.number_input("Risk % per trade", 0.05, 10.0, D.riskPct, 0.05, key="in_risk")
            rr = s3.number_input("Target 1 RR (Daily/Weekly)", 1.0, 20.0, D.targetRR, 0.5, key="in_rr")
            hrr = s4.number_input("Hourly: पहला target RR", 1.0, 20.0, D.hourlyRR, 0.5, key="in_hrr")
            s5, s6, s7, s8 = st.columns(4)
            mrr = s5.number_input("Monthly: पहला target RR", 1.0, 50.0, D.monthlyRR, 0.5, key="in_mrr")
            t2rr = s5.number_input("Target 2 RR (सभी)", 1.0, 50.0, D.target2RR, 0.5, key="in_t2rr")
            half = s6.checkbox("3+ boring → आधी quantity", D.halfQty3, key="in_half")
            ttype = s7.selectbox("Trader type", TRADER_TYPES, index=0, key="in_ttype")
            bmode = s8.selectbox("SL buffer तरीका", ["DATR % (course)", "Chart ATR ×"], key="in_bmode")
            s9, s10, s11, s12 = st.columns(4)
            b_aggr = s9.number_input("Buffer % DATR — Aggressive/Hourly", 0.0, 50.0, D.bufAggrPct, 0.5, key="in_baggr")
            b_daily = s10.number_input("Buffer % DATR — Daily", 0.0, 50.0, D.bufDailyPct, 0.5, key="in_bdaily")
            b_swing = s11.number_input("Buffer % DATR — Weekly/Monthly", 0.0, 50.0, D.bufSwingPct, 0.5, key="in_bswing")
            slb = s12.number_input("SL Buffer × chart ATR (fallback)", 0.0, 5.0, D.slBufferAtr, 0.01, key="in_slb")
            s13, s14, s15, s16 = st.columns(4)
            ebuf = s13.checkbox("Entry पर भी buffer (sideways/index)", D.entryBuffer, key="in_ebuf")
            scanbars = s14.number_input("कितने बार पीछे (scanBars)", 200, 5000, D.scanBars, 100, key="in_scanbars")
            maxsyms = s15.number_input("अधिकतम स्क्रिप्ट (0 = सभी)", 0, 3000, 0, 50, key="in_maxsyms")
            maxst = s16.number_input("Max zones / TF", 10, 240, D.maxStored, 10, key="in_maxst")
        with t5:
            g1, g2, g3, g4 = st.columns(4)
            min_score = g1.number_input("Min score (कम = NO TRADE)", 0, 10, D.minScore, 1, key="in_minscore")
            great = g2.number_input("Great score", 0, 10, D.greatScore, 1, key="in_great")
            hide_nt = g3.checkbox("NO-TRADE ज़ोन छिपाएँ", D.hideNoTrade, key="in_hident")
            ema = g4.number_input("EMA (pulse & trend)", 2, 200, D.emaFast, 1, key="in_ema")
            g5, g6, g7, g8 = st.columns(4)
            thr = g5.number_input("7-candle sideways band (× ATR)", 0.0, 5.0, D.trendThr, 0.1, key="in_thr")
            f_act = g6.checkbox("सिर्फ़ Action दिशा वाले ज़ोन", D.filterAction, key="in_fact")
            f_ws = g7.checkbox("सिर्फ़ Wholesale ज़ोन", D.filterWholesale, key="in_fws")
            disp = g8.number_input("Displacement: leg-out TR ≥ ATR ×", 1.0, 5.0, D.dispMult, 0.1, key="in_disp")
            g9, g10, g11, g12 = st.columns(4)
            vol_len = g9.number_input("Relative volume SMA", 2, 200, D.volLen, 1, key="in_vollen")
            rel_vol = g10.number_input("Relative volume ≥ SMA ×", 1.0, 10.0, D.relVolMult, 0.1, key="in_relvol")
            sweep = g11.number_input("Liquidity sweep lookback", 2, 100, D.sweepLen, 1, key="in_sweep")
            showmtf = g12.checkbox("MTF गणना", D.showMTF, key="in_showmtf")
            g13, g14, g15, _ = st.columns(4)
            swl = g13.number_input("Swing left", 1, 20, D.swingLeft, 1, key="in_swl")
            swr = g14.number_input("Swing right", 1, 20, D.swingRight, 1, key="in_swr")
            near = g15.number_input("MTF: % के भीतर", 0.1, 100.0, D.mtfNearPct, 0.5, key="in_near")

    inp = PineInputs(
        accountCapital=float(cap), riskPct=float(risk), targetRR=float(rr), hourlyRR=float(hrr),
        target2RR=float(t2rr), legOutEachMinBody=float(lo_each), powerOnBody=bool(pow_body),
        maturityBars=int(mat_bars),
        monthlyRR=float(mrr), halfQty3=bool(half), traderType=str(ttype), bufferMode=str(bmode),
        bufAggrPct=float(b_aggr), bufDailyPct=float(b_daily), bufSwingPct=float(b_swing),
        slBufferAtr=float(slb), entryBuffer=bool(ebuf),
        strict=bool(strict), atrPeriod=int(atrp), filledMinBody=float(filled),
        legInAtrMult=float(li_atr), legOutAtrMult=float(lo_atr), boringAtrMult=float(bo_atr),
        minBaseCountInput=int(minbc), maxBaseCountInput=int(maxbc),
        legInToBaseSizeMult=float(li_base), legOutToBaseSizeMult=float(lo_base),
        maxLegOut=int(maxlo), allowTwoLegOut=bool(two_lo), legOutJoinTolAtr=float(join_tol),
        useCBLI=bool(cbli), cbliMode=str(cbli_mode), cbliMaxPct=float(cbli_pct),
        useFormation=bool(form), formationTolAtr=float(form_tol),
        proxMode=str(prox_mode), distalMode=str(dist_mode),
        corePower=bool(core_power), powerMode=str(power_mode), powerMinRR=float(power_min),
        powerBestRR=float(power_best),
        useWhiteArea=bool(white), coreFresh=bool(fresh), coreOpposite=bool(opp),
        oppositeRR=float(opp_rr), corePlayground=bool(play), playRR=float(play_rr),
        oppUseMTF=bool(opp_mtf), coreMTF=str(core_mtf),
        legInMaxWick=float(li_wick), legOutMinBody=float(lo_body), gapTolAtr=float(gap_tol),
        legOutTooLong=float(lo_long), useCoveredLegOut=bool(covered), coverPct=float(cover_pct),
        useMaturity=bool(maturity), immatureWithTrend=bool(imm_trend), useTimeRule=bool(time_rule),
        maxTouches=int(max_touch), meltingReversalOnly=bool(melting), reRunDays=int(rerun),
        plusBuffer=bool(plus_buf), commodityMode=bool(commodity),
        dispMult=float(disp), volLen=int(vol_len), relVolMult=float(rel_vol), sweepLen=int(sweep),
        minScore=int(min_score), greatScore=int(great), hideNoTrade=bool(hide_nt),
        emaFast=int(ema), trendThr=float(thr), filterAction=bool(f_act), filterWholesale=bool(f_ws),
        showMTF=bool(showmtf), mtfNearPct=float(near), swingLeft=int(swl), swingRight=int(swr),
        maxStored=int(maxst), scanBars=int(scanbars),
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


@st.cache_resource(ttl=3600, max_entries=4, show_spinner=False)
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


@st.cache_data(ttl=1800, show_spinner=False)
def cached_scan(symbols: tuple, inp_key: str, opts_key: str):
    inp = PineInputs(**json.loads(inp_key)).sanitized()
    return _scan_all(list(symbols), inp, json.loads(opts_key))


def _scan_all(symbols, inp, opts):
    tfs = list(opts["tfs"]) or list(ALL_TFS)
    need = list(dict.fromkeys(list(tfs) + ["75m", "1d", "1w", "1M"]))
    frames = cached_frames(tuple(symbols), tuple(need))
    rows, snaps, atrs, ltps, errs = [], {}, {}, {}, {}
    rej_all: Dict[str, int] = {}
    created_all = 0
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
                created_all += int(r.get("created", 0))
                for k, v in (r.get("rej") or {}).items():
                    rej_all[k] = rej_all.get(k, 0) + int(v)

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
                band_tf=btf, at=datetime.now(IST), scanned=len(jobs),
                rej=rej_all, created=created_all)


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
    t = max(0.0, min(1.0, (float(sc) - 3.0) / 7.0))
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

_STATE_ORDER = {"Fresh": 0, "Tested": 1, "Broken": 2}


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
        _th("TF"), _th("Zone Side"), _th("Pattern"), _th("Status"),
        _th("Score /10"), _th("Power"),
        _th("Multi-TF Highlight"),
        _th("Proximal (₹)"), _th("Distal (₹)"), _th("Current LTP"),
        _th("Distance", dir0="asc"),
    ]
    if extra:
        heads += [_th("Entry"), _th("SL"), _th("T1"), _th("T2"), _th("Qty"), _th("Boring"),
                  _th("Rules /5"), _th("Inst /4"), _th("Pulse · Trend"), _th("Room"),
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
            _td(f'{int(r["score"])}', int(r["score"]), "ctr",
                style=f"background:{_score_bg(float(r['score']))};color:#111;font-weight:700",),
            _td(f'{"★ " if r.get("best") else ""}1:{float(r.get("pow_rr", 0)):.1f}'
                f'{" · 1 LO" if r.get("single_lo") else ""}',
                float(r.get("pow_rr", 0)), "ctr" + (" gs-star" if r.get("best") else "")),
            _td(mtf_cell, mtf_n),
            _td(f'{float(r["prox"]):,.2f}', float(r["prox"]), "num"),
            _td(f'{float(r["dist"]):,.2f}', float(r["dist"]), "num"),
            _td(ltp_txt, ltp if np.isfinite(ltp) else -1e18, "num"),
            _td(_dist_bar(float(r["dist_pct"]), range_pct), float(r["dist_pct"])),
        ]
        if extra:
            rm = r.get("room")
            rm_ok = rm is not None and not (isinstance(rm, float) and pd.isna(rm))
            cells += [
                _td(f'{float(r["entry"]):,.2f}', float(r["entry"]), "num"),
                _td(f'{float(r["sl"]):,.2f}', float(r["sl"]), "num"),
                _td(f'{float(r["tp"]):,.2f}', float(r["tp"]), "num"),
                _td(f'{float(r["t5"]):,.2f}', float(r["t5"]), "num"),
                _td(f'{int(r["qty"])}', int(r["qty"]), "num"),
                _td(f'{int(r["base_count"])}', int(r["base_count"]), "ctr"),
                _td(f'{int(r.get("notes_pass", 0))}', int(r.get("notes_pass", 0)), "ctr"),
                _td(f'{int(r.get("inst", 0))}', int(r.get("inst", 0)), "ctr"),
                _td(f'{r.get("trend_tf", "")} {r.get("pulse", "")} {r.get("trend", "")}',
                    str(r.get("pulse", "")), "ctr " + ("gs-ok" if r.get("action_ok") else "gs-sm")),
                _td(f'1:{float(rm):.1f}' if rm_ok else "open", float(rm) if rm_ok else 999.0, "ctr gs-sm"),
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
        "T1": df["tp"].round(2),
        "T2": df["t5"].round(2),
        "दूरी %": df["dist_pct"].round(2),
        "स्कोर /10": df["score"].astype(int),
        "फ़ैसला": df["verdict"],
        "Power (1:x)": df["pow_rr"].round(1),
        "★ Best": df["best"].map(lambda v: "★" if v else ""),
        "Rules /5": df["notes_pass"].astype(int),
        "Inst /4": df["inst"].astype(int),
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
    if not rej:
        return ""
    top = sorted(rej.items(), key=lambda kv: -kv[1])[:3]
    return " · सबसे ज़्यादा reject: " + ", ".join(f"{k} ({v})" for k, v in top)


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
        st.warning("इस रन में कोई ज़ोन नहीं बना — ⚙️ इनपुट → 🔓 ढीला दबाएँ, या "
                   "Power न्यूनतम 1:3 को 1:2 करें।" + _diag_text(res))
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

    st.caption(f"ज़ोन दिख रहे: {len(f)} · बने (सभी TF): {res.get('created', 0)}" + _diag_text(res))
    if not f.empty:
        try:
            scale = float(max(1.0, f["dist_pct"].quantile(0.90)))
        except Exception:                                        # noqa: BLE001
            scale = 3.0
        _html(sheet_html(f, uo.get("show_mcap", True), 50000.0, extra=extra_cols,
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
