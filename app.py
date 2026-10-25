from __future__ import annotations

import logging as _logging
_logging.getLogger("yfinance").setLevel(_logging.CRITICAL)

import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from io import BytesIO, StringIO
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

# Google Sheets स्टाइल कॉम्पैक्ट लेआउट
st.set_page_config(page_title="Pine Zone Scanner (Sheets View)", page_icon="📊",
                   layout="wide", initial_sidebar_state="collapsed")

IST = ZoneInfo("Asia/Kolkata")

# ===================================================================== CSS (Google Sheets Theme)
st.markdown("""
<style>
:root { color-scheme: dark; }
.stApp { background:#0d1117; color:#c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
[data-testid="stAppViewContainer"] .block-container { max-width: 100vw; padding: 1.5rem 1rem 1rem; }
[data-testid="stHeader"] { background: #161b22; height: 2.5rem; }

/* Google Sheets Toolbar Container */
.sheet-toolbar {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
}

/* Compact Widget Inputs */
div[data-testid="stTextInput"] input, div[data-testid="stSelectbox"] select {
    background-color: #0d1117 !important;
    border: 1px solid #30363d !important;
    color: #e6edf3 !important;
    font-size: 0.82rem !important;
    height: 2rem !important;
    padding: 2px 8px !important;
}

/* Compact Dataframe - Spreadsheet Grid Appearance */
[data-testid="stDataFrame"] {
    border: 1px solid #30363d;
    background: #0d1117;
}

/* Status Bar */
.sheet-statusbar {
    background: #161b22;
    border-top: 1px solid #30363d;
    font-size: 0.75rem;
    color: #8b949e;
    padding: 4px 10px;
    display: flex;
    justify-content: space-between;
}
</style>
""", unsafe_allow_html=True)

# ===================================================================== CONFIG & CONSTANTS
TIMEFRAMES = ("3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h", "4h", "1d", "1w", "1M")
MINUTE_TFS = {"3m", "5m", "10m", "15m", "30m", "75m"}
HOUR_TFS = {"1h", "2h", "4h"}
DAY_TFS = {"1d", "1w", "1M"}

FNO_FALLBACK = (
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "SBIN", "BHARTIARTL",
    "AXISBANK", "KOTAKBANK", "TATAMOTORS", "BAJFINANCE", "MARUTI", "SUNPHARMA", "TITAN"
)

GLOBAL_INSTRUMENTS = (
    {"key": "NIFTY 50", "yahoo": "^NSEI", "tv": "NSE:NIFTY", "label": "NIFTY 50"},
    {"key": "BANKNIFTY", "yahoo": "^NSEBANK", "tv": "NSE:BANKNIFTY", "label": "NIFTY BANK"},
    {"key": "CRUDE OIL", "yahoo": "CL=F", "tv": "NYMEX:CL1!", "label": "Crude Oil"},
    {"key": "GOLD", "yahoo": "GC=F", "tv": "COMEX:GC1!", "label": "Gold Futures"},
    {"key": "USD/INR", "yahoo": "INR=X", "tv": "FX_IDC:USDINR", "label": "USD/INR"}
)

OHLCV = ("Open", "High", "Low", "Close", "Volume")

# ===================================================================== DATA UTILS
def normalize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(OHLCV))
    f = df.copy()
    if isinstance(f.columns, pd.MultiIndex):
        f.columns = [str(c[-1]) for c in f.columns]
    f.rename(columns={c: c.capitalize() for c in f.columns if c.capitalize() in OHLCV}, inplace=True)
    if "Close" not in f.columns:
        return pd.DataFrame(columns=list(OHLCV))
    for c in ("Open", "High", "Low"):
        if c not in f.columns:
            f[c] = f["Close"]
    if "Volume" not in f.columns:
        f["Volume"] = 0.0
    f = f.loc[:, list(OHLCV)].dropna(subset=["Open", "High", "Low", "Close"])
    idx = pd.DatetimeIndex(pd.to_datetime(f.index, errors="coerce"))
    f = f.loc[~idx.isna()].copy()
    idx = idx[~idx.isna()]
    f.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    return f.loc[~f.index.duplicated(keep="last")].sort_index()

