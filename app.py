from __future__ import annotations

import logging as _logging
_logging.getLogger("yfinance").setLevel(_logging.CRITICAL)

import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Pine Zone Scanner",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

IST = ZoneInfo("Asia/Kolkata")

# ===================================================================== CSS (Google Sheets Theme)
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

GLOBAL_INSTRUMENTS = (
    {"key": "NIFTY 50", "yahoo": "^NSEI", "tv_eq": "NSE:NIFTY", "tv_fut": "NSE:NIFTY1!", "label": "NIFTY 50"},
    {"key": "BANKNIFTY", "yahoo": "^NSEBANK", "tv_eq": "NSE:BANKNIFTY", "tv_fut": "NSE:BANKNIFTY1!", "label": "BANKNIFTY"},
    {"key": "CRUDE OIL", "yahoo": "CL=F", "tv_eq": "NYMEX:CL1!", "tv_fut": "NYMEX:CL1!", "label": "CRUDE OIL"},
    {"key": "BRENT CRUDE", "yahoo": "BZ=F", "tv_eq": "NYMEX:BZ1!", "tv_fut": "NYMEX:BZ1!", "label": "BRENT CRUDE"},
    {"key": "GOLD", "yahoo": "GC=F", "tv_eq": "COMEX:GC1!", "tv_fut": "COMEX:GC1!", "label": "GOLD"},
    {"key": "SILVER", "yahoo": "SI=F", "tv_eq": "COMEX:SI1!", "tv_fut": "COMEX:SI1!", "label": "SILVER"},
    {"key": "USD/INR", "yahoo": "INR=X", "tv_eq": "FX_IDC:USDINR", "tv_fut": "FX_IDC:USDINR", "label": "USDINR"},
    {"key": "S&P 500", "yahoo": "^GSPC", "tv_eq": "SP:SPX", "tv_fut": "CME_MINI:ES1!", "label": "S&P 500"},
    {"key": "NASDAQ 100", "yahoo": "^NDX", "tv_eq": "NASDAQ:NDX", "tv_fut": "CME_MINI:NQ1!", "label": "NASDAQ 100"},
    {"key": "DOLLAR INDEX", "yahoo": "DX-Y.NYB", "tv_eq": "TVC:DXY", "tv_fut": "ICEUS:DX1!", "label": "DXY"}
)

TV_INTERVAL = {
    "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
    "75m": "75", "1h": "60", "2h": "120", "4h": "240", "6h": "360",
    "1d": "D", "1w": "W", "1M": "M"
}

def tradingview_link(symbol: str, tf: str, is_future: bool = False, custom_tv: str = "") -> str:
    interval = TV_INTERVAL.get(tf, "15")
    if custom_tv:
        tv_sym = custom_tv
    else:
        tv_sym = f"NSE:{symbol}1!" if is_future else f"NSE:{symbol}"
    encoded = quote(tv_sym, safe="")
    return f"https://in.tradingview.com/chart/?symbol={encoded}&interval={interval}"

# ===================================================================== STRICT NSE F&O AUTO-SYNC
INDEX_EXCLUSIONS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYIT", "NIFTYNXT50"
}

@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_verified_nse_fno_list() -> tuple[str, ...]:
    """NSE Bhavcopy और Dhan Master से केवल शुद्ध Stock Futures (FUTSTK) लाता है"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/csv,application/json,*/*",
    }
    
    # स्रोत 1: Dhan Scrip Master (सख्त FUTSTK फ़िल्टर)
    try:
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            df = pd.read_csv(io.StringIO(res.text), low_memory=False)
            # केवल शुद्ध स्टॉक फ्यूचर्स
            cond = (
                (df["SEM_SEGMENT"].astype(str).str.upper() == "NSE_FNO") &
                (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "FUTSTK")
            )
            fno = df[cond]
            raw_syms = fno["SEM_UNDERLYING_SYMBOL"].dropna().unique()
            clean = []
            for s in raw_syms:
                sym_str = str(s).strip().upper()
                if sym_str and sym_str not in INDEX_EXCLUSIONS and not any(sym_str.startswith(x) for x in ("NIFTY", "BANKNIFTY")):
                    clean.append(sym_str)
            clean = sorted(list(set(clean)))
            if len(clean) >= 150:
                return tuple(clean)
    except Exception:
        pass

    # स्रोत 2: NSE Official FO Bhavcopy
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

    # स्रोत 3: आपातकालीन बैकअप (यदि इंटरनेट ब्लॉक हो)
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
    SUZLON SYNGENE TATACHEM TATACOMM TATACONSUM TATAELXSI TATAMOTORS TATAPOWER
    TATASTEEL TCS TECHM TIINDIA TITAGARH TITAN TMPV TORNTPHARM TRENT TVSMOTOR
    UBL ULTRACEMCO UNIONBANK UNITDSPR UPL VBL VEDL VOLTAS WIPRO YESBANK ZOMATO ZYDUSLIFE
    """.split())))

