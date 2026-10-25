"""
Pine v6 Zone Scanner (Strict Parity) — NSE F&O universe.

Engine = line-by-line port of the Pine Script v6 indicator "Zone MTF"
(iincomesense). Mapping of Pine parts to this file:

  * Every Pine `input.*` -> field of `PineParams` (same default, same label,
    same group). Sidebar exposes all of them. Inputs that Pine declares but
    never uses in its logic are kept and marked "Pine: declared, not used".
  * `f_candidate(bc)`             -> find_candidate()        (pure, shared)
  * Swing pivots / breaks         -> Bars.ph_conf / pl_conf + run_chart_engine()
  * Chart-TF zone engine          -> run_chart_engine()  (duplicate, behind,
                                     base-break, swing-break, state update,
                                     swing expiry, maxStored shift)
  * MTF engine `f_mtf_engine()`   -> run_mtf_engine()    (no swing/behind,
                                     Broken removed each bar)
  * MTF confirmed value           -> engine run on complete bars only; the
                                     latest active demand/supply per TF
  * MTF visibility (mtfNearPct / filterDistantMTF / showMTF) -> run_scan()

Pine execution order per bar is kept: candidate + zone creation first,
then the SAME-bar state update (a new zone can become Tested on its own
leg-out bar, exactly as in Pine), then swing expiry.

App-only parts (not in Pine): NSE F&O universe, yfinance data, ATR scan
range filter, Qty-at-risk column (uses Pine's accountCapital / riskPct),
and the "Multi-TF Zone" column (now based on Pine MTF lines).
"""

from __future__ import annotations

import io
import logging as _logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

_logging.getLogger("yfinance").setLevel(_logging.CRITICAL)

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Pine v6 Zone Scanner (Strict Parity)",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

IST = ZoneInfo("Asia/Kolkata")
NAN = float("nan")
HARD_MAX_BASE_COUNT = 3                 # Pine: HARD_MAX_BASE_COUNT
MIN_SCAN_BARS = 25                      # app: minimum bars before a TF is scanned
SESSION_START_MIN = 9 * 60 + 15         # NSE cash session open (09:15 IST)
SESSION_END = (15, 30)                  # NSE cash session close (15:30 IST)
MS_PER_DAY = 86_400_000
OHLCV = ("Open", "High", "Low", "Close", "Volume")
EMPTY = pd.DataFrame(columns=list(OHLCV))