def resample_candles(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    df = normalize_ohlcv(df)
    if df.empty:
        return df
    if tf == "1d":
        return df
    rule_map = {"3m": "3min", "5m": "5min", "10m": "10min", "15m": "15min", "30m": "30min",
                "75m": "75min", "1h": "60min", "2h": "120min", "4h": "240min"}
    rule = rule_map.get(tf)
    if not rule:
        return df
    return df.resample(rule).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna().sort_index()

# ===================================================================== CORE ENGINE (ZONE SCAN)
def detect_zones(df: pd.DataFrame, tf: str, symbol: str) -> list[dict]:
    if len(df) < 20:
        return []
    c = df["Close"].to_numpy()
    o = df["Open"].to_numpy()
    h = df["High"].to_numpy()
    l = df["Low"].to_numpy()
    zones = []
    
    # Fast Zone Vector Scanning
    ltp = c[-1]
    for i in range(len(df) - 4, 3, -1):
        # Demand (RBR / DBR)
        if c[i] > o[i] and (h[i] - l[i]) > 0:
            body = abs(c[i] - o[i])
            if body / (h[i] - l[i]) > 0.6:  # Leg Out Bullish
                base_hi = max(h[i-1], h[i-2])
                base_lo = min(l[i-1], l[i-2])
                if c[i] > base_hi:
                    dist_pct = round(abs(ltp - base_hi) / ltp * 100, 2)
                    zones.append({
                        "Symbol": symbol, "TF": tf, "Zone": "🟢 Demand", "Pattern": "DBR/RBR",
                        "State": "Fresh" if ltp > base_hi else "Tested",
                        "Score": 85, "Proximal": round(base_hi, 2), "Distal": round(base_lo, 2),
                        "LTP": round(ltp, 2), "Gap %": dist_pct, "Created": df.index[i].strftime("%d-%b %H:%M")
                    })
                    break
        # Supply (DBD / RBD)
        if o[i] > c[i] and (h[i] - l[i]) > 0:
            body = abs(c[i] - o[i])
            if body / (h[i] - l[i]) > 0.6:  # Leg Out Bearish
                base_hi = max(h[i-1], h[i-2])
                base_lo = min(l[i-1], l[i-2])
                if c[i] < base_lo:
                    dist_pct = round(abs(ltp - base_lo) / ltp * 100, 2)
                    zones.append({
                        "Symbol": symbol, "TF": tf, "Zone": "🔴 Supply", "Pattern": "RBD/DBD",
                        "State": "Fresh" if ltp < base_lo else "Tested",
                        "Score": 85, "Proximal": round(base_lo, 2), "Distal": round(base_hi, 2),
                        "LTP": round(ltp, 2), "Gap %": dist_pct, "Created": df.index[i].strftime("%d-%b %H:%M")
                    })
                    break
    return zones

# ===================================================================== BATCH DOWNLOAD
@st.cache_data(ttl=180, show_spinner=False)
def load_market_data(symbols: tuple[str, ...], tf_group: str):
    import yfinance as yf
    tickers = [f"{s}.NS" for s in symbols]
    interval = "1m" if tf_group == "intraday" else "1d"
    period = "5d" if tf_group == "intraday" else "1y"
    
    try:
        raw = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                          progress=False, threads=True, timeout=12)
        out = {}
        for s in symbols:
            tk = f"{s}.NS"
            try:
                frame = raw[tk] if len(symbols) > 1 else raw
                out[s] = normalize_ohlcv(frame)
            except Exception:
                out[s] = pd.DataFrame()
        return out
    except Exception:
        return {s: pd.DataFrame() for s in symbols}

# ===================================================================== UI - TOOLBAR (GOOGLE SHEETS STYLE)
ss = st.session_state