# ===================================================================== DATA UTILITIES
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
    if not rule:
        return df
    return df.resample(rule).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
    }).dropna().sort_index()

# ===================================================================== ZONE DETECTION
def detect_zones(df: pd.DataFrame, tf: str, symbol: str, is_global: bool = False,
                 tv_eq_custom: str = "", tv_fut_custom: str = "") -> list[dict]:
    if len(df) < 8:
        return []
    c = df["Close"].to_numpy()
    o = df["Open"].to_numpy()
    h = df["High"].to_numpy()
    l = df["Low"].to_numpy()
    zones = []
    
    ltp = c[-1]
    chart_eq_url = tradingview_link(symbol, tf, is_future=False, custom_tv=tv_eq_custom)
    chart_fut_url = tradingview_link(symbol, tf, is_future=True, custom_tv=tv_fut_custom)

    scan_depth = min(len(df) - 2, 7)
    for i in range(len(df) - 2, len(df) - 2 - scan_depth, -1):
        if i < 2:
            break
        span = h[i] - l[i]
        if span <= 0:
            continue
        body = abs(c[i] - o[i])
        
        # Demand Zone
        if c[i] > o[i] and (body / span) >= 0.50:
            base_hi = max(h[i-1], h[i-2])
            base_lo = min(l[i-1], l[i-2])
            if c[i] > base_hi:
                dist_pct = round(abs(ltp - base_hi) / ltp * 100, 2)
                zones.append({
                    "Equity_Link": chart_eq_url,
                    "Future_Link": chart_fut_url,
                    "Equity_Name": symbol,
                    "TF": tf,
                    "Zone Side": "🟢 Demand",
                    "Pattern": "DBR" if o[i-2] > c[i-2] else "RBR",
                    "Status": "Fresh" if ltp >= base_hi else "Tested",
                    "Score": 85,
                    "Proximal (₹)": round(base_hi, 2),
                    "Distal (₹)": round(base_lo, 2),
                    "Current LTP": round(ltp, 2),
                    "Distance Gap %": dist_pct,
                    "Created Date": df.index[i].strftime("%d-%b %H:%M"),
                })
                break

        # Supply Zone
        elif o[i] > c[i] and (body / span) >= 0.50:
            base_hi = max(h[i-1], h[i-2])
            base_lo = min(l[i-1], l[i-2])
            if c[i] < base_lo:
                dist_pct = round(abs(ltp - base_lo) / ltp * 100, 2)
                zones.append({
                    "Equity_Link": chart_eq_url,
                    "Future_Link": chart_fut_url,
                    "Equity_Name": symbol,
                    "TF": tf,
                    "Zone Side": "🔴 Supply",
                    "Pattern": "RBD" if c[i-2] > o[i-2] else "DBD",
                    "Status": "Fresh" if ltp <= base_lo else "Tested",
                    "Score": 85,
                    "Proximal (₹)": round(base_lo, 2),
                    "Distal (₹)": round(base_hi, 2),
                    "Current LTP": round(ltp, 2),
                    "Distance Gap %": dist_pct,
                    "Created Date": df.index[i].strftime("%d-%b %H:%M"),
                })
                break
    return zones