# ===================================================================== CSS
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
[data-testid="stDataFrame"] { border: 1px solid #30363d; background: #0d1117; }
.sheet-statusbar {
    background: #161b22; border-top: 1px solid #30363d; font-size: 0.75rem;
    color: #8b949e; padding: 4px 10px; display: flex; justify-content: space-between;
}
</style>
""", unsafe_allow_html=True)

# ===================================================================== TIMEFRAMES
TIMEFRAMES = ("3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M")
TF_MINUTES = {"3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "75m": 75,
              "1h": 60, "2h": 120, "4h": 240, "6h": 360}
MINUTE_TFS = {"3m", "5m", "10m", "15m", "30m", "75m"}   # built from 1m bars
HOUR_TFS = {"1h", "2h", "4h", "6h"}                     # built from 60m bars
DAY_TFS = {"1d", "1w", "1M"}                            # built from daily bars
# Pine MTF set: 3, 5, 10, 15, 60, 120, 240, 360, D  (chart TF is excluded per row)
MTF_TFS = ("3m", "5m", "10m", "15m", "1h", "2h", "4h", "6h", "1d")

TV_INTERVAL = {
    "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
    "75m": "75", "1h": "60", "2h": "120", "4h": "240", "6h": "360",
    "1d": "D", "1w": "W", "1M": "M",
}


def tradingview_link(symbol: str, tf: str, is_future: bool = False) -> str:
    interval = TV_INTERVAL.get(tf, "15")
    tv_sym = f"NSE:{symbol}1!" if is_future else f"NSE:{symbol}"
    return f"https://in.tradingview.com/chart/?symbol={quote(tv_sym, safe='')}&interval={interval}"


INDEX_EXCLUSIONS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYIT", "NIFTYNXT50"}

# ===================================================================== PINE INPUTS
G_ACC = "0 · Account & trade"
G_LEG = "1 · Leg / base rules"
G_VAL = "2 · Validation & score"
G_MTF = "1 · MTF lines"          # same group names as the Pine script
G_SHD = "2 · Shadows"
G_SWG = "3 · Swings"
G_PAR = "4 · Parity inputs (Pine: declared, not filters)"
INACT = "Pine: declared, not used"


def P(default, label: str, group: str, lo=None, hi=None, step=None, tag: str = ""):
    """Declare one Pine input: default value + sidebar metadata."""
    if isinstance(default, bool):
        kind = "bool"
    elif isinstance(default, int):
        kind = "int"
    else:
        kind = "float"
    return field(default=default, metadata=dict(
        label=label, group=group, lo=lo, hi=hi, step=step, tag=tag, kind=kind))


@dataclass(frozen=True)
class PineParams:
    """Every input of the Pine v6 script, same names and defaults."""
    # ---- Account / trade
    accountCapital: float = P(25000.0, "Account Capital", G_ACC, lo=0.0, step=1000.0, tag="app: Qty only")
    riskPct: float = P(0.5, "Risk %", G_ACC, lo=0.0, hi=100.0, step=0.1, tag="app: Qty only")
    targetRR: float = P(5.0, "Target RR", G_ACC, lo=0.1, step=0.5)
    slBufferAtr: float = P(0.1, "SL Buffer ATR", G_ACC, lo=0.0, step=0.05)
    atrPeriod: int = P(14, "ATR Period", G_ACC, lo=1)
    volSmaPeriod: int = P(20, "Volume SMA Period", G_ACC, lo=1)

    # ---- Leg / base rules
    legOutTrMult: float = P(1.2, "Leg-Out TR Multiplier", G_LEG, lo=0.0, step=0.05)
    legOutMinTrRatio: float = P(1.0, "Leg-Out Min TR Ratio vs Leg-In", G_LEG, lo=0.0, step=0.05)
    hqLegOutTrMult: float = P(2.0, "HQ Leg-Out TR Multiplier", G_LEG, lo=0.0, step=0.1)
    hqLegInAtrMult: float = P(1.5, "HQ Leg-In ATR Multiplier", G_LEG, lo=0.0, step=0.1)
    maxBaseAtrMult: float = P(1.0, "Max Base TR ATR Multiplier", G_LEG, lo=0.0, step=0.05)
    maxWickPct: float = P(0.30, "Max Wick %", G_LEG, lo=0.0, hi=1.0, step=0.01)
    minBaseCountInput: int = P(1, "Min Base Count", G_LEG, lo=1, hi=10)
    maxBaseCountInput: int = P(3, "Max Base Count", G_LEG, lo=1, hi=10, tag=f"capped at {HARD_MAX_BASE_COUNT}")
    legInMinAtrMult: float = P(1.0, "Leg-In Min ATR Multiplier", G_LEG, lo=0.0, step=0.05)
    minClvPct: float = P(0.60, "Min CLV %", G_LEG, lo=0.0, hi=1.0, step=0.05)
    legInToBaseSizeMult: float = P(2.0, "Leg-In to Base Size Multiplier", G_LEG, lo=0.0, step=0.1)
    legInMinBodyPct: float = P(0.60, "Leg-In Min Body %", G_LEG, lo=0.0, hi=1.0, step=0.05)
    useImbalance: bool = P(True, "Use Imbalance", G_LEG)
    maxImbalanceMult: float = P(1.0, "Max Imbalance vs Leg-In Mult", G_LEG, lo=0.0, step=0.1, tag=INACT)
    relaxGapCapOvernight: bool = P(True, "Relax Gap Cap on Overnight", G_LEG, tag=INACT)

    # ---- Validation & score
    genuineGapBonus: int = P(10, "Genuine Gap Score Bonus", G_VAL, lo=0)
    overnightGapBonus: int = P(15, "Overnight Gap Score Bonus", G_VAL, lo=0)
    rejectOppositeCoverPct: float = P(0.50, "Reject Opposite Cover %", G_VAL, lo=0.0, hi=1.0, step=0.05)
    minValidScore: int = P(40, "Min Valid Score", G_VAL, lo=0)
    hqScoreThreshold: int = P(90, "HQ Score Threshold", G_VAL, lo=0)
    legOutBodyHeavyPct: float = P(0.60, "Leg-Out Body Heavy Pressure %", G_VAL, lo=0.0, hi=1.0, step=0.05)
    testedLegOutRetracePct: float = P(1.00, "Tested Leg-Out Retrace %", G_VAL, lo=0.0, hi=1.0, step=0.05)
    maxTestedCount: int = P(1, "Max Tested Count", G_VAL, lo=0)

    # ---- 1 · MTF lines
    showMTF: bool = P(True, "Show other-TF proximal lines", G_MTF)
    filterDistantMTF: bool = P(True, "Hide distant MTF lines (display only)", G_MTF)
    mtfNearPct: float = P(8.0, "Show lines within % of last price", G_MTF, lo=0.1, hi=100.0, step=0.5)

    # ---- 2 · Shadows
    showShadow: bool = P(True, "Shadow when prior zone behind / broken / swing broken", G_SHD)
    showFlags: bool = P(True, "Show BASE BREAK / SWING labels", G_SHD)
    shadowAtr: float = P(0.08, "Shadow padding × ATR", G_SHD, lo=0.0, hi=1.0, step=0.01, tag="visual only")
    maxStored: int = P(220, "Max chart zones (boxes ≤ 500)", G_SHD, lo=10, hi=240)

    # ---- 3 · Swings
    swingLeft: int = P(3, "Confirmed swing: bars on left", G_SWG, lo=1, hi=20)
    swingRight: int = P(3, "Confirmed swing: bars on right", G_SWG, lo=1, hi=20)

    # ---- 4 · Parity inputs (declared in Pine, never used in zone logic)
    legOutToLegInBodyMult: float = P(1.0, "Leg-Out to Leg-In Body Mult", G_PAR, lo=0.0, step=0.1, tag=INACT)
    baseBoringMaxBodyPct: float = P(0.55, "Base Small-Body (Indecision) %", G_PAR, lo=0.0, hi=1.0, step=0.05, tag=INACT)
    eodHighBufferPct: float = P(10.0, "EOD High Buffer %", G_PAR, lo=0.0, step=1.0, tag=INACT)
    eodLowBufferPct: float = P(10.0, "EOD Low Buffer %", G_PAR, lo=0.0, step=1.0, tag=INACT)
    scanAfterCandleComplete: bool = P(True, "Scan After Candle Complete", G_PAR, tag="app: drops forming bar")
    scanMonthlyOnce: bool = P(True, "Scan Monthly Once", G_PAR, tag=INACT)
    scanWeeklyOnce: bool = P(True, "Scan Weekly Once", G_PAR, tag=INACT)
    scanDailyOnce: bool = P(True, "Scan Daily Once", G_PAR, tag=INACT)
    enableClosingWickCheck: bool = P(True, "Use Closing-Side Wick Guard", G_PAR, tag=INACT)
    legInMinClosingWickPct: float = P(0.1, "Leg-In Min Closing Wick %", G_PAR, lo=0.0, hi=1.0, step=0.01, tag=INACT)
    enableLegOutCoverCheck: bool = P(True, "Use Leg-Out Coverage Guard", G_PAR, tag=INACT)
    legOutMaxCoverPct: float = P(90.0, "Leg-Out Max Cover %", G_PAR, lo=0.0, hi=100.0, step=1.0, tag=INACT)
    enableHQBaseColourCheck: bool = P(True, "Use Boring-Colour HQ Flag", G_PAR, tag=INACT)
    hqBaseColourProbabilityPct: float = P(90.0, "HQ Base Colour Probability %", G_PAR, lo=0.0, hi=100.0, step=1.0, tag=INACT)
    enableWhiteAreaCheck: bool = P(True, "Use White Area Check", G_PAR, tag=INACT)


def base_limits(p: PineParams) -> tuple[int, int]:
    """Pine: minBaseCount / maxBaseCount after clamping."""
    mn = max(1, min(p.minBaseCountInput, p.maxBaseCountInput))
    mx = max(1, min(p.maxBaseCountInput, HARD_MAX_BASE_COUNT))
    return mn, max(mn, mx)


def sidebar_params() -> PineParams:
    """Build the sidebar from the PineParams metadata; returns current values."""
    vals: dict = {}
    with st.sidebar:
        st.markdown("### Pine v6 inputs")
        st.caption("Defaults equal the Pine script. Hover a label to see its Pine variable name.")
        by_group: dict[str, list] = {}
        for f in fields(PineParams):
            by_group.setdefault(f.metadata["group"], []).append(f)
        for group, flds in by_group.items():
            with st.expander(group, expanded=False):
                for f in flds:
                    m = f.metadata
                    label = m["label"] + (f"  _({m['tag']})_" if m["tag"] else "")
                    key = f"pine_{f.name}"
                    if m["kind"] == "bool":
                        vals[f.name] = st.checkbox(label, value=bool(f.default), key=key, help=f.name)
                    elif m["kind"] == "int":
                        vals[f.name] = int(st.number_input(
                            label, value=int(f.default),
                            min_value=int(m["lo"]) if m["lo"] is not None else None,
                            max_value=int(m["hi"]) if m["hi"] is not None else None,
                            step=1, key=key, help=f.name))
                    else:
                        vals[f.name] = float(st.number_input(
                            label, value=float(f.default),
                            min_value=float(m["lo"]) if m["lo"] is not None else None,
                            max_value=float(m["hi"]) if m["hi"] is not None else None,
                            step=float(m["step"] or 0.01), key=key, help=f.name))
    return PineParams(**vals)


# ===================================================================== UNIVERSE (NSE F&O)
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
            clean = [str(s).strip().upper() for s in raw_syms
                     if str(s).strip() and str(s).strip().upper() not in INDEX_EXCLUSIONS
                     and not any(str(s).strip().upper().startswith(x) for x in ("NIFTY", "BANKNIFTY"))]
            clean = sorted(set(clean))
            if len(clean) >= 150:
                return tuple(clean)
    except Exception:
        pass

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


# ===================================================================== DATA: NORMALISE / RESAMPLE
def normalize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return EMPTY.copy()
    f = df.copy()
    if isinstance(f.columns, pd.MultiIndex):
        f.columns = [str(c[-1]) for c in f.columns]
    f.rename(columns={c: c.capitalize() for c in f.columns if c.capitalize() in OHLCV}, inplace=True)
    if "Close" not in f.columns:
        return EMPTY.copy()
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
    """Pine ta.rma: seeded with SMA of the first `length` values."""
    alpha = 1.0 / length
    out = np.full_like(src, np.nan, dtype=float)
    if len(src) < length:
        return out
    out[length - 1] = np.mean(src[:length])
    for i in range(length, len(src)):
        out[i] = alpha * src[i] + (1 - alpha) * out[i - 1]
    return out


def resample_session(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Intraday bars anchored to the NSE session open (09:15), like TradingView."""
    if df is None or df.empty:
        return EMPTY.copy()
    day = df.index.normalize()
    anchor = day + pd.Timedelta(minutes=SESSION_START_MIN)
    k = np.asarray((df.index - anchor) // pd.Timedelta(minutes=minutes)).astype(np.int64)
    out = df.groupby([day, k]).agg(Open=("Open", "first"), High=("High", "max"),
                                   Low=("Low", "min"), Close=("Close", "last"),
                                   Volume=("Volume", "sum"))
    lvl_day = pd.DatetimeIndex(out.index.get_level_values(0))
    lvl_k = np.asarray(out.index.get_level_values(1)).astype(np.int64)
    out.index = lvl_day + pd.Timedelta(minutes=SESSION_START_MIN) + pd.to_timedelta(lvl_k * minutes, unit="m")
    return out.dropna(subset=["Close"]).sort_index()


def resample_daily(df: pd.DataFrame | None, tf: str) -> pd.DataFrame:
    if df is None or df.empty:
        return EMPTY.copy()
    if tf == "1d":
        return df
    rule = "W-FRI" if tf == "1w" else "ME"
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()


def tf_frame(tf: str, data: dict) -> pd.DataFrame:
    if tf in DAY_TFS:
        return resample_daily(data.get("1d"), tf)
    base = data.get("60m" if tf in HOUR_TFS else "1m")
    return resample_session(base, TF_MINUTES[tf]) if base is not None else EMPTY.copy()


def drop_forming_bar(frame: pd.DataFrame, tf: str, now: datetime) -> pd.DataFrame:
    """Pine 'Scan After Candle Complete': ignore the bar that has not closed yet."""
    if frame.empty:
        return frame
    last = frame.index[-1]
    close_time = last.normalize() + pd.Timedelta(hours=SESSION_END[0], minutes=SESSION_END[1])
    if tf in TF_MINUTES:
        close_time = min(last + pd.Timedelta(minutes=TF_MINUTES[tf]), close_time)
    return frame.iloc[:-1] if now < close_time else frame


def get_scan_range_bounds(df_daily: pd.DataFrame | None, range_type: str, atr_period: int) -> tuple[float, float]:
    """App-only ATR price band (Daily / Weekly / Monthly). Not part of the Pine script."""
    if df_daily is None or df_daily.empty or len(df_daily) < 15:
        return 0.0, 1e9
    if range_type == "Daily":
        ref = df_daily
    elif range_type == "Weekly":
        ref = df_daily.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min",
                                              "Close": "last", "Volume": "sum"}).dropna()
    else:
        ref = df_daily.resample("ME").agg({"Open": "first", "High": "max", "Low": "min",
                                           "Close": "last", "Volume": "sum"}).dropna()
    if len(ref) < 15:
        ref = df_daily
    h = ref["High"].to_numpy(float)
    l = ref["Low"].to_numpy(float)
    c = ref["Close"].to_numpy(float)
    tr = h - l
    if len(ref) > 1:
        tr[1:] = np.maximum(tr[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr_series = pine_rma(tr, atr_period)
    atr = atr_series[-1] if not np.isnan(atr_series[-1]) else (h[-1] - l[-1])
    return max(0.0, float(l[-1] - atr)), float(h[-1] + atr)


# ===================================================================== PINE ENGINE
def _pivot_confirm(arr: np.ndarray, left: int, right: int, is_high: bool) -> list[float]:
    """Pine ta.pivothigh/pivotlow. Returns, per CONFIRMATION bar (= centre + right),
    the pivot price; NaN elsewhere. No look-ahead: confirmed `right` bars later."""
    n = len(arr)
    out = np.full(n, np.nan)
    if n < left + right + 1:
        return out.tolist()
    win = pd.Series(arr).rolling(left + right + 1, min_periods=left + right + 1)
    ext = (win.max() if is_high else win.min()).shift(-right).to_numpy(float)
    idx = np.nonzero(arr == ext)[0]          # centre is the window extreme
    idx = idx[idx + right < n]
    out[idx + right] = arr[idx]
    return out.tolist()


class Bars:
    """Pine per-bar series for one timeframe (index 0 = oldest bar)."""

    def __init__(self, df: pd.DataFrame, p: PineParams):
        n = len(df)
        self.n = n
        o = df["Open"].to_numpy(float)
        h = df["High"].to_numpy(float)
        l = df["Low"].to_numpy(float)
        c = df["Close"].to_numpy(float)
        v = df["Volume"].to_numpy(float)

        # true_range(): gap-aware from bar 1 on; bar 0 uses high-low
        tr = np.zeros(n)
        if n:
            tr[0] = h[0] - l[0]
        if n > 1:
            pc = c[:-1]
            tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
        atr = pine_rma(tr, p.atrPeriod)
        vol_sma = pd.Series(v).rolling(p.volSmaPeriod).mean().to_numpy(float)

        rng = h - l
        safe = np.where(rng == 0, np.nan, rng)           # Pine: r == 0 -> 0.0
        body = np.nan_to_num(np.abs(c - o) / safe)
        wick = np.nan_to_num(((h - np.maximum(o, c)) + (np.minimum(o, c) - l)) / safe)

        ms = ((df.index.tz_convert("UTC") - pd.Timestamp(0, tz="UTC"))
              // pd.Timedelta(milliseconds=1)).to_numpy(dtype=np.int64)
        dow = np.asarray(df.index.dayofweek)
        # isOvernightGap(): dayofweek != dayofweek[1] or time - time[1] > 86400000
        overnight = np.zeros(n, dtype=bool)
        if n > 1:
            overnight[1:] = (dow[1:] != dow[:-1]) | ((ms[1:] - ms[:-1]) > MS_PER_DAY)

        self.o, self.h, self.l, self.c, self.v = o.tolist(), h.tolist(), l.tolist(), c.tolist(), v.tolist()
        self.tr = tr.tolist()
        self.atr = atr.tolist()
        self.vol_sma = vol_sma.tolist()
        self.body = body.tolist()
        self.wick = wick.tolist()
        self.bull = (c > o).tolist()             # is_bull
        self.bear = (o > c).tolist()             # is_bear
        self.ms = ms.tolist()
        self.overnight = overnight.tolist()
        self.ph_conf = _pivot_confirm(h, p.swingLeft, p.swingRight, True)
        self.pl_conf = _pivot_confirm(l, p.swingLeft, p.swingRight, False)
        self.last_close = float(c[-1]) if n else NAN


@dataclass
class Cand:
    prox: float
    dist: float
    mid: float
    demand: bool
    score: int
    pattern: str
    category: str
    baseCount: int
    legInTR: float
    legOutTR: float
    overnight: bool


def find_candidate(B: Bars, p: PineParams, i: int, bc: int) -> Optional[Cand]:
    """Pine f_candidate(bc). Pure: same answer for chart and MTF engines."""
    li = i - (bc + 1)          # leg-in bar
    pi = li - 1                # bar before leg-in
    if pi < 0:
        return None
    atr_li = B.atr[li]
    if math.isnan(atr_li):
        return None

    inTR = B.tr[li]
    inLow, inHigh, inClose, inVol = B.l[li], B.h[li], B.c[li], B.v[li]
    inRange = inHigh - inLow
    inBull, inBear = B.bull[li], B.bear[li]
    if inRange == 0 or B.body[li] < p.legInMinBodyPct:
        return None

    opposite = (inBull and B.bear[pi]) or (inBear and B.bull[pi])
    prevHi = max(B.o[pi], B.c[pi])
    prevLo = min(B.o[pi], B.c[pi])
    overlap = max(0.0, min(prevHi, inHigh) - max(prevLo, inLow))
    if opposite and overlap / inRange >= p.rejectOppositeCoverPct:
        return None

    bullClv = (inClose - inLow) / inRange
    bearClv = (inHigh - inClose) / inRange

    # base candles: bars i-1 .. i-bc
    baseTR, baseHigh, baseLow = 0.0, -1.0, 1e9
    for b in range(1, bc + 1):
        j = i - b
        a = B.atr[j]
        if math.isnan(a):
            return None
        bt = B.tr[j]
        if bt > p.maxBaseAtrMult * a:
            return None
        baseTR = max(baseTR, bt)
        baseHigh = max(baseHigh, B.h[j])
        baseLow = min(baseLow, B.l[j])
    if baseTR == 0:
        return None

    effectiveMult = 1.5 if bc == 1 else p.legInToBaseSizeMult     # Pine literal 1.5 for bc == 1
    if inTR < effectiveMult * baseTR or not (inTR >= p.legInMinAtrMult * atr_li):
        return None

    outTR = B.tr[i]
    demand = B.bull[i]
    supply = B.bear[i]
    if not (demand or supply):
        return None
    atr_i = B.atr[i]
    if math.isnan(atr_i):
        return None

    explosive = outTR >= p.legOutTrMult * atr_i
    wickOK = B.wick[i] <= p.maxWickPct
    hierarchy = (outTR >= p.legOutMinTrRatio * inTR) and (inTR > baseTR)
    volumeOK = B.v[i] > inVol
    overnight = B.overnight[i]

    gap = False
    imbalance = True
    if p.useImbalance:
        if demand:
            gap = B.l[i] > baseHigh
            imbalance = gap or (B.c[i] > inHigh)
        else:
            gap = B.h[i] < baseLow
            imbalance = gap or (B.c[i] < inLow)

    bodyEngulf = min(B.o[i], B.c[i]) <= baseLow and max(B.o[i], B.c[i]) >= baseHigh
    if bodyEngulf and not gap:
        return None

    rbr = inBull and bullClv >= p.minClvPct and demand
    dbr = inBear and bearClv >= p.minClvPct and demand
    dbd = inBear and bearClv >= p.minClvPct and supply
    rbd = inBull and bullClv >= p.minClvPct and supply

    if not ((rbr or dbr or dbd or rbd) and explosive and wickOK and hierarchy and volumeOK and imbalance):
        return None

    # scoring (Pine literals kept as literals)
    score = 0
    if bc == 1:
        score += 15
    if inTR >= p.hqLegInAtrMult * atr_li:
        score += 10
    if outTR >= p.hqLegOutTrMult * inTR:
        score += 15
    if inTR >= 2.0 * baseTR and outTR >= 2.0 * inTR:
        score += 15
    if B.v[i] > B.vol_sma[i]:
        score += 10
    outRange = B.h[i] - B.l[i]
    if outRange > 0:
        bodyPos = (B.c[i] - B.l[i]) / outRange if demand else (B.h[i] - B.c[i]) / outRange
    else:
        bodyPos = 0.0
    if demand:
        if dbr:
            if bodyPos >= 0.80 or B.body[i] >= p.legOutBodyHeavyPct:
                score += 15
        elif bodyPos >= 0.80:
            score += 15
    elif bodyPos >= 0.80:
        score += 15
    oppositeBase = any((demand and B.bear[i - b]) or (supply and B.bull[i - b]) for b in range(1, bc + 1))
    if oppositeBase:
        score += 10
    score += 10
    if gap:
        score += p.genuineGapBonus
    if overnight and gap:
        score += p.overnightGapBonus
    if score < p.minValidScore:
        return None

    prox = baseHigh if demand else baseLow
    dist = baseLow if demand else baseHigh
    rangeOut = B.h[i] - B.l[i]
    mid = (B.h[i] - p.testedLegOutRetracePct * rangeOut) if demand else (B.l[i] + p.testedLegOutRetracePct * rangeOut)
    pattern = "RBR" if rbr else "DBR" if dbr else "DBD" if dbd else "RBD"
    category = "Continuation" if (rbr or dbd) else "Reversal"
    return Cand(prox=prox, dist=dist, mid=mid, demand=demand, score=score, pattern=pattern,
                category=category, baseCount=bc, legInTR=inTR, legOutTR=outTR, overnight=overnight)


@dataclass
class Zone:
    prox: float
    dist: float
    sl: float
    tp: float
    demand: bool
    hq: bool
    score: int
    pattern: str
    category: str
    state: str
    touch: int
    startBar: int
    createdBar: int
    createdTime: int          # ms epoch of the leg-out candle (source origin)
    baseCount: int
    mid: float
    overnight: bool
    legInTR: float
    legOutTR: float
    brokenAt: Optional[int] = None
    shadow: bool = False
    flag: str = ""


def make_zone(cand: Cand, B: Bars, i: int, sl: float, tp: float, hq: bool) -> Zone:
    return Zone(prox=cand.prox, dist=cand.dist, sl=sl, tp=tp, demand=cand.demand, hq=hq,
                score=cand.score, pattern=cand.pattern, category=cand.category, state="Fresh",
                touch=0, startBar=i - cand.baseCount, createdBar=i, createdTime=B.ms[i],
                baseCount=cand.baseCount, mid=cand.mid, overnight=cand.overnight,
                legInTR=cand.legInTR, legOutTR=cand.legOutTR)


def is_duplicate(zones: list[Zone], demand: bool, prox: float, tol: float) -> bool:
    """Pine duplicate check: same side, proximal within tol; ignores Broken; max 11 looked at."""
    checked = 0
    for old in reversed(zones):
        if old.state == "Broken":
            continue
        if old.demand == demand and abs(old.prox - prox) < tol:
            return True
        checked += 1
        if checked >= 11:
            break
    return False


def behind_flags(zones: list[Zone], B: Bars, i: int, bc: int) -> tuple[bool, bool]:
    """Pine 'behind' (older active zone existed before the first base bar) and
    'baseBreaksOld' (an old zone turned Broken during this base and a base candle closed beyond its distal)."""
    behind = False
    base_breaks_old = False
    t = i - bc
    for old in reversed(zones):
        if old.createdBar < t:
            if old.state != "Broken":
                behind = True
            if old.state == "Broken" and old.brokenAt is not None and t <= old.brokenAt < i:
                for b in range(1, bc + 1):
                    cb = B.c[i - b]
                    if (old.demand and cb < old.dist) or (not old.demand and cb > old.dist):
                        base_breaks_old = True
                        break
        if behind and base_breaks_old:
            break
    return behind, base_breaks_old


def update_states(zones: list[Zone], B: Bars, i: int, max_tested: int) -> None:
    """Pine SAME-bar state update (also applies to zones created on this bar)."""
    hi, lo = B.h[i], B.l[i]
    for z in zones:
        if z.state == "Fresh":
            if z.demand:
                if lo <= z.dist:
                    z.state = "Broken"
                elif lo <= z.mid:
                    z.state = "Tested"
                    z.touch += 1
            else:
                if hi >= z.dist:
                    z.state = "Broken"
                elif hi >= z.mid:
                    z.state = "Tested"
                    z.touch += 1
        elif z.state == "Tested":
            if z.demand:
                if lo <= z.dist:
                    z.state = "Broken"
                elif lo <= z.mid:
                    z.touch += 1
            else:
                if hi >= z.dist:
                    z.state = "Broken"
                elif hi >= z.mid:
                    z.touch += 1
        if z.state == "Tested" and z.touch > max_tested:
            z.state = "Broken"
        if z.state == "Broken" and z.brokenAt is None:
            z.brokenAt = i


def run_chart_engine(B: Bars, p: PineParams) -> list[Zone]:
    """Pine chart-timeframe engine. Returns Fresh + Tested zones after the last bar."""
    mn, mx = base_limits(p)
    min_bar = max(p.atrPeriod, mx + 3, 11)
    R = p.swingRight
    zones: list[Zone] = []

    lastSH = lastSHBar = highConfAt = highBreakAt = None
    lastSL = lastSLBar = lowConfAt = lowBreakAt = None

    for i in range(B.n):
        # 1) confirmed pivots (Pine: if not na(ph) ... )
        ph = B.ph_conf[i]
        if not math.isnan(ph):
            lastSH, lastSHBar, highConfAt, highBreakAt = ph, i - R, i, None
        pl = B.pl_conf[i]
        if not math.isnan(pl):
            lastSL, lastSLBar, lowConfAt, lowBreakAt = pl, i - R, i, None
        # 2) first CLOSE beyond a confirmed pivot
        c_i = B.c[i]
        if lastSH is not None and highBreakAt is None and c_i > lastSH:
            highBreakAt = i
        if lastSL is not None and lowBreakAt is None and c_i < lastSL:
            lowBreakAt = i

        # 3) candidate discovery + zone creation
        atr_i = B.atr[i]
        if i >= min_bar and not math.isnan(atr_i):
            found = False
            for bc in range(mn, mx + 1):
                if found:
                    break
                cand = find_candidate(B, p, i, bc)
                if cand is None:
                    continue
                found = True                       # Pine sets found BEFORE the duplicate check
                if is_duplicate(zones, cand.demand, cand.prox, atr_i * 0.25):
                    continue

                behind, base_breaks_old = behind_flags(zones, B, i, bc)
                t = i - bc
                base_break_high = (lastSH is not None and lastSHBar < t and highConfAt < t
                                   and highBreakAt is not None and t <= highBreakAt < i)
                out_break_high = (lastSH is not None and lastSHBar < t and highConfAt < t
                                  and highBreakAt == i)
                base_break_low = (lastSL is not None and lastSLBar < t and lowConfAt < t
                                  and lowBreakAt is not None and t <= lowBreakAt < i)
                out_break_low = (lastSL is not None and lastSLBar < t and lowConfAt < t
                                 and lowBreakAt == i)
                break_high = base_break_high or out_break_high
                break_low = base_break_low or out_break_low
                swing_break = break_high or break_low

                sl = cand.dist - p.slBufferAtr * atr_i if cand.demand else cand.dist + p.slBufferAtr * atr_i
                risk = abs(cand.prox - sl)
                tp = cand.prox + risk * p.targetRR if cand.demand else cand.prox - risk * p.targetRR

                z = make_zone(cand, B, i, sl, tp, hq=cand.score >= p.hqScoreThreshold)
                z.shadow = bool(p.showShadow and (behind or base_breaks_old or swing_break))
                parts = []
                if base_breaks_old:
                    parts.append("BASE BREAK")
                if swing_break:
                    parts.append("SWING HIGH" if break_high else "SWING LOW")
                z.flag = " + ".join(parts) if (p.showFlags and parts) else ""
                zones.append(z)
                if len(zones) > p.maxStored:       # Pine array.shift
                    zones.pop(0)

        # 4) SAME-bar state update
        update_states(zones, B, i, p.maxTestedCount)

        # 5) swing expiry: a broken pivot is kept for at most maxBaseCount bars
        if highBreakAt is not None and i - highBreakAt > mx:
            lastSH = lastSHBar = highBreakAt = None
        if lowBreakAt is not None and i - lowBreakAt > mx:
            lastSL = lastSLBar = lowBreakAt = None

    return [z for z in zones if z.state in ("Fresh", "Tested")]


def run_mtf_engine(B: Bars, p: PineParams) -> tuple[Optional[Zone], Optional[Zone]]:
    """Pine f_mtf_engine(): same candidate/state rules, no swings, Broken removed.
    Returns the latest active (demand, supply) zone after the last bar."""
    mn, mx = base_limits(p)
    min_bar = max(p.atrPeriod, mx + 3, 11)
    zones: list[Zone] = []
    for i in range(B.n):
        atr_i = B.atr[i]
        if i >= min_bar and not math.isnan(atr_i):
            found = False
            for bc in range(mn, mx + 1):
                if found:
                    break
                cand = find_candidate(B, p, i, bc)
                if cand is None:
                    continue
                found = True
                if is_duplicate(zones, cand.demand, cand.prox, atr_i * 0.25):
                    continue
                zones.append(make_zone(cand, B, i, NAN, NAN, hq=False))
        update_states(zones, B, i, p.maxTestedCount)
        zones = [z for z in zones if z.state != "Broken"]
    latest_d = next((z for z in reversed(zones) if z.demand), None)
    latest_s = next((z for z in reversed(zones) if not z.demand), None)
    return latest_d, latest_s


# ===================================================================== DATA DOWNLOAD
def _download_safe_batch(tickers: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
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


@st.cache_data(ttl=900, show_spinner=False)
def fetch_bundle(symbols: tuple[str, ...], need: tuple[str, ...]) -> dict[str, dict[str, pd.DataFrame]]:
    """need ⊆ {'1m', '60m', '1d'} -> {symbol: {interval_key: OHLCV frame}}."""
    specs = {"1m": ("1m", "5d"), "60m": ("60m", "60d"), "1d": ("1d", "2y")}
    chunks = [list(symbols)[i:i + 15] for i in range(0, len(symbols), 15)]
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for key in need:
        interval, period = specs[key]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(_download_safe_batch, [f"{s}.NS" for s in part], period, interval)
                    for part in chunks]
            for fut in futs:
                try:
                    res = fut.result()
                except Exception:
                    continue
                for sym, frame in res.items():
                    out.setdefault(sym, {})[key] = frame
    return out


# ===================================================================== SCAN
def fmt_ms(ms: int) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(IST).strftime("%d-%b %H:%M")


@st.cache_data(ttl=900, show_spinner=False)
def run_scan(symbols: tuple[str, ...], selected_tfs: tuple[str, ...], range_type: str,
             p: PineParams, now_key: str):
    """Returns (chart_rows, mtf_rows). now_key (minute resolution) refreshes 'complete bar' logic."""
    tfs_needed = set(selected_tfs) | (set(MTF_TFS) if p.showMTF else set())
    need = {"1d"}
    if tfs_needed & MINUTE_TFS:
        need.add("1m")
    if tfs_needed & HOUR_TFS:
        need.add("60m")
    bundle = fetch_bundle(symbols, tuple(sorted(need)))

    now = datetime.now(IST)
    risk_capital = p.accountCapital * p.riskPct / 100.0
    tf_order = [t for t in TIMEFRAMES if t in tfs_needed]
    rows: list[dict] = []
    mtf_rows: list[dict] = []

    for sym in symbols:
        data = bundle.get(sym)
        if not data:
            continue
        r_min, r_max = get_scan_range_bounds(data.get("1d"), range_type, p.atrPeriod)
        sym_ltp = None
        if data.get("1m") is not None and not data["1m"].empty:
            sym_ltp = float(data["1m"]["Close"].iloc[-1])
        elif data.get("1d") is not None and not data["1d"].empty:
            sym_ltp = float(data["1d"]["Close"].iloc[-1])

        sym_rows: list[dict] = []
        mtf_latest: dict[str, tuple[Optional[Zone], Optional[Zone]]] = {}

        for tf in tf_order:
            frame = tf_frame(tf, data)
            if p.scanAfterCandleComplete:
                frame = drop_forming_bar(frame, tf, now)
            if len(frame) < MIN_SCAN_BARS:
                continue
            B = Bars(frame, p)
            ltp = B.last_close

            if tf in selected_tfs:
                for z in run_chart_engine(B, p):
                    if not (r_min <= z.prox <= r_max or r_min <= z.dist <= r_max):
                        continue
                    per_share = abs(z.prox - z.sl)
                    qty = int(risk_capital // per_share) if per_share > 0 else 0
                    sym_rows.append({
                        "Equity_Name": sym,
                        "Equity_Link": tradingview_link(sym, tf, is_future=False),
                        "Future_Link": tradingview_link(sym, tf, is_future=True),
                        "TF": tf,
                        "Zone Side": "🟢 Demand" if z.demand else "🔴 Supply",
                        "Pattern": z.pattern,
                        "Category": z.category,
                        "Status": z.state,
                        "Proximal": round(z.prox, 2),
                        "Distal": round(z.dist, 2),
                        "SL": round(z.sl, 2),
                        "TP": round(z.tp, 2),
                        "Score": z.score,
                        "HQ": "⭐" if z.hq else "",
                        "Flags": z.flag,
                        "Shadow": "Yes" if z.shadow else "",
                        "Current LTP": round(ltp, 2),
                        "Distance Gap %": round(abs(ltp - z.prox) / ltp * 100, 2) if ltp else NAN,
                        "Qty @ Risk": qty,
                        "Created Date": fmt_ms(z.createdTime),
                        "_demand": z.demand,
                        "_prox": z.prox,
                        "_atr": B.atr[-1],
                        "_tf": tf,
                    })

            if p.showMTF and tf in MTF_TFS:
                mtf_latest[tf] = run_mtf_engine(B, p)

        # Multi-TF Zone: this zone plus other Pine-MTF TFs with an active same-side
        # line within 0.25 × ATR (Pine duplicate tolerance).
        for rec in sym_rows:
            matches = 1
            for tf2, (d_z, s_z) in mtf_latest.items():
                if tf2 == rec["_tf"]:
                    continue
                other = d_z if rec["_demand"] else s_z
                if other is not None and abs(other.prox - rec["_prox"]) < rec["_atr"] * 0.25:
                    matches += 1
            rec["Multi-TF Zone"] = (f"🔥 Confluence ({matches}x TF)" if matches >= 3
                                    else "⭐ Multi-TF (2x)" if matches == 2 else "—")
        rows.extend(sym_rows)

        # MTF lines (Pine: latest active demand + supply per TF, near last price)
        if p.showMTF:
            for tf in MTF_TFS:
                if tf not in mtf_latest:
                    continue
                for side, z in (("🟢 Demand", mtf_latest[tf][0]), ("🔴 Supply", mtf_latest[tf][1])):
                    if z is None:
                        continue
                    dist_pct = abs(z.prox - sym_ltp) / sym_ltp * 100 if sym_ltp else NAN
                    near = (not math.isnan(dist_pct)) and dist_pct <= p.mtfNearPct
                    if p.filterDistantMTF and not near:
                        continue
                    mtf_rows.append({
                        "Equity_Name": sym,
                        "Equity_Link": tradingview_link(sym, tf, is_future=False),
                        "Source TF": tf,
                        "Zone Side": side,
                        "Pattern": z.pattern,
                        "Status": z.state,
                        "Proximal": round(z.prox, 2),
                        "LTP": round(sym_ltp, 2) if sym_ltp else NAN,
                        "Distance %": round(dist_pct, 2) if not math.isnan(dist_pct) else NAN,
                        "Within Near %": "✔" if near else "",
                        "Created Date": fmt_ms(z.createdTime),
                    })

    for r in rows:
        for k in ("_demand", "_prox", "_atr", "_tf"):
            r.pop(k, None)
    return rows, mtf_rows


# ===================================================================== UI
def main() -> None:
    p = sidebar_params()
    live_fno_symbols = get_verified_nse_fno_list()

    with st.container():
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.6, 2.0, 1.1, 0.9, 0.9, 1.2, 0.8])
        with c1:
            st.selectbox("📁 Universe", [f"NSE All Future Stocks ({len(live_fno_symbols)})"], index=0)
        with c2:
            selected_tfs = st.multiselect("⏱️ Timeframe", list(TIMEFRAMES), default=["15m", "1h", "1d"])
        with c3:
            range_choice = st.selectbox("🎯 Scan Range", ["Weekly (Default)", "Daily", "Monthly"], index=0)
        with c4:
            side_filter = st.selectbox("⚖️ Side", ["All", "🟢 Demand", "🔴 Supply"], index=0)
        with c5:
            state_filter = st.selectbox("📌 Status", ["All", "Fresh", "Tested"], index=0)
        with c6:
            search_query = st.text_input("🔍 Search", placeholder="RELIANCE, SBIN...")
        with c7:
            st.write("")
            st.write("")
            rescan = st.button("🚀 Scan", type="primary", use_container_width=True)

    if not selected_tfs:
        st.warning("कृपया कम से कम एक Timeframe चुनें।")
        st.stop()

    range_type = "Weekly" if "Weekly" in range_choice else ("Daily" if "Daily" in range_choice else "Monthly")
    tfs_sorted = tuple(t for t in TIMEFRAMES if t in selected_tfs)
    if rescan:
        st.cache_data.clear()
    now_key = datetime.now(IST).strftime("%Y%m%d%H%M")

    with st.spinner(f"Running Pine v6 engine across {len(live_fno_symbols)} stocks ({range_type} scan range)..."):
        rows, mtf_rows = run_scan(tuple(live_fno_symbols), tfs_sorted, range_type, p, now_key)

    df_view = pd.DataFrame(rows)
    if df_view.empty:
        st.info("चयनित फ़िल्टर और ATR रेंज में कोई सक्रिय वैध ज़ोन नहीं मिला।")
    else:
        if side_filter != "All":
            df_view = df_view[df_view["Zone Side"] == side_filter]
        if state_filter != "All":
            df_view = df_view[df_view["Status"] == state_filter]
        if search_query.strip():
            parts = [x.strip() for x in re.split(r"[,\s]+", search_query) if x.strip()]
            pat = "|".join(re.escape(x) for x in parts)
            df_view = df_view[df_view["Equity_Name"].str.contains(pat, case=False, na=False, regex=True)]
        df_view = df_view.sort_values(by="Distance Gap %", ascending=True).reset_index(drop=True)

        display_cols = [
            "Equity_Link", "Future_Link", "TF", "Zone Side", "Pattern", "Category", "Status",
            "Multi-TF Zone", "Proximal", "Distal", "SL", "TP", "Score", "HQ", "Flags", "Shadow",
            "Current LTP", "Distance Gap %", "Qty @ Risk", "Created Date",
        ]
        st.dataframe(
            df_view[display_cols],
            column_config={
                "Equity_Link": st.column_config.LinkColumn("Equity (Cash)",
                                                           display_text=r"symbol=(?:NSE%3A)?([^&!%]+)", width="small"),
                "Future_Link": st.column_config.LinkColumn("Current-Month Future",
                                                           display_text=r"symbol=(?:NSE%3A)?([^&%]+)", width="medium"),
                "TF": st.column_config.TextColumn("TF", width="small"),
                "Zone Side": st.column_config.TextColumn("Zone Side", width="small"),
                "Pattern": st.column_config.TextColumn("Pattern", width="small"),
                "Category": st.column_config.TextColumn("Category", width="small"),
                "Status": st.column_config.TextColumn("Status", width="small"),
                "Multi-TF Zone": st.column_config.TextColumn("Multi-TF Highlight", width="medium"),
                "Proximal": st.column_config.NumberColumn("Proximal (₹)", format="%.2f"),
                "Distal": st.column_config.NumberColumn("Distal (₹)", format="%.2f"),
                "SL": st.column_config.NumberColumn("SL (₹)", format="%.2f"),
                "TP": st.column_config.NumberColumn("TP (₹)", format="%.2f"),
                "Score": st.column_config.NumberColumn("Score", format="%d"),
                "HQ": st.column_config.TextColumn("HQ", width="small"),
                "Flags": st.column_config.TextColumn("Flags", width="medium"),
                "Shadow": st.column_config.TextColumn("Shadow", width="small"),
                "Current LTP": st.column_config.NumberColumn("Current LTP", format="₹%.2f"),
                "Distance Gap %": st.column_config.ProgressColumn("Distance Gap %", min_value=0.0,
                                                                  max_value=10.0, format="%.2f%%"),
                "Qty @ Risk": st.column_config.NumberColumn("Qty @ Risk", format="%d"),
                "Created Date": st.column_config.TextColumn("Created Date", width="medium"),
            },
            use_container_width=True,
            hide_index=False,
            height=620,
        )
        st.markdown(f"""
        <div class="sheet-statusbar">
            <span>Active Valid Zones: <b>{len(df_view)}</b> | Engine: <b>Pine v6 port (zone + MTF rules)</b></span>
            <span>Complete bars only · Qty = Account × Risk% ÷ |Proximal − SL|</span>
        </div>
        """, unsafe_allow_html=True)

    if p.showMTF:
        st.markdown("#### MTF lines — latest active demand / supply per TF (Pine MTF rules)")
        df_mtf = pd.DataFrame(mtf_rows)
        if df_mtf.empty:
            st.caption("No MTF line is active near the last price for this universe.")
        else:
            if side_filter != "All":
                df_mtf = df_mtf[df_mtf["Zone Side"] == side_filter]
            if state_filter != "All":
                df_mtf = df_mtf[df_mtf["Status"] == state_filter]
            if search_query.strip():
                parts = [x.strip() for x in re.split(r"[,\s]+", search_query) if x.strip()]
                pat = "|".join(re.escape(x) for x in parts)
                df_mtf = df_mtf[df_mtf["Equity_Name"].str.contains(pat, case=False, na=False, regex=True)]
            df_mtf = df_mtf.sort_values(by="Distance %", ascending=True).reset_index(drop=True)
            st.dataframe(
                df_mtf,
                column_config={
                    "Equity_Link": st.column_config.LinkColumn("Equity", display_text=r"symbol=(?:NSE%3A)?([^&!%]+)"),
                    "Proximal": st.column_config.NumberColumn("Proximal (₹)", format="%.2f"),
                    "LTP": st.column_config.NumberColumn("LTP (₹)", format="%.2f"),
                    "Distance %": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
                },
                use_container_width=True,
                hide_index=True,
                height=360,
            )


if __name__ == "__main__":
    main()
