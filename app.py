from __future__ import annotations

import logging as _logging
_logging.getLogger("yfinance").setLevel(_logging.CRITICAL)

import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Pine v6 Zone Scanner",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

IST = ZoneInfo("Asia/Kolkata")

# ===================================================================== CSS (Google Sheets Layout)
st.markdown("""
<style>
:root { color-scheme: dark; }
.stApp { background:#0d1117; color:#c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
[data-testid="stAppViewContainer"] .block-container { max-width: 100vw; padding: 1.5rem 1rem 1rem; }
[data-testid="stHeader"] { background: #161b22; height: 2.5rem; }

div[data-testid="stTextInput"] input, div[data-testid="stSelectbox"] select {
    background-color: #0d1117 !important;
    border: 1px solid #30363d !important;
    color: #e6edf3 !important;
    font-size: 0.82rem !important;
    height: 2rem !important;
    padding: 2px 8px !important;
}

[data-testid="stDataFrame"] {
    border: 1px solid #30363d;
    background: #0d1117;
}

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

# ===================================================================== 13 TIMEFRAMES
TIMEFRAMES = (
    "3m", "5m", "10m", "15m", "30m", "75m",
    "1h", "2h", "4h", "6h", "1d", "1w", "1M"
)
MINUTE_TFS = {"3m", "5m", "10m", "15m", "30m", "75m"}
HOUR_TFS = {"1h", "2h", "4h", "6h"}
DAY_TFS = {"1d", "1w", "1M"}

TV_INTERVAL = {
    "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
    "75m": "75", "1h": "60", "2h": "120", "4h": "240", "6h": "360",
    "1d": "D", "1w": "W", "1M": "M"
}

def tradingview_link(symbol: str, tf: str, is_future: bool = False) -> str:
    interval = TV_INTERVAL.get(tf, "15")
    tv_sym = f"NSE:{symbol}1!" if is_future else f"NSE:{symbol}"
    return f"https://in.tradingview.com/chart/?symbol={quote(tv_sym, safe='')}&interval={interval}"

INDEX_EXCLUSIONS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYIT", "NIFTYNXT50"}

# ===================================================================== AUTO-SYNC LIVE F&O
@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_verified_nse_fno_list() -> tuple[str, ...]:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,application/json,*/*"}
    try:
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            df = pd.read_csv(io.StringIO(res.text), low_memory=False)
            cond = (
                (df["SEM_SEGMENT"].astype(str).str.upper() == "NSE_FNO") &
                (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "FUTSTK")
            )
            raw_syms = df[cond]["SEM_UNDERLYING_SYMBOL"].dropna().unique()
            clean = [str(s).strip().upper() for s in raw_syms if str(s).strip() and str(s).strip().upper() not in INDEX_EXCLUSIONS and not any(str(s).strip().upper().startswith(x) for x in ("NIFTY", "BANKNIFTY"))]
            clean = sorted(list(set(clean)))
            if len(clean) >= 150:
                return tuple(clean)
    except Exception:
        pass

    today = datetime.now(IST).date()
    for offset in range(5):
        d = today - timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        udiff_url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
        try:
            r = requests.get(udiff_url, headers=headers, timeout=5)
            if r.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    for name in z.namelist():
                        if name.endswith(".csv"):
                            with z.open(name) as f:
                                bdf = pd.read_csv(f, low_memory=False)
                                stks = bdf[bdf["FinInstrmTp"].astype(str).str.upper() == "STF"]["TckrSymb"].dropna().unique()
                                clean = sorted([str(s).strip().upper() for s in stks if str(s).strip() and str(s).strip().upper() not in INDEX_EXCLUSIONS])
                                if len(clean) >= 150:
                                    return tuple(clean)
        except Exception:
            continue

    return tuple(sorted(set("""
    AARTIIND ABB ABBOTINDIA ABCAPITAL ABFRL ACC ADANIENSOL ADANIENT ADANIGREEN
    ADANIPORTS ALKEM AMBER AMBUJACEM ANGELONE APOLLOHOSP APOLLOTYRE ASHOKLEY
    ASIANPAINT ASTRAL ATUL AUBANK AUROPHARMA AXISBANK BAJAJ-AUTO BAJAJFINSV
    BAJFINANCE BALKRISIND BANDHANBNK BANKBARODA BANKINDIA BATAINDIA BEL BERGEPAINT
    BHARATFORG BHARTIARTL BHEL BIOCON BOSCHLTD BPCL BRITANNIA BSE CANBK CANFINHOME
    CDSL CESC CGPOWER CHAMBLFERT CHOLAFIN CIPLA COALINDIA COFORGE COLPAL CONCOR
    CROMPTON CUB CUMMINSIND CYIENT DABUR DALBHARAT DEEPAKNTR DELHIVERY DIVISLAB
    DIXON DLF DMART DRREDDY EICHERMOT ESCORTS EXIDEIND FEDERALBNK FORTIS GAIL
    GLENMARK GMRAIRPORT GODREJCP GODREJPROP GRANULES GRASIM GUJGASLTD HAL HAVELLS
    HCLTECH HDFCAMC HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDCOPPER HINDPETRO
    HINDUNILVR HUDCO ICICIBANK ICICIGI ICICIPRULI IDEA IDFCFIRSTB IEX INDHOTEL
    INDIAMART INDIGO INDUSINDBK INDUSTOWER INFY IOC IONEXCH IRB IRCTC IRFC IREDA
    ITC JINDALSTEL JIOFIN JSWENERGY JSWSTEEL JUBLFOOD KALYANKJIL KANSAINER KEI
    KOTAKBANK KPITTECH LALPATHLAB LAURUSLABS LT LTF LICI LTIM LTTS LUPIN M&M
    M&MFIN MANAPPURAM MARICO MARUTI MAXHEALTH MCDOWELL-N MCX METROPOLIS MFSL
    MGL MOTHERSON MPHASIS MUTHOOTFIN NATIONALUM NAUKRI NBCC NCC NESTLEIND NHPC
    NMDC NTPC NUVAMA OBEROIRLTY OFSS OIL ONGC PAGEIND PATANJALI PERSISTENT PETRONET
    PFC PGEL PHOENIXLTD PIIND PIDILITIND PNB POLICYBZR POLYCAB POWERGRID POWERINDIA
    PRESTIGE PVRINOX RAMCOCEM RBLBANK RECLTD RELIANCE SAIL SBICARD SBILIFE SBIN
    SHREECEM SHRIRAMFIN SIEMENS SOLARINDS SONACOMS SRF SUNPHARMA SUPREMEIND
    SUZLON SWIGGY SYNGENE TATACHEM TATACOMM TATACONSUM TATAELXSI TATAMOTORS TATAPOWER
    TATASTEEL TCS TECHM TIINDIA TITAGARH TITAN TMPV TORNTPHARM TRENT TVSMOTOR
    UBL ULTRACEMCO UNIONBANK UNITDSPR UPL VBL VEDL VOLTAS WIPRO YESBANK ZOMATO ZYDUSLIFE
    """.split())))

# ===================================================================== DATA ENGINE
OHLCV = ("Open", "High", "Low", "Close", "Volume")

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

def pine_rma(src: np.ndarray, length: int) -> np.ndarray:
    alpha = 1.0 / length
    out = np.full_like(src, np.nan)
    if len(src) < length:
        return out
    out[length - 1] = np.mean(src[:length])
    for i in range(length, len(src)):
        out[i] = alpha * src[i] + (1 - alpha) * out[i - 1]
    return out

def get_scan_range_bounds(df_daily: pd.DataFrame, range_type: str = "Weekly") -> tuple[float, float]:
    if df_daily is None or df_daily.empty or len(df_daily) < 15:
        return 0.0, 1e9
    if range_type == "Daily":
        ref = df_daily
    elif range_type == "Weekly":
        ref = df_daily.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    else:
        ref = df_daily.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()

    if len(ref) < 15:
        ref = df_daily

    h = ref["High"].to_numpy()
    l = ref["Low"].to_numpy()
    c = ref["Close"].to_numpy()
    tr = np.zeros(len(ref))
    tr[0] = h[0] - l[0]
    for i in range(1, len(ref)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    atr_series = pine_rma(tr, 14)
    atr = atr_series[-1] if not np.isnan(atr_series[-1]) else (h[-1] - l[-1])
    return max(0.0, float(l[-1] - atr)), float(h[-1] + atr)

def resample_candles(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    df = normalize_ohlcv(df)
    if df.empty or tf == "1d":
        return df
    rule_map = {
        "3m": "3min", "5m": "5min", "10m": "10min", "15m": "15min", "30m": "30min",
        "75m": "75min", "1h": "60min", "2h": "120min", "4h": "240min", "6h": "360min",
        "1w": "W-FRI", "1M": "ME"
    }
    rule = rule_map.get(tf)
    return df.resample(rule).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna().sort_index() if rule else df

# ===================================================================== STRICT PINE V6 ENGINE
@dataclass
class ZoneRecord:
    proxVal: float
    distVal: float
    isDemand: bool
    patternType: str
    state: str
    touchCount: int
    createdBarIndex: int
    legOutMidLevel: float
    timestamp: Any
    densityScore: int

class StrictPineV6Engine:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.o = df["Open"].to_numpy(dtype=float)
        self.h = df["High"].to_numpy(dtype=float)
        self.l = df["Low"].to_numpy(dtype=float)
        self.c = df["Close"].to_numpy(dtype=float)
        self.v = df["Volume"].to_numpy(dtype=float)
        self.n = len(df)
        self.dow = df.index.dayofweek.to_numpy() if self.n else np.array([], dtype=int)
        
        # TR & RMA ATR(14)
        tr = np.zeros(self.n)
        if self.n > 0:
            tr[0] = self.h[0] - self.l[0]
            for i in range(1, self.n):
                tr[i] = max(self.h[i] - self.l[i], abs(self.h[i] - self.c[i-1]), abs(self.l[i] - self.c[i-1]))
        self.tr = tr
        self.atr = pine_rma(tr, 14)
        self.vol_sma = pd.Series(self.v).rolling(20).mean().to_numpy()

    def wick_pct(self, i: int) -> float:
        r = self.h[i] - self.l[i]
        return 0.0 if r == 0 else ((self.h[i] - max(self.o[i], self.c[i])) + (min(self.o[i], self.c[i]) - self.l[i])) / r

    def body_pct(self, i: int) -> float:
        r = self.h[i] - self.l[i]
        return 0.0 if r == 0 else abs(self.c[i] - self.o[i]) / r

    def is_bull(self, i: int) -> bool: return self.c[i] > self.o[i]
    def is_bear(self, i: int) -> bool: return self.o[i] > self.c[i]

    def _candidate(self, i: int, bc: int) -> Optional[dict]:
        li = i - (bc + 1)
        pi = li - 1
        if pi < 0 or np.isnan(self.atr[li]):
            return None

        inTR = self.tr[li]
        inLow, inHigh, inClose = self.l[li], self.h[li], self.c[li]
        inRange = inHigh - inLow
        inBull, inBear = self.is_bull(li), self.is_bear(li)

        if inRange == 0 or self.body_pct(li) < 0.60:
            return None

        # Opposite Cover Check
        opposite = (inBull and self.is_bear(pi)) or (inBear and self.is_bull(pi))
        prevHi = max(self.o[pi], self.c[pi])
        prevLo = min(self.o[pi], self.c[pi])
        overlap = max(0.0, min(prevHi, inHigh) - max(prevLo, inLow))
        if opposite and (overlap / inRange) >= 0.50:
            return None

        bullClv = (inClose - inLow) / inRange
        bearClv = (inHigh - inClose) / inRange

        baseOK = True
        baseTR = 0.0
        baseHigh = -1.0
        baseLow = 1e12
        for b in range(1, bc + 1):
            idx = i - b
            if np.isnan(self.atr[idx]) or self.tr[idx] > 1.0 * self.atr[idx]:
                baseOK = False
                break
            baseTR = max(baseTR, self.tr[idx])
            baseHigh = max(baseHigh, self.h[idx])
            baseLow = min(baseLow, self.l[idx])

        if not baseOK or baseTR == 0:
            return None

        effectiveMult = 1.5 if bc == 1 else 2.0
        if inTR < effectiveMult * baseTR or not (inTR >= 1.0 * self.atr[li]):
            return None

        outTR = self.tr[i]
        demand = self.is_bull(i)
        supply = self.is_bear(i)
        if not (demand or supply):
            return None

        explosive = outTR >= 1.2 * self.atr[i]
        wickOK = self.wick_pct(i) <= 0.30
        hierarchy = (outTR >= 1.0 * inTR) and (inTR > baseTR)
        volumeOK = self.v[i] > self.v[li]
        overnight = (self.dow[i] != self.dow[i-1]) if i > 0 else False

        if demand:
            gap = self.l[i] > baseHigh
            imbalance = gap or (self.c[i] > inHigh)
        else:
            gap = self.h[i] < baseLow
            imbalance = gap or (self.c[i] < inLow)

        bodyEngulf = min(self.o[i], self.c[i]) <= baseLow and max(self.o[i], self.c[i]) >= baseHigh
        if bodyEngulf and not gap:
            return None

        rbr = inBull and bullClv >= 0.60 and demand
        dbr = inBear and bearClv >= 0.60 and demand
        dbd = inBear and bearClv >= 0.60 and supply
        rbd = inBull and bullClv >= 0.60 and supply

        if not ((rbr or dbr or dbd or rbd) and explosive and wickOK and hierarchy and volumeOK and imbalance):
            return None

        score = 0
        if bc == 1: score += 15
        if inTR >= 1.5 * self.atr[li]: score += 10
        if outTR >= 2.0 * inTR: score += 15
        if inTR >= 2.0 * baseTR and outTR >= 2.0 * inTR: score += 15
        if self.v[i] > self.vol_sma[i]: score += 10

        outRange = self.h[i] - self.l[i]
        bodyPos = ((self.c[i] - self.l[i]) / outRange) if (demand and outRange > 0) else (((self.h[i] - self.c[i]) / outRange) if outRange > 0 else 0.0)
        if demand:
            if dbr:
                if bodyPos >= 0.80 or self.body_pct(i) >= 0.60: score += 15
            elif bodyPos >= 0.80: score += 15
        elif bodyPos >= 0.80: score += 15

        oppositeBase = any((demand and self.is_bear(i - b)) or (supply and self.is_bull(i - b)) for b in range(1, bc + 1))
        if oppositeBase: score += 10
        score += 10
        if gap: score += 10
        if overnight and gap: score += 15

        if score < 40:
            return None

        prox = baseHigh if demand else baseLow
        dist = baseLow if demand else baseHigh
        # Pine v6: mid = high - testedLegOutRetracePct * (high - low) [1.0 retrace = outLow]
        mid = (self.h[i] - 1.0 * (self.h[i] - self.l[i])) if demand else (self.l[i] + 1.0 * (self.h[i] - self.l[i]))
        pattern = "RBR" if rbr else ("DBR" if dbr else ("DBD" if dbd else "RBD"))
        return {
            "prox": prox, "dist": dist, "mid": mid, "demand": demand,
            "score": score, "pattern": pattern, "bc": bc
        }

    def run_pine_engine(self) -> List[ZoneRecord]:
        active_zones: List[ZoneRecord] = []
        min_bar = max(14, 3 + 3, 11)

        for i in range(self.n):
            low_i, high_i = self.l[i], self.h[i]

            # 1. पहले से बने ज़ोन्स का स्टेट अपडेट (कैंडल i द्वारा टेस्ट)
            for z in active_zones:
                if z.state == "Broken":
                    continue
                
                # Pine Script Rule: ज़ोन अपनी बनने वाली कैंडल पर टेस्ट नहीं हो सकता
                if i > z.createdBarIndex:
                    if z.state == "Fresh":
                        if z.isDemand:
                            if low_i <= z.distVal:
                                z.state = "Broken"
                            elif low_i <= z.legOutMidLevel:
                                z.state = "Tested"
                                z.touchCount += 1
                        else:
                            if high_i >= z.distVal:
                                z.state = "Broken"
                            elif high_i >= z.legOutMidLevel:
                                z.state = "Tested"
                                z.touchCount += 1
                    elif z.state == "Tested":
                        if z.isDemand:
                            if low_i <= z.distVal:
                                z.state = "Broken"
                            elif low_i <= z.legOutMidLevel:
                                z.touchCount += 1
                        else:
                            if high_i >= z.distVal:
                                z.state = "Broken"
                            elif high_i >= z.legOutMidLevel:
                                z.touchCount += 1

                    # maxTestedCount = 1
                    if z.state == "Tested" and z.touchCount > 1:
                        z.state = "Broken"

            # 2. कैंडल i पर नए ज़ोन की खोज (Scan Bar)
            if i >= min_bar and not np.isnan(self.atr[i]):
                found = False
                for bc in range(1, 4):
                    if found:
                        break
                    cand = self._candidate(i, bc)
                    if cand is None:
                        continue
                    found = True
                    
                    # Duplicate check (0.25 * ATR)
                    duplicate = False
                    for old in reversed(active_zones):
                        if old.state == "Broken":
                            continue
                        if old.isDemand == cand["demand"] and abs(old.proxVal - cand["prox"]) < self.atr[i] * 0.25:
                            duplicate = True
                            break
                    if duplicate:
                        continue

                    # नया ज़ोन हमेशा 'Fresh' स्टेट में ही बनेगा
                    z = ZoneRecord(
                        proxVal=float(cand["prox"]),
                        distVal=float(cand["dist"]),
                        isDemand=bool(cand["demand"]),
                        patternType=str(cand["pattern"]),
                        state="Fresh",
                        touchCount=0,
                        createdBarIndex=i,
                        legOutMidLevel=float(cand["mid"]),
                        timestamp=self.df.index[i],
                        densityScore=int(cand["score"])
                    )
                    active_zones.append(z)

        # केवल शुद्ध Active Zones (Fresh & Tested) ही बाहर जाएंगे, Broken पूरी तरह बाहर
        return [z for z in active_zones if z.state in ("Fresh", "Tested")]

# ===================================================================== SAFE BATCH DATA
def _download_safe_batch(tickers: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out = {}
    try:
        raw = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                          progress=False, threads=False, timeout=12)
        for tk in tickers:
            sym = tk.replace(".NS", "")
            try:
                frame = raw[tk] if len(tickers) > 1 else raw
                clean = normalize_ohlcv(frame)
                if not clean.empty:
                    out[sym] = clean
            except Exception:
                pass
    except Exception:
        pass
    return out

@st.cache_data(ttl=120, show_spinner=False)
def load_all_market_data(tf_group: str, symbols: tuple[str, ...]) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    interval = "1m" if tf_group == "intraday" else "1d"
    period = "5d" if tf_group == "intraday" else "2y"

    chunk_size = 15
    chunks = [list(symbols)[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    tf_data: dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_download_safe_batch, [f"{s}.NS" for s in part], period, interval) for part in chunks]
        for fut in futures:
            try:
                tf_data.update(fut.result())
            except Exception:
                pass

    daily_data: dict[str, pd.DataFrame] = {}
    if tf_group == "intraday":
        with ThreadPoolExecutor(max_workers=4) as pool:
            d_futures = [pool.submit(_download_safe_batch, [f"{s}.NS" for s in part], "1y", "1d") for part in chunks]
            for fut in d_futures:
                try:
                    daily_data.update(fut.result())
                except Exception:
                    pass
    else:
        daily_data = tf_data

    return tf_data, daily_data

# ===================================================================== TOOLBAR ROW
live_fno_symbols = get_verified_nse_fno_list()
fno_set = set(live_fno_symbols)

with st.container():
    c1, c2, c3, c4, c5, c6, c7 = st.columns([1.6, 2.0, 1.1, 0.9, 0.9, 1.2, 0.8])
    with c1:
        universe_choice = st.selectbox("📁 Universe", [f"NSE All Future Stocks ({len(live_fno_symbols)})"], index=0)
    with c2:
        selected_tfs = st.multiselect("⏱️ Timeframe (13 Total)", list(TIMEFRAMES), default=["15m", "1h", "1d"])
    with c3:
        range_choice = st.selectbox("🎯 Scan Range", ["Weekly (Default)", "Daily", "Monthly"], index=0)
    with c4:
        side_filter = st.selectbox("⚖️ Side", ["All", "🟢 Demand", "🔴 Supply"], index=0)
    with c5:
        state_filter = st.selectbox("📌 Status", ["All", "Fresh", "Tested"], index=0)
    with c6:
        search_query = st.text_input("🔍 Search", placeholder="RELIANCE, SWIGGY...")
    with c7:
        st.write("")
        st.write("")
        rescan = st.button("🚀 Scan", type="primary", use_container_width=True)

if not selected_tfs:
    st.warning("कृपया कम से कम एक Timeframe चुनें।")
    st.stop()

# ===================================================================== EXECUTION
range_type = "Weekly" if "Weekly" in range_choice else ("Daily" if "Daily" in range_choice else "Monthly")
tf_group = "intraday" if any(t in MINUTE_TFS or t in HOUR_TFS for t in selected_tfs) else "daily"

with st.spinner(f"Running Exact Pine v6 Engine across {len(live_fno_symbols)} stocks in {range_type} ATR Range..."):
    tf_bundle, daily_bundle = load_all_market_data(tf_group, live_fno_symbols)
    all_raw_zones = []

    for sym, raw_df in tf_bundle.items():
        if sym not in fno_set or raw_df is None or raw_df.empty:
            continue

        df_d = daily_bundle.get(sym, raw_df)
        r_min, r_max = get_scan_range_bounds(df_d, range_type=range_type)

        for tf in selected_tfs:
            resampled = resample_candles(raw_df, tf)
            if len(resampled) < 20:
                continue
            
            engine = StrictPineV6Engine(resampled)
            active_zones = engine.run_pine_engine()
            ltp = float(resampled["Close"].iloc[-1])

            for z in active_zones:
                # ATR Scan Range Check
                if not (r_min <= z.proxVal <= r_max or r_min <= z.distVal <= r_max):
                    continue

                dist_pct = round(abs(ltp - z.proxVal) / ltp * 100, 2)
                all_raw_zones.append({
                    "Equity_Name": sym,
                    "Equity_Link": tradingview_link(sym, tf, is_future=False),
                    "Future_Link": tradingview_link(sym, tf, is_future=True),
                    "TF": tf,
                    "Zone Side": "🟢 Demand" if z.isDemand else "🔴 Supply",
                    "Pattern": z.patternType,
                    "Status": z.state,
                    "Proximal (₹)": round(z.proxVal, 2),
                    "Distal (₹)": round(z.distVal, 2),
                    "Current LTP": round(ltp, 2),
                    "Distance Gap %": dist_pct,
                    "Created Date": z.timestamp.strftime("%d-%b %H:%M"),
                })

df_view = pd.DataFrame(all_raw_zones)

# ===================================================================== MULTI-TF HIGHLIGHT & RENDER
if not df_view.empty:
    tf_counts = df_view.groupby(["Equity_Name", "Zone Side"])["TF"].transform("nunique")
    df_view["Multi-TF Zone"] = tf_counts.apply(
        lambda count: f"🔥 Confluence ({count}x TF)" if count >= 3 else (f"⭐ Multi-TF (2x)" if count == 2 else "—")
    )

    if side_filter != "All":
        df_view = df_view[df_view["Zone Side"] == side_filter]
    if state_filter != "All":
        df_view = df_view[df_view["Status"] == state_filter]
    if search_query.strip():
        parts = [p.strip() for p in re.split(r"[,\s]+", search_query) if p.strip()]
        pat = "|".join(re.escape(p) for p in parts)
        df_view = df_view[df_view["Equity_Name"].str.contains(pat, case=False, na=False, regex=True)]

    df_view = df_view.sort_values(by="Distance Gap %", ascending=True).reset_index(drop=True)

    display_df = df_view[[
        "Equity_Link", "Future_Link", "TF", "Zone Side", "Pattern", "Status",
        "Multi-TF Zone", "Proximal (₹)", "Distal (₹)", "Current LTP", "Distance Gap %", "Created Date"
    ]]

    st.dataframe(
        display_df,
        column_config={
            "Equity_Link": st.column_config.LinkColumn(
                "Equity (Cash)",
                display_text=r"symbol=(?:NSE%3A)?([^&!%]+)",
                width="small"
            ),
            "Future_Link": st.column_config.LinkColumn(
                "Current-Month Future",
                display_text=r"symbol=(?:NSE%3A)?([^&%]+)",
                width="medium"
            ),
            "TF": st.column_config.TextColumn("TF", width="small"),
            "Zone Side": st.column_config.TextColumn("Zone Side", width="small"),
            "Pattern": st.column_config.TextColumn("Pattern", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Multi-TF Zone": st.column_config.TextColumn("Multi-TF Highlight", width="medium"),
            "Proximal (₹)": st.column_config.NumberColumn("Proximal (₹)", format="%.2f"),
            "Distal (₹)": st.column_config.NumberColumn("Distal (₹)", format="%.2f"),
            "Current LTP": st.column_config.NumberColumn("Current LTP", format="₹%.2f"),
            "Distance Gap %": st.column_config.ProgressColumn(
                "Distance Gap %",
                min_value=0.0,
                max_value=10.0,
                format="%.2f%%"
            ),
            "Created Date": st.column_config.TextColumn("Created Date", width="medium"),
        },
        use_container_width=True,
        hide_index=False,
        height=620
    )

    st.markdown(f"""
    <div class="sheet-statusbar">
        <span>Active Valid Zones: <b>{len(display_df)}</b> | Engine: <b>Strict Pine v6 (Broken Fully Excluded)</b></span>
        <span>TradingView charts open cleanly on exact timeframe without MTF lines</span>
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("चयनित फ़िल्टर और ATR रेंज में कोई सक्रिय वैध ज़ोन नहीं मिला।")