# ===================================================================== BATCH DOWNLOAD
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
def load_all_market_data(universe_type: str, tf_group: str, fno_pool: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    interval = "1m" if tf_group == "intraday" else "1d"
    period = "5d" if tf_group == "intraday" else "2y"

    if universe_type == "Global Markets":
        ticker_map = {item["key"]: item["yahoo"] for item in GLOBAL_INSTRUMENTS}
        import yfinance as yf
        raw = yf.download(list(ticker_map.values()), period=period, interval=interval,
                          group_by="ticker", progress=False, timeout=12)
        out = {}
        for key, tk in ticker_map.items():
            try:
                frame = raw[tk] if len(ticker_map) > 1 else raw
                out[key] = normalize_ohlcv(frame)
            except Exception:
                out[key] = pd.DataFrame()
        return out

    symbols = fno_pool if "NSE All Future" in universe_type else fno_pool[:50]
    
    # 15-15 के सेफ चंक्स में डाउनलोड
    chunk_size = 15
    chunks = [list(symbols)[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    combined: dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_download_safe_batch, [f"{s}.NS" for s in part], period, interval)
            for part in chunks
        ]
        for fut in futures:
            try:
                combined.update(fut.result())
            except Exception:
                pass

    return combined

# ===================================================================== TOOLBAR ROW
live_fno_symbols = get_verified_nse_fno_list()
fno_set = set(live_fno_symbols)  # केवल F&O स्टॉक्स की सख्त पुष्टि के लिए

with st.container():
    c1, c2, c3, c4, c5, c6, c7 = st.columns([1.7, 2.3, 1.0, 1.0, 1.1, 1.2, 0.8])
    with c1:
        universe_choice = st.selectbox(
            "📁 Universe",
            [f"NSE All Future Stocks ({len(live_fno_symbols)})", "Top 50 F&O", "Global Markets"],
            index=0
        )
    with c2:
        selected_tfs = st.multiselect(
            "⏱️ Timeframe (13 Total)",
            list(TIMEFRAMES),
            default=["15m", "1h", "1d"]
        )
    with c3:
        side_filter = st.selectbox("⚖️ Side", ["All", "🟢 Demand", "🔴 Supply"], index=0)
    with c4:
        state_filter = st.selectbox("📌 Status", ["All", "Fresh", "Tested"], index=0)
    with c5:
        sort_mode = st.selectbox("📏 Distance", ["Nearest ↑", "Farthest ↓"], index=0)
    with c6:
        search_query = st.text_input("🔍 Search", placeholder="RELIANCE, SBIN...")
    with c7:
        st.write("")
        st.write("")
        rescan = st.button("🚀 Scan", type="primary", use_container_width=True)

if not selected_tfs:
    st.warning("कृपया कम से कम एक Timeframe चुनें।")
    st.stop()

# ===================================================================== EXECUTION SCAN
tf_group = "intraday" if any(t in MINUTE_TFS or t in HOUR_TFS for t in selected_tfs) else "daily"
univ_key = "NSE All Future Stocks" if "NSE All Future" in universe_choice else universe_choice

with st.spinner(f"Scanning verified {universe_choice} across {', '.join(selected_tfs)}..."):
    data_bundle = load_all_market_data(univ_key, tf_group, live_fno_symbols)
    all_detected_zones = []

    if univ_key == "Global Markets":
        for item in GLOBAL_INSTRUMENTS:
            key = item["key"]
            raw_df = data_bundle.get(key)
            if raw_df is None or raw_df.empty:
                continue
            for tf in selected_tfs:
                resampled = resample_candles(raw_df, tf)
                zones = detect_zones(resampled, tf, item["label"], is_global=True,
                                     tv_eq_custom=item["tv_eq"], tv_fut_custom=item["tv_fut"])
                all_detected_zones.extend(zones)
    else:
        # केवल वही स्टॉक्स स्कैन होंगे जो आधिकारिक F&O सेट (fno_set) में हैं
        for sym, raw_df in data_bundle.items():
            if sym not in fno_set:
                continue
            if raw_df is None or raw_df.empty:
                continue
            for tf in selected_tfs:
                resampled = resample_candles(raw_df, tf)
                zones = detect_zones(resampled, tf, sym)
                all_detected_zones.extend(zones)

df_view = pd.DataFrame(all_detected_zones)

# ===================================================================== DISPLAY (SHEETS FORMAT)
if not df_view.empty:
    if side_filter != "All":
        df_view = df_view[df_view["Zone Side"] == side_filter]
    if state_filter != "All":
        df_view = df_view[df_view["Status"] == state_filter]
    if search_query.strip():
        parts = [p.strip() for p in re.split(r"[,\s]+", search_query) if p.strip()]
        pat = "|".join(re.escape(p) for p in parts)
        df_view = df_view[df_view["Equity_Name"].str.contains(pat, case=False, na=False, regex=True)]

    ascending = True if sort_mode.startswith("Nearest") else False
    df_view = df_view.sort_values(by="Distance Gap %", ascending=ascending).reset_index(drop=True)

    display_df = df_view[[
        "Equity_Link", "Future_Link", "TF", "Zone Side", "Pattern", "Status",
        "Score", "Proximal (₹)", "Distal (₹)", "Current LTP", "Distance Gap %", "Created Date"
    ]]

    st.dataframe(
        display_df,
        column_config={
            # 1. Equity / Cash कॉलम
            "Equity_Link": st.column_config.LinkColumn(
                "Equity (Cash)",
                display_text=r"symbol=(?:NSE%3A)?([^&!%]+)",
                width="small"
            ),
            # 2. Current Month Future कॉलम
            "Future_Link": st.column_config.LinkColumn(
                "Current-Month Future",
                display_text=r"symbol=(?:NSE%3A)?([^&%]+)",
                width="medium"
            ),
            "TF": st.column_config.TextColumn("TF", width="small"),
            "Zone Side": st.column_config.TextColumn("Zone Side", width="small"),
            "Pattern": st.column_config.TextColumn("Pattern", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Score": st.column_config.NumberColumn("Score", format="%d", width="small"),
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
        <span>Rows: <b>{len(display_df)}</b> zones | Active Pool: <b>{len(live_fno_symbols)} Pure F&O Stocks (Auto-synced)</b></span>
        <span>Strict F&O Filter Enabled (No outside symbols) | Left: Cash Chart | Right: Futures Chart</span>
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("चयनित फिल्टर के आधार पर कोई ज़ोन नहीं मिला।")