# Toolbar Controls Row (एक ही रो में सभी कंट्रोल्स)
with st.container():
    c1, c2, c3, c4, c5, c6, c7 = st.columns([1.2, 1.8, 1.2, 1.2, 1.2, 1.5, 0.8])
    with c1:
        universe = st.selectbox("📁 Universe", ["Top 20", "Top 50", "Global"], index=0)
    with c2:
        selected_tfs = st.multiselect("⏱️ Timeframe", list(TIMEFRAMES), default=["15m", "1h", "1d"])
    with c3:
        side_filter = st.selectbox("⚖️ Side", ["All", "🟢 Demand", "🔴 Supply"], index=0)
    with c4:
        state_filter = st.selectbox("📌 State", ["All", "Fresh", "Tested"], index=1)
    with c5:
        sort_distance = st.selectbox("📏 Distance", ["Nearest ↑", "Farthest ↓"], index=0)
    with c6:
        query = st.text_input("🔍 Search", placeholder="e.g. RELIANCE, SBIN")
    with c7:
        st.write("")
        st.write("")
        refresh = st.button("🔄 Scan", type="primary", use_container_width=True)

# Select Universe Pool
symbols_to_scan = FNO_FALLBACK[:20] if universe == "Top 20" else FNO_FALLBACK
tf_group = "intraday" if any(t in MINUTE_TFS or t in HOUR_TFS for t in selected_tfs) else "daily"

# Execution
with st.spinner("Processing Sheets Data Grid..."):
    data_bundle = load_market_data(symbols_to_scan, tf_group)
    all_zones = []
    
    for sym in symbols_to_scan:
        raw_df = data_bundle.get(sym)
        if raw_df is None or raw_df.empty:
            continue
        for tf in selected_tfs:
            resampled = resample_candles(raw_df, tf)
            zones = detect_zones(resampled, tf, sym)
            all_zones.extend(zones)

df_grid = pd.DataFrame(all_zones)

# ===================================================================== FILTERING & GOOGLE SHEETS DISPLAY
if not df_grid.empty:
    if side_filter != "All":
        df_grid = df_grid[df_grid["Zone"] == side_filter]
    if state_filter != "All":
        df_grid = df_grid[df_grid["State"] == state_filter]
    if query.strip():
        df_grid = df_grid[df_grid["Symbol"].str.contains(query.strip().upper(), na=False)]
    
    ascending = True if sort_distance == "Nearest ↑" else False
    df_grid = df_grid.sort_values(by="Gap %", ascending=ascending).reset_index(drop=True)

    # Google Sheets Table View Render
    st.dataframe(
        df_grid,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol (Ticker)", width="medium"),
            "TF": st.column_config.TextColumn("TF", width="small"),
            "Zone": st.column_config.TextColumn("Zone Side", width="small"),
            "State": st.column_config.TextColumn("Status", width="small"),
            "Proximal": st.column_config.NumberColumn("Proximal (₹)", format="%.2f"),
            "Distal": st.column_config.NumberColumn("Distal (₹)", format="%.2f"),
            "LTP": st.column_config.NumberColumn("Current LTP", format="₹%.2f"),
            "Gap %": st.column_config.ProgressColumn("Distance Gap %", min_value=0, max_value=15, format="%.2f%%"),
            "Created": st.column_config.TextColumn("Created Date", width="medium"),
        },
        use_container_width=True,
        hide_index=False,
        height=580
    )
    
    # Status Bar
    st.markdown(f"""
    <div class="sheet-statusbar">
        <span>Rows: <b>{len(df_grid)}</b> | Active Universe: {universe}</span>
        <span>Auto-sync: Ready | Timeframes Active: {', '.join(selected_tfs)}</span>
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("चयनित फ़िल्टर के आधार पर कोई ज़ोन उपलब्ध नहीं है। कृपया टाइमफ्रेम या Universe बदलें।")
