# Pine Zone MTF scanner - single-file Streamlit app (Pine v6 rules / inputs / logic UNCHANGED)
#
# v2 CHANGELOG (this rewrite)
#  * SPEED : biggest fix — "Scan" no longer wipes the Yahoo / NSE bhavcopy caches on every
#            click. It only invalidates the derived scan-result cache, so repeated scans
#            reuse already-downloaded candles (TTL 90s) instead of re-downloading everything.
#            A separate "Hard refresh" button nukes all caches when you really need fresh data.
#  * SPEED : per-symbol Pine scanning runs on a small thread pool (order-preserving, so the
#            output rows / tie-break order are identical to the serial version).
#  * UI    : replaced the broken custom "pill" CSS (labels were being clipped to "I…", "D…",
#            "C…") with native Streamlit segmented-control / multiselect chips — same compact
#            look, zero clipping, zero extra CSS fighting the framework.
#  * UI    : table first, KPI strip (Fresh/Tested/Broken/Demand/Supply/HQ) like a BI dashboard,
#            clean column formatting (₹, %, dates, link columns) so it reads like an
#            Excel/Google-Sheet grid. Native dataframe sorting/search toolbar is used instead
#            of a pandas Styler (fast).
#  * Pine engine / inputs / scoring / state machine: 100% unchanged from the supplied script.
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

st.set_page_config(page_title="Pine Zone MTF", page_icon="🎯",
                   layout="wide", initial_sidebar_state="collapsed")

IST = ZoneInfo("Asia/Kolkata")

# ===================================================================== universe
TIMEFRAMES: tuple[str, ...] = (
    "3m", "5m", "10m", "15m", "30m", "75m",
    "1h", "2h", "4h", "6h", "1d", "1w", "1M",
)

# Offline fallback; the latest NSE derivative bhavcopy overrides this.
FNO_FALLBACK: tuple[str, ...] = tuple(sorted(set("""
AARTIIND ABB ABBOTINDIA ABCAPITAL ABFRL ACC ADANIENSOL ADANIENT ADANIGREEN
ADANIPORTS ALKEM AMBUJACEM ANGELONE APOLLOHOSP APOLLOTYRE ASHOKLEY ASIANPAINT
ASTRAL ATUL AUBANK AUROPHARMA AXISBANK BAJAJ-AUTO BAJAJFINSV BAJFINANCE
BALKRISIND BANDHANBNK BANKBARODA BANKINDIA BATAINDIA BEL BERGEPAINT BHARATFORG
BHARTIARTL BHEL BIOCON BOSCHLTD BPCL BRITANNIA BSE CANBK CANFINHOME
CHAMBLFERT CHOLAFIN CIPLA COALINDIA COFORGE COLPAL CONCOR CROMPTON CUB
CUMMINSIND CYIENT DABUR DALBHARAT DEEPAKNTR DELHIVERY DIVISLAB DIXON DLF
DMART DRREDDY EICHERMOT ESCORTS EXIDEIND FEDERALBNK FORTIS GAIL GLENMARK
GMRAIRPORT GODREJCP GODREJPROP GRANULES GRASIM GUJGASLTD HAL HAVELLS HCLTECH
HDFCAMC HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDCOPPER HINDPETRO HINDUNILVR
HUDCO ICICIBANK ICICIGI ICICIPRULI IDEA IDFCFIRSTB IEX INDHOTEL INDIAMART
INDIGO INDUSINDBK INDUSTOWER INFY IOC IONEXCH IRB IRCTC IRFC ITC JINDALSTEL
JIOFIN JSWENERGY JSWSTEEL JUBLFOOD KALYANKJIL KANSAINER KEI KOTAKBANK
KPITTECH LALPATHLAB LAURUSLABS LT LTF LICI LTIM LTTS LUPIN M&M M&MFIN
MANAPPURAM MARICO MARUTI MCDOWELL-N MCX METROPOLIS MFSL MGL MOTHERSON
MPHASIS MUTHOOTFIN NATIONALUM NAUKRI NBCC NCC NESTLEIND NHPC NMDC NTPC
OBEROIRLTY OFSS OIL ONGC PAGEIND PERSISTENT PETRONET PFC PGEL PIIND PIDILITIND
PNB POLYCAB POWERGRID POWERINDIA PRESTIGE PVRINOX RAMCOCEM RBLBANK RECLTD
RELIANCE SAIL SBICARD SBILIFE SBIN SHREECEM SHRIRAMFIN SIEMENS SONACOMS
SRF SUNPHARMA SUPREMEIND SYNGENE TATACHEM TATACOMM TATACONSUM TATAELXSI
TATAMOTORS TATAPOWER TATASTEEL TCS TECHM TITAGARH TITAN TORNTPHARM TRENT
TVSMOTOR UBL ULTRACEMCO UNIONBANK UNITDSPR UPL VBL VEDL VOLTAS
WIPRO YESBANK ZYDUSLIFE
""".split())))

GLOBAL_INSTRUMENTS: tuple[dict[str, str], ...] = (
    {"key": "NIFTY 50", "yahoo": "^NSEI", "tv": "NSE:NIFTY", "label": "NIFTY 50"},
    {"key": "NIFTY BANK", "yahoo": "^NSEBANK", "tv": "NSE:BANKNIFTY", "label": "NIFTY BANK"},
    {"key": "USD/INR", "yahoo": "INR=X", "tv": "FX_IDC:USDINR", "label": "USD/INR"},
    {"key": "GOLD", "yahoo": "GC=F", "tv": "COMEX:GC1!", "label": "Gold Futures"},
    {"key": "SILVER", "yahoo": "SI=F", "tv": "COMEX:SI1!", "label": "Silver Futures"},
    {"key": "WTI CRUDE", "yahoo": "CL=F", "tv": "NYMEX:CL1!", "label": "WTI Crude"},
    {"key": "BRENT", "yahoo": "BZ=F", "tv": "NYMEX:BZ1!", "label": "Brent Crude"},
    {"key": "S&P 500", "yahoo": "^GSPC", "tv": "SP:SPX", "label": "S&P 500"},
    {"key": "NASDAQ 100", "yahoo": "^NDX", "tv": "NASDAQ:NDX", "label": "NASDAQ 100"},
    {"key": "DOLLAR INDEX", "yahoo": "DX-Y.NYB", "tv": "TVC:DXY", "label": "US Dollar Index"},
)

MINUTE_TFS = {"3m", "5m", "10m", "15m", "30m", "75m"}
HOUR_TFS = {"1h", "2h", "4h", "6h"}
DAY_TFS = {"1d", "1w", "1M"}

# ===================================================================== candles
OHLCV = ("Open", "High", "Low", "Close", "Volume")


def normalize_ohlcv(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return a sorted OHLCV frame with numeric columns and an IST index."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(OHLCV))
    df = frame.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[-1] if str(c[-1]).lower() in {x.lower() for x in OHLCV}
                           else c[0]) for c in df.columns]
    rename: dict[Any, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        aliases = {"open": "Open", "high": "High", "low": "Low",
                   "close": "Close", "adj close": "Close", "volume": "Volume"}
        if key in aliases:
            rename[col] = aliases[key]
    df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    if "Close" not in df.columns:
        return pd.DataFrame(columns=list(OHLCV))
    for col in ("Open", "High", "Low"):
        if col not in df.columns:
            df[col] = df["Close"]
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    df = df.loc[:, list(OHLCV)]
    for col in OHLCV:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    idx = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))
    valid = ~idx.isna()
    df = df.loc[valid].copy()
    idx = idx[valid]
    if idx.tz is None:
        idx = idx.tz_localize(IST)
    else:
        idx = idx.tz_convert(IST)
    df.index = idx
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _session_filter(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    df = normalize_ohlcv(frame)
    if df.empty:
        return df
    weekday = df.index.dayofweek < 5
    df = df.loc[weekday]
    if market.upper() == "NSE":
        minutes = df.index.hour * 60 + df.index.minute
        df = df.loc[(minutes >= 9 * 60 + 15) & (minutes < 15 * 60 + 30)]
    return df


def _resample_intraday(frame: pd.DataFrame, minutes: int, market: str,
                       now: datetime | None = None) -> pd.DataFrame:
    """Resample within each session/day; never let a bar span overnight."""
    df = _session_filter(frame, market)
    if df.empty:
        return df
    now_ist = now.astimezone(IST) if now and now.tzinfo else (now.replace(tzinfo=IST) if now else datetime.now(IST))
    rule = f"{int(minutes)}min"
    out: list[pd.DataFrame] = []
    for day, group in df.groupby(df.index.date, sort=True):
        if group.empty:
            continue
        if market.upper() == "NSE":
            offset = "9h15min"
        else:
            offset = "0min"
        bars = group.resample(
            rule, origin="start_day", offset=offset, label="left", closed="left"
        ).agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna(subset=["Open", "High", "Low", "Close"])
        if bars.empty:
            continue
        if market.upper() == "NSE":
            close_time = pd.Timestamp(datetime.combine(day, dtime(15, 30)), tz=IST)
        else:
            close_time = None
        keep = []
        for stamp in bars.index:
            effective_end = stamp + pd.Timedelta(minutes=minutes)
            if close_time is not None:
                effective_end = min(effective_end, close_time)
            if day == now_ist.date() and effective_end.to_pydatetime() > now_ist:
                continue
            keep.append(stamp)
        bars = bars.loc[keep]
        if not bars.empty:
            out.append(bars)
    if not out:
        return pd.DataFrame(columns=list(OHLCV))
    result = pd.concat(out).sort_index()
    return result[~result.index.duplicated(keep="last")]


def _aggregate_calendar(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Build weekly/monthly bars from daily bars without deprecated aliases."""
    df = normalize_ohlcv(frame)
    if df.empty:
        return df
    naive = df.index.tz_localize(None)
    if period == "1w":
        keys = naive.to_period("W-FRI")
    elif period == "1M":
        keys = naive.to_period("M")
    else:
        raise ValueError(f"Unsupported calendar period: {period}")
    out: list[dict[str, Any]] = []
    for key in keys.unique():
        block = df.loc[keys == key]
        if block.empty:
            continue
        stamp = block.index[-1]
        out.append({
            "_stamp": stamp,
            "Open": float(block["Open"].iloc[0]),
            "High": float(block["High"].max()),
            "Low": float(block["Low"].min()),
            "Close": float(block["Close"].iloc[-1]),
            "Volume": float(block["Volume"].sum()),
        })
    if not out:
        return pd.DataFrame(columns=list(OHLCV))
    result = pd.DataFrame(out).set_index("_stamp").sort_index()
    result.index.name = df.index.name
    return result.loc[:, list(OHLCV)]


def timeframe_candles(data: dict[str, pd.DataFrame], timeframe: str,
                      market: str = "NSE", now: datetime | None = None) -> pd.DataFrame:
    """Create requested timeframe from the three compact Yahoo data layers."""
    market = market.upper()
    if timeframe in MINUTE_TFS:
        source = data.get("minute")
        if source is None or source.empty:
            return pd.DataFrame(columns=list(OHLCV))
        return _resample_intraday(source, int(timeframe[:-1]), market, now=now)
    if timeframe in HOUR_TFS:
        source = data.get("hour")
        if source is None or source.empty:
            return pd.DataFrame(columns=list(OHLCV))
        if timeframe == "1h":
            return _resample_intraday(source, 60, market, now=now)
        hours = int(timeframe[:-1])
        return _resample_intraday(source, hours * 60, market, now=now)
    daily = data.get("daily")
    if daily is None or daily.empty:
        return pd.DataFrame(columns=list(OHLCV))
    if timeframe == "1d":
        return normalize_ohlcv(daily)
    if timeframe in {"1w", "1M"}:
        return _aggregate_calendar(daily, timeframe)
    raise ValueError(f"Unsupported timeframe: {timeframe}")


# ===================================================================== Pine engine
# Python port of the Pine Script v6 Zone MTF engine (rules / inputs unchanged).
PINE_DEFAULTS: Dict[str, Any] = {
    "accountCapital": 25000.0,
    "riskPct": 0.5,
    "targetRR": 5.0,
    "slBufferAtr": 0.1,
    "atrPeriod": 14,
    "volSmaPeriod": 20,
    "legOutTrMult": 1.2,
    "legOutMinTrRatio": 1.0,
    "hqLegOutTrMult": 2.0,
    "hqLegInAtrMult": 1.5,
    "maxBaseAtrMult": 1.0,
    "maxWickPct": 0.30,
    "minBaseCountInput": 1,
    "maxBaseCountInput": 3,
    "legInMinAtrMult": 1.0,
    "minClvPct": 0.60,
    "legInToBaseSizeMult": 2.0,
    "legInMinBodyPct": 0.60,
    "useImbalance": True,
    "maxImbalanceMult": 1.0,
    "relaxGapCapOvernight": True,
    "genuineGapBonus": 10,
    "overnightGapBonus": 15,
    "rejectOppositeCoverPct": 0.50,
    "minValidScore": 40,
    "hqScoreThreshold": 90,
    "legOutBodyHeavyPct": 0.60,
    "testedLegOutRetracePct": 1.00,
    "maxTestedCount": 1,

    # Kept as Pine inputs; marked inactive in the supplied script.
    "legOutToLegInBodyMult": 1.0,
    "baseBoringMaxBodyPct": 0.55,
    "eodHighBufferPct": 10.0,
    "eodLowBufferPct": 10.0,
    "scanAfterCandleComplete": True,
    "scanMonthlyOnce": True,
    "scanWeeklyOnce": True,
    "scanDailyOnce": True,
    "enableClosingWickCheck": True,
    "legInMinClosingWickPct": 0.1,
    "enableLegOutCoverCheck": True,
    "legOutMaxCoverPct": 90.0,
    "enableHQBaseColourCheck": True,
    "hqBaseColourProbabilityPct": 90.0,
    "enableWhiteAreaCheck": True,

    # Pine groups 1-3: MTF visibility, shadow and swing settings.
    "showMTF": True,
    "filterDistantMTF": True,
    "mtfNearPct": 8.0,
    "showShadow": True,
    "showFlags": True,
    "shadowAtr": 0.08,
    "swingLeft": 3,
    "swingRight": 3,
    "maxStored": 220,
}
HARD_MAX_BASE_COUNT = 3


@dataclass
class Box:
    left: int
    top: float
    right: int
    bottom: float
    border_color: object
    bgcolor: object

    def set_right(self, right: int) -> None:
        self.right = right

    def set_bgcolor(self, color: object) -> None:
        self.bgcolor = color

    def set_border_color(self, color: object) -> None:
        self.border_color = color


@dataclass
class Zone:
    proxVal: float
    distVal: float
    slVal: float
    tpVal: float
    isDemand: bool
    isHQ: bool
    densityScore: int
    patternType: str
    zoneCategory: str
    state: str
    touchCount: int
    startBarIndex: int
    createdBarIndex: int
    baseCount: int
    legOutHigh: float
    legOutLow: float
    legOutMidLevel: float
    isOvernight: bool
    legInTR: float
    legOutTR: float
    zoneBox: Box
    shadowBox: Optional[Box] = None
    infoLabel: str = ""
    brokenAt: Optional[int] = None
    timestamp: object = None
    riskPct: float = float("nan")
    score10: float = 0.0
    hasGenuineGap: bool = False
    entryBarIndex: Optional[int] = None
    entryTimestamp: object = None
    shadowBehind: bool = False
    baseBreaksOld: bool = False
    swingBreak: bool = False
    brokeHigh: bool = False
    brokeLow: bool = False
    shadowTop: float = float("nan")
    shadowBottom: float = float("nan")
    shadowColor: str = ""
    flagText: str = ""
    breakReason: str = ""

    @property
    def breakBarIndex(self) -> Optional[int]:
        return self.brokenAt


OHLCV_LOWER = ("open", "high", "low", "close", "volume")


def _normalize_core_frame(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[-1]) for col in df.columns]
    rename = {col: str(col).strip().lower() for col in df.columns
              if str(col).strip().lower() in set(OHLCV_LOWER)}
    df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"PineZoneEngine requires {col} prices")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["open", "high", "low", "close"])
    idx = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))
    valid = ~idx.isna()
    df = df.loc[valid].copy()
    df.index = idx[valid]
    return df.sort_index().loc[:, list(OHLCV_LOWER)]


def true_range_series(df: pd.DataFrame) -> np.ndarray:
    """Same gap-aware TR used by Pine true_range()."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    tr = high - low
    if len(tr) > 1:
        tr[1:] = np.maximum(tr[1:], np.maximum(np.abs(high[1:] - close[:-1]),
                                               np.abs(low[1:] - close[:-1])))
    return tr


def check_white_area(df: pd.DataFrame, base_start_idx: int, base_end_idx: int,
                     leg_out_idx: int, curr_idx: int) -> bool:
    """Legacy helper kept for API compatibility; inactive in Pine validation."""
    frame = _normalize_core_frame(df)
    bh = float(frame["high"].iloc[base_start_idx:base_end_idx + 1].max())
    bl = float(frame["low"].iloc[base_start_idx:base_end_idx + 1].min())
    if leg_out_idx + 1 >= curr_idx:
        return True
    window = frame.iloc[leg_out_idx + 1:curr_idx]
    return not bool(((window["low"] <= bh) & (window["high"] >= bl)).any())


def check_leg_out_coverage(df: pd.DataFrame, leg_out_idx: int, curr_idx: int,
                           max_cover_pct: float = 90.0) -> bool:
    """Legacy helper kept for API compatibility; inactive in Pine validation."""
    frame = _normalize_core_frame(df)
    hi, lo = float(frame["high"].iloc[leg_out_idx]), float(frame["low"].iloc[leg_out_idx])
    span = hi - lo
    if span <= 0 or leg_out_idx + 1 > curr_idx:
        return True
    window = frame.iloc[leg_out_idx + 1:curr_idx + 1]
    overlap = (np.minimum(window["high"], hi) - np.maximum(window["low"], lo)).clip(lower=0)
    return not bool(((overlap / span) > float(max_cover_pct) / 100.0).any())


class ZoneEngine:
    """Per-timeframe Pine chart engine or isolated MTF engine."""

    def __init__(self, df: pd.DataFrame, *, chart_mode: bool = True, **kwargs: Any):
        self.df = _normalize_core_frame(df)
        self.chart_mode = bool(chart_mode)
        for key, default in PINE_DEFAULTS.items():
            setattr(self, key, kwargs.get(key, default))
        self.atrPeriod = max(1, int(self.atrPeriod))
        self.volSmaPeriod = max(1, int(self.volSmaPeriod))
        self.minBaseCountInput = int(self.minBaseCountInput)
        self.maxBaseCountInput = int(self.maxBaseCountInput)
        self.minBaseCount = max(1, min(self.minBaseCountInput, self.maxBaseCountInput))
        self.maxBaseCount = min(self.maxBaseCountInput, HARD_MAX_BASE_COUNT)
        self.swingLeft = max(1, min(20, int(self.swingLeft)))
        self.swingRight = max(1, min(20, int(self.swingRight)))
        self.maxStored = max(10, min(240, int(self.maxStored)))
        self.o = self.df["open"].to_numpy(dtype=float, copy=True)
        self.h = self.df["high"].to_numpy(dtype=float, copy=True)
        self.l = self.df["low"].to_numpy(dtype=float, copy=True)
        self.c = self.df["close"].to_numpy(dtype=float, copy=True)
        self.v = self.df["volume"].to_numpy(dtype=float, copy=True)
        self.n = len(self.df)
        self.dow = self.df.index.dayofweek.to_numpy() if self.n else np.array([], dtype=int)
        time_index = self.df.index
        if time_index.tz is not None:
            time_index = time_index.tz_convert("UTC").tz_localize(None)
        self.time_ms = time_index.astype("datetime64[ns]").astype(np.int64) // 10**6
        self.active_zones: List[Zone] = []
        self.lastSwingHigh: float | None = None
        self.lastSwingLow: float | None = None
        self.lastSwingHighBar: int | None = None
        self.lastSwingLowBar: int | None = None
        self.highConfirmedAt: int | None = None
        self.lowConfirmedAt: int | None = None
        self.highBreakAt: int | None = None
        self.lowBreakAt: int | None = None
        self._prepare()

    def _rma(self, values: np.ndarray, length: int) -> np.ndarray:
        result = np.full(len(values), np.nan)
        if len(values) < length:
            return result
        result[length - 1] = np.mean(values[:length])
        for i in range(length, len(values)):
            result[i] = (values[i] - result[i - 1]) / length + result[i - 1]
        return result

    def _prepare(self) -> None:
        self.tr_values = true_range_series(self.df)
        self.atr = self._rma(self.tr_values, self.atrPeriod)
        self.vol_sma = self.df["volume"].rolling(self.volSmaPeriod).mean().to_numpy()

    def _tr(self, index: int) -> float:
        previous_close = self.c[index - 1]
        return max(self.h[index] - self.l[index],
                   abs(self.h[index] - previous_close),
                   abs(self.l[index] - previous_close))

    def _bull(self, index: int) -> bool:
        return bool(self.c[index] > self.o[index])

    def _bear(self, index: int) -> bool:
        return bool(self.o[index] > self.c[index])

    def _wick_pct(self, index: int) -> float:
        span = self.h[index] - self.l[index]
        if span == 0:
            return 0.0
        wick = ((self.h[index] - max(self.o[index], self.c[index]))
                + (min(self.o[index], self.c[index]) - self.l[index]))
        return wick / span

    def _body_pct(self, index: int) -> float:
        span = self.h[index] - self.l[index]
        return abs(self.c[index] - self.o[index]) / span if span else 0.0

    def _overnight(self, index: int) -> bool:
        if index == 0:
            return False
        return bool(self.dow[index] != self.dow[index - 1]
                    or self.time_ms[index] - self.time_ms[index - 1] > 86400000)

    def _candidate(self, i: int, bc: int) -> dict[str, Any] | None:
        li, pi = bc + 1, bc + 2
        p_in, p_prev = i - li, i - pi
        if p_prev < 0 or np.isnan(self.atr[p_in]):
            return None
        in_tr = self._tr(p_in)
        in_low, in_high, in_close, in_vol = self.l[p_in], self.h[p_in], self.c[p_in], self.v[p_in]
        in_range = in_high - in_low
        in_bull, in_bear = self._bull(p_in), self._bear(p_in)
        if in_range == 0 or self._body_pct(p_in) < self.legInMinBodyPct:
            return None

        opposite = (in_bull and self._bear(p_prev)) or (in_bear and self._bull(p_prev))
        prev_hi, prev_lo = max(self.o[p_prev], self.c[p_prev]), min(self.o[p_prev], self.c[p_prev])
        overlap = max(0.0, min(prev_hi, in_high) - max(prev_lo, in_low))
        if opposite and overlap / in_range >= self.rejectOppositeCoverPct:
            return None
        bull_clv = (in_close - in_low) / in_range
        bear_clv = (in_high - in_close) / in_range

        base_ok = True
        base_tr, base_high, base_low = 0.0, -1.0, 1_000_000_000.0
        for b in range(1, bc + 1):
            p = i - b
            if np.isnan(self.atr[p]):
                base_ok = False
                break
            bt = self._tr(p)
            if bt > self.maxBaseAtrMult * self.atr[p]:
                base_ok = False
                break
            base_tr = max(base_tr, bt)
            base_high = max(base_high, self.h[p])
            base_low = min(base_low, self.l[p])
        if not base_ok or base_tr == 0:
            return None

        effective_mult = 1.5 if bc == 1 else self.legInToBaseSizeMult
        if in_tr < effective_mult * base_tr or not in_tr >= self.legInMinAtrMult * self.atr[p_in]:
            return None
        out_tr = self._tr(i)
        demand, supply = self._bull(i), self._bear(i)
        if not (demand or supply):
            return None
        explosive = out_tr >= self.legOutTrMult * self.atr[i]
        wick_ok = self._wick_pct(i) <= self.maxWickPct
        hierarchy = out_tr >= self.legOutMinTrRatio * in_tr and in_tr > base_tr
        volume_ok = self.v[i] > in_vol
        overnight = self._overnight(i)
        gap = False
        imbalance = True
        if self.useImbalance:
            if demand:
                gap = self.l[i] > base_high
                imbalance = gap or self.c[i] > in_high
            else:
                gap = self.h[i] < base_low
                imbalance = gap or self.c[i] < in_low
        body_engulf = min(self.o[i], self.c[i]) <= base_low and max(self.o[i], self.c[i]) >= base_high
        if body_engulf and not gap:
            return None

        rbr = in_bull and bull_clv >= self.minClvPct and demand
        dbr = in_bear and bear_clv >= self.minClvPct and demand
        dbd = in_bear and bear_clv >= self.minClvPct and supply
        rbd = in_bull and bull_clv >= self.minClvPct and supply
        if not ((rbr or dbr or dbd or rbd) and explosive and wick_ok
                and hierarchy and volume_ok and imbalance):
            return None

        score = 0
        if bc == 1:
            score += 15
        if in_tr >= self.hqLegInAtrMult * self.atr[p_in]:
            score += 10
        if out_tr >= self.hqLegOutTrMult * in_tr:
            score += 15
        if in_tr >= 2.0 * base_tr and out_tr >= 2.0 * in_tr:
            score += 15
        if self.v[i] > self.vol_sma[i]:
            score += 10
        out_range = self.h[i] - self.l[i]
        body_pos = ((self.c[i] - self.l[i]) / out_range if demand and out_range > 0
                    else (self.h[i] - self.c[i]) / out_range if out_range > 0 else 0.0)
        if demand:
            if dbr:
                if body_pos >= 0.80 or self._body_pct(i) >= self.legOutBodyHeavyPct:
                    score += 15
            elif body_pos >= 0.80:
                score += 15
        elif body_pos >= 0.80:
            score += 15
        opposite_base = any((demand and self._bear(i - b)) or
                            (supply and self._bull(i - b)) for b in range(1, bc + 1))
        if opposite_base:
            score += 10
        score += 10
        if gap:
            score += self.genuineGapBonus
        if overnight and gap:
            score += self.overnightGapBonus
        if score < self.minValidScore:
            return None

        prox = base_high if demand else base_low
        dist = base_low if demand else base_high
        mid = (self.h[i] - self.testedLegOutRetracePct * (self.h[i] - self.l[i])
               if demand else self.l[i] + self.testedLegOutRetracePct * (self.h[i] - self.l[i]))
        return {
            "prox": prox, "dist": dist, "mid": mid, "demand": demand,
            "score": int(score), "pattern": "RBR" if rbr else "DBR" if dbr else "DBD" if dbd else "RBD",
            "category": "Continuation" if (rbr or dbd) else "Reversal",
            "legInTR": in_tr, "legOutTR": out_tr, "outHigh": self.h[i],
            "outLow": self.l[i], "overnight": overnight,
            "baseHigh": base_high, "baseLow": base_low, "baseCount": bc,
            "gap": gap,
        }

    def _update_swings(self, i: int) -> None:
        pivot = i - self.swingRight
        if pivot >= self.swingLeft:
            left_h = self.h[pivot - self.swingLeft:pivot]
            right_h = self.h[pivot + 1:i + 1]
            left_l = self.l[pivot - self.swingLeft:pivot]
            right_l = self.l[pivot + 1:i + 1]
            if (len(left_h) == self.swingLeft and len(right_h) == self.swingRight
                    and self.h[pivot] >= np.max(left_h) and self.h[pivot] > np.max(right_h)):
                self.lastSwingHigh = float(self.h[pivot])
                self.lastSwingHighBar = pivot
                self.highConfirmedAt = i
                self.highBreakAt = None
            if (len(left_l) == self.swingLeft and len(right_l) == self.swingRight
                    and self.l[pivot] <= np.min(left_l) and self.l[pivot] < np.min(right_l)):
                self.lastSwingLow = float(self.l[pivot])
                self.lastSwingLowBar = pivot
                self.lowConfirmedAt = i
                self.lowBreakAt = None
        if (self.lastSwingHigh is not None and self.highBreakAt is None
                and self.highConfirmedAt is not None and i >= self.highConfirmedAt
                and self.c[i] > self.lastSwingHigh):
            self.highBreakAt = i
        if (self.lastSwingLow is not None and self.lowBreakAt is None
                and self.lowConfirmedAt is not None and i >= self.lowConfirmedAt
                and self.c[i] < self.lastSwingLow):
            self.lowBreakAt = i

    def _expire_swings(self, i: int) -> None:
        if self.highBreakAt is not None and i - self.highBreakAt > self.maxBaseCount:
            self.lastSwingHigh = None
            self.lastSwingHighBar = None
            self.highBreakAt = None
        if self.lowBreakAt is not None and i - self.lowBreakAt > self.maxBaseCount:
            self.lastSwingLow = None
            self.lastSwingLowBar = None
            self.lowBreakAt = None

    def _shadow_context(self, i: int, bc: int) -> tuple[bool, bool, bool, bool, bool]:
        first_base = i - bc
        behind = False
        base_breaks_old = False
        for old in reversed(self.active_zones):
            if old.createdBarIndex < first_base:
                if old.state != "Broken":
                    behind = True
                if (old.state == "Broken" and old.brokenAt is not None
                        and first_base <= old.brokenAt < i):
                    for b in range(1, bc + 1):
                        if ((old.isDemand and self.c[i - b] < old.distVal)
                                or (not old.isDemand and self.c[i - b] > old.distVal)):
                            base_breaks_old = True
                            break
            if behind and base_breaks_old:
                break
        base_broke_high = (
            self.lastSwingHigh is not None and self.lastSwingHighBar is not None
            and self.highConfirmedAt is not None and self.highBreakAt is not None
            and self.lastSwingHighBar < first_base and self.highConfirmedAt < first_base
            and first_base <= self.highBreakAt < i
        )
        out_broke_high = (
            self.lastSwingHigh is not None and self.lastSwingHighBar is not None
            and self.highConfirmedAt is not None and self.highBreakAt == i
            and self.lastSwingHighBar < first_base and self.highConfirmedAt < first_base
        )
        base_broke_low = (
            self.lastSwingLow is not None and self.lastSwingLowBar is not None
            and self.lowConfirmedAt is not None and self.lowBreakAt is not None
            and self.lastSwingLowBar < first_base and self.lowConfirmedAt < first_base
            and first_base <= self.lowBreakAt < i
        )
        out_broke_low = (
            self.lastSwingLow is not None and self.lastSwingLowBar is not None
            and self.lowConfirmedAt is not None and self.lowBreakAt == i
            and self.lastSwingLowBar < first_base and self.lowConfirmedAt < first_base
        )
        broke_high = bool(base_broke_high or out_broke_high)
        broke_low = bool(base_broke_low or out_broke_low)
        return behind, base_breaks_old, broke_high or broke_low, broke_high, broke_low

    def _is_duplicate(self, cand: dict[str, Any], i: int) -> bool:
        checked = 0
        for old in reversed(self.active_zones):
            if old.state == "Broken":
                continue
            if old.isDemand == cand["demand"] and abs(old.proxVal - cand["prox"]) < self.atr[i] * 0.25:
                return True
            checked += 1
            if checked >= 11:
                break
        return False

    def _add_zone(self, i: int, cand: dict[str, Any]) -> None:
        bc = cand["baseCount"]
        behind, base_breaks_old, swing_break, broke_high, broke_low = self._shadow_context(i, bc)
        sl = (cand["dist"] - self.slBufferAtr * self.atr[i] if cand["demand"]
              else cand["dist"] + self.slBufferAtr * self.atr[i])
        risk = abs(cand["prox"] - sl)
        tp = (cand["prox"] + risk * self.targetRR if cand["demand"]
              else cand["prox"] - risk * self.targetRR)
        should_shadow = bool(self.showShadow and (behind or base_breaks_old or swing_break))
        if base_breaks_old:
            shadow_color = "orange"
        elif swing_break:
            shadow_color = "yellow"
        else:
            shadow_color = "gray" if behind else ""
        flag = ""
        if self.showFlags and (base_breaks_old or swing_break):
            flag = ("BASE BREAK" if base_breaks_old else "")
            if base_breaks_old and swing_break:
                flag += " + "
            if swing_break:
                flag += "SWING HIGH" if broke_high else "SWING LOW"
        top, bottom = max(cand["prox"], cand["dist"]), min(cand["prox"], cand["dist"])
        shadow_box = None
        shadow_top, shadow_bottom = float("nan"), float("nan")
        if should_shadow:
            shadow_top = top + self.shadowAtr * self.atr[i]
            shadow_bottom = bottom - self.shadowAtr * self.atr[i]
            shadow_box = Box(i - bc - 1, shadow_top, i + 15, shadow_bottom,
                             (shadow_color, 12 if base_breaks_old else 55),
                             (shadow_color, 72 if base_breaks_old else 82 if swing_break else 91))
        zone_color = "green" if cand["demand"] else "red"
        box = Box(i - bc - 1, cand["prox"], i + 15, cand["dist"],
                  zone_color, (zone_color, 85))
        zone = Zone(
            proxVal=float(cand["prox"]), distVal=float(cand["dist"]),
            slVal=float(sl), tpVal=float(tp), isDemand=bool(cand["demand"]),
            isHQ=bool(cand["score"] >= self.hqScoreThreshold),
            densityScore=int(cand["score"]), patternType=str(cand["pattern"]),
            zoneCategory=str(cand["category"]), state="Fresh", touchCount=0,
            startBarIndex=i - bc, createdBarIndex=i, baseCount=bc,
            legOutHigh=float(cand["outHigh"]), legOutLow=float(cand["outLow"]),
            legOutMidLevel=float(cand["mid"]), isOvernight=bool(cand["overnight"]),
            legInTR=float(cand["legInTR"]), legOutTR=float(cand["legOutTR"]),
            zoneBox=box, shadowBox=shadow_box, infoLabel=flag,
            timestamp=self.df.index[i], riskPct=risk / cand["prox"] * 100 if cand["prox"] else float("nan"),
            score10=round(cand["score"] / 10.0, 1), hasGenuineGap=bool(cand["gap"]),
            shadowBehind=behind, baseBreaksOld=base_breaks_old,
            swingBreak=swing_break, brokeHigh=broke_high, brokeLow=broke_low,
            shadowTop=shadow_top, shadowBottom=shadow_bottom,
            shadowColor=shadow_color, flagText=flag,
        )
        self.active_zones.append(zone)
        if self.chart_mode and len(self.active_zones) > self.maxStored:
            self.active_zones.pop(0)

    def _scan_bar(self, i: int) -> None:
        # Pine sets `found` before duplicate-check; after any valid candidate,
        # later base-count alternatives on this bar are not examined.
        for bc in range(self.minBaseCount, self.maxBaseCount + 1):
            cand = self._candidate(i, bc)
            if cand is None:
                continue
            if not self._is_duplicate(cand, i):
                self._add_zone(i, cand)
            break

    def _update_states(self, i: int) -> None:
        low, high = self.l[i], self.h[i]
        for k in range(len(self.active_zones) - 1, -1, -1):
            zone = self.active_zones[k]
            # SPEED (identical output): in chart mode an already-broken zone only
            # re-applied the same grey colours every bar; nothing else changes.
            if self.chart_mode and zone.state == "Broken" and zone.brokenAt is not None:
                continue
            if zone.state == "Fresh":
                if zone.isDemand:
                    if low <= zone.distVal:
                        zone.state = "Broken"
                    elif low <= zone.legOutMidLevel:
                        zone.state = "Tested"
                        zone.touchCount += 1
                else:
                    if high >= zone.distVal:
                        zone.state = "Broken"
                    elif high >= zone.legOutMidLevel:
                        zone.state = "Tested"
                        zone.touchCount += 1
            elif zone.state == "Tested":
                if zone.isDemand:
                    if low <= zone.distVal:
                        zone.state = "Broken"
                    elif low <= zone.legOutMidLevel:
                        zone.touchCount += 1
                else:
                    if high >= zone.distVal:
                        zone.state = "Broken"
                    elif high >= zone.legOutMidLevel:
                        zone.touchCount += 1
            if zone.state == "Tested" and zone.touchCount > self.maxTestedCount:
                zone.state = "Broken"
            if zone.state == "Broken":
                if zone.brokenAt is None:
                    zone.brokenAt = i
                zone.zoneBox.set_bgcolor(("gray", 95))
                zone.zoneBox.set_border_color(("gray", 80))
                if zone.shadowBox is not None:
                    zone.shadowBox.set_bgcolor(("gray", 95))
                    zone.shadowBox.set_border_color(("gray", 85))
                if not self.chart_mode:
                    self.active_zones.pop(k)
            else:
                zone.zoneBox.set_right(i + 15)
                if zone.shadowBox is not None:
                    zone.shadowBox.set_right(i + 15)

    def run(self) -> List[Zone]:
        min_bar = max(self.atrPeriod, self.maxBaseCount + 3, 11)
        for i in range(self.n):
            self._update_swings(i)
            if i >= min_bar and not np.isnan(self.atr[i]):
                self._scan_bar(i)
            self._update_states(i)
            self._expire_swings(i)
        return self.active_zones


def settings(**overrides: Any) -> Dict[str, Any]:
    result = dict(PINE_DEFAULTS)
    for key, value in overrides.items():
        if key in PINE_DEFAULTS:
            result[key] = value
    return result


def scan_zones(df: pd.DataFrame, params: Optional[Dict[str, Any]] = None,
               *, tf: Optional[str] = None, chart_mode: bool = True) -> List[Zone]:
    config = settings(**dict(params or {}))
    return ZoneEngine(df, chart_mode=chart_mode, **config).run()


def latest_active_zones(zones: List[Zone]) -> List[Zone]:
    return [zone for zone in zones if zone.state in ("Fresh", "Tested")]


def high_quality_zones(zones: List[Zone]) -> List[Zone]:
    return [zone for zone in zones if zone.isHQ]


def latest_mtf_by_side(zones: List[Zone]) -> dict[str, Zone]:
    """Return latest active MTF demand and supply as Pine f_mtf_engine does."""
    active = latest_active_zones(zones)
    result: dict[str, Zone] = {}
    for zone in reversed(active):
        side = "Demand" if zone.isDemand else "Supply"
        if side not in result:
            result[side] = zone
        if len(result) == 2:
            break
    return result


pine_settings = settings
run_pine_engine = scan_zones


# ===================================================================== market data
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ZoneScanner/1.0)"}
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/csv,application/zip,application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def yahoo_ticker(symbol: str) -> str:
    """Map an NSE cash symbol to Yahoo's ``.NS`` notation."""
    value = str(symbol).strip().upper()
    return value if value.endswith((".NS", ".BO")) or value.startswith("^") else f"{value}.NS"


def _clean_yahoo_block(block: pd.DataFrame | None) -> pd.DataFrame:
    if block is None or block.empty:
        return pd.DataFrame()
    frame = block.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(c[-1]) for c in frame.columns]
    wanted = {str(c).strip().lower(): c for c in frame.columns}
    cols: dict[str, Any] = {}
    for key, name in (("open", "Open"), ("high", "High"), ("low", "Low"),
                      ("close", "Close"), ("volume", "Volume")):
        if key in wanted:
            cols[wanted[key]] = name
        elif key == "close" and "adj close" in wanted:
            cols[wanted["adj close"]] = name
    frame = frame.rename(columns=cols)
    if "Close" not in frame.columns:
        return pd.DataFrame()
    frame = frame.loc[:, ~frame.columns.duplicated(keep="first")]
    return normalize_ohlcv(frame)


def _extract_ticker(raw: pd.DataFrame, ticker: str,
                    requested: tuple[str, ...]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    block: pd.DataFrame | None = None
    if isinstance(raw.columns, pd.MultiIndex):
        candidates = {ticker, ticker.upper(), ticker.lower()}
        for level in range(raw.columns.nlevels):
            values = raw.columns.get_level_values(level)
            match = next((v for v in values.unique() if str(v) in candidates), None)
            if match is not None:
                try:
                    block = raw.xs(match, axis=1, level=level, drop_level=True)
                    break
                except (KeyError, ValueError):
                    pass
        if block is None and len(requested) == 1:
            block = raw.copy()
    else:
        block = raw.copy() if len(requested) == 1 else None
    return _clean_yahoo_block(block)


def _download_chunk(tickers: tuple[str, ...], period: str,
                    interval: str) -> dict[str, pd.DataFrame]:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is not installed; install requirements.txt") from exc
    raw = yf.download(
        tickers=list(tickers), period=period, interval=interval,
        group_by="ticker", auto_adjust=False, actions=False,
        progress=False, threads=False, prepost=False, timeout=18,
    )
    result = {}
    for ticker in tickers:
        frame = _extract_ticker(raw, ticker, tickers)
        if not frame.empty:
            result[ticker] = frame
    return result


def download_yahoo_candles(tickers: tuple[str, ...] | list[str], period: str,
                           interval: str, *, chunk_size: int = 25,
                           workers: int = 8) -> dict[str, pd.DataFrame]:
    """Download many symbols in small parallel batches, failing soft per batch."""
    clean = tuple(dict.fromkeys(str(t).strip() for t in tickers if str(t).strip()))
    if not clean:
        return {}
    chunks = [clean[i:i + chunk_size] for i in range(0, len(clean), chunk_size)]
    result: dict[str, pd.DataFrame] = {}
    if len(chunks) == 1:
        return _download_chunk(chunks[0], period, interval)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunks)))) as pool:
        futures = [pool.submit(_download_chunk, part, period, interval) for part in chunks]
        for fut in futures:
            try:
                result.update(fut.result())
            except Exception:
                continue
    return result


def _nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get("https://www.nseindia.com/", timeout=3)
    except requests.RequestException:
        pass
    return session


def _bhavcopy_urls(day: date) -> tuple[str, ...]:
    ddmmyy = day.strftime("%d%m%y")
    ddmonyyyy = day.strftime("%d%b%Y").upper()
    year = day.strftime("%Y")
    month = day.strftime("%b").upper()
    old_name = f"fo{ddmonyyyy}bhav.csv.zip"
    new_name = f"fo{ddmmyy}bhav.csv.zip"
    udiff = f"BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"   # current NSE format
    return (
        f"https://nsearchives.nseindia.com/content/fo/{udiff}",
        f"https://nsearchives.nseindia.com/content/fo/{new_name}",
        f"https://archives.nseindia.com/content/fo/{new_name}",
        f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{year}/{month}/{old_name}",
        f"https://archives.nseindia.com/content/historical/DERIVATIVES/{year}/{month}/{old_name}",
    )


def _read_bhavcopy(payload: bytes) -> pd.DataFrame | None:
    if not payload or payload[:20].lstrip().lower().startswith((b"<!doctype", b"<html")):
        return None
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                return None
            with archive.open(names[0]) as handle:
                return pd.read_csv(handle, low_memory=False)
    except zipfile.BadZipFile:
        try:
            return pd.read_csv(BytesIO(payload), low_memory=False)
        except Exception:
            return None
    except Exception:
        return None


def _col_by_alias(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    normalized = {re.sub(r"[^A-Z0-9]", "", str(col).upper()): str(col)
                  for col in frame.columns}
    for alias in aliases:
        key = re.sub(r"[^A-Z0-9]", "", alias.upper())
        if key in normalized:
            return normalized[key]
    return None


def _parse_fo_contracts(frame: pd.DataFrame, asof: date,
                        today: date | None = None) -> dict[str, dict[str, Any]]:
    if frame is None or frame.empty:
        return {}
    symbol_col = _col_by_alias(frame, ("SYMBOL", "TckrSymb", "Undrlyg", "Underlying"))
    instrument_col = _col_by_alias(frame, ("INSTRUMENT", "FinInstrmTp", "Instrument Type"))
    expiry_col = _col_by_alias(frame, ("EXPIRY_DT", "XpryDt", "Expiry Date", "Expiry"))
    close_col = _col_by_alias(frame, ("CLOSE", "ClsPric", "Close Price", "SETTLE_PR"))
    if not symbol_col or not expiry_col or not close_col:
        return {}
    tmp = frame.copy()
    tmp["_symbol"] = tmp[symbol_col].astype(str).str.strip().str.upper()
    tmp["_expiry"] = pd.to_datetime(tmp[expiry_col], errors="coerce", dayfirst=True)
    tmp["_close"] = pd.to_numeric(tmp[close_col], errors="coerce")
    if instrument_col:
        tmp["_instrument"] = tmp[instrument_col].astype(str).str.upper().str.strip()
        allowed = tmp["_instrument"].str.contains(r"FUTSTK|STF", regex=True, na=False)
        if allowed.any():
            tmp = tmp.loc[allowed]
        else:
            tmp = tmp.iloc[0:0]
    tmp = tmp.dropna(subset=["_expiry", "_close"])
    tmp = tmp[(tmp["_symbol"] != "") & (tmp["_close"] > 0)]
    tmp["_expiry_date"] = tmp["_expiry"].dt.date
    selection_date = today or datetime.now(IST).date()
    tmp = tmp[tmp["_expiry_date"] >= selection_date]
    if tmp.empty:
        return {}
    tmp = tmp.sort_values(["_symbol", "_expiry_date"])
    result: dict[str, dict[str, Any]] = {}
    for symbol, group in tmp.groupby("_symbol", sort=True):
        row = group.iloc[0]
        expiry = row["_expiry_date"]
        result[str(symbol)] = {
            "ltp": float(row["_close"]),
            "expiry": expiry,
            "symbol": f"{symbol} FUT · {expiry:%d-%b-%Y}",
            "asof": asof,
            "source": "NSE FO bhavcopy · EOD",
        }
    return result


def fetch_latest_fo_bhavcopy(*, max_calendar_days: int = 6,
                             timeout: float = 2.5) -> dict[str, Any]:
    """Newest accessible NSE FO bhavcopy (all candidate URLs tried in parallel)."""
    session = _nse_session()
    today = datetime.now(IST).date()
    jobs: list[tuple[date, str]] = []
    for offset in range(max(1, int(max_calendar_days))):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for url in _bhavcopy_urls(day):
            jobs.append((day, url))

    def attempt(job: tuple[date, str]):
        day, url = job
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code != 200 or len(response.content) < 100:
                return None
            parsed = _read_bhavcopy(response.content)
            if parsed is None or parsed.empty:
                return None
            contracts = _parse_fo_contracts(parsed, asof=day)
            if contracts:
                return day, url, contracts
        except Exception:
            return None
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for outcome in pool.map(attempt, jobs):      # ordered: newest day first
            if outcome:
                day, url, contracts = outcome
                return {"contracts": contracts, "symbols": sorted(contracts),
                        "asof": day, "source": url, "status": "ok"}
    return {"contracts": {}, "symbols": [], "asof": None,
            "source": "", "status": "unavailable"}


# ===================================================================== Dhan (optional, read-only)
MASTER_URLS = (
    "https://images.dhan.co/api-data/api-scrip-master.csv",
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
)
API_URL = "https://api.dhan.co/v2/marketfeed/ltp"


def load_instrument_master(timeout: float = 12) -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
    last_error: Exception | None = None
    for url in MASTER_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            frame = pd.read_csv(StringIO(response.text), low_memory=False)
            if not frame.empty:
                return frame
        except Exception as exc:
            last_error = exc
    if last_error:
        raise RuntimeError(f"Dhan instrument master unavailable: {last_error}")
    return pd.DataFrame()


def _find_col(frame: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    normalized = {re.sub(r"[^A-Z0-9]", "", str(c).upper()): str(c)
                  for c in frame.columns}
    for name in names:
        key = re.sub(r"[^A-Z0-9]", "", name.upper())
        if key in normalized:
            return normalized[key]
    return None


def _underlying(row: pd.Series, trading_col: str | None,
                custom_col: str | None, underlying_col: str | None) -> str:
    if underlying_col:
        value = str(row.get(underlying_col, "")).strip().upper()
        if value and value not in {"NAN", "NONE"}:
            return value
    for col in (custom_col, trading_col):
        if not col:
            continue
        value = str(row.get(col, "")).strip().upper()
        if not value or value in {"NAN", "NONE"}:
            continue
        for sep in (" ", "-"):
            if sep in value:
                value = value.split(sep, 1)[0].strip()
                break
        return value
    return ""


def build_security_map(master: pd.DataFrame, symbols: list[str] | tuple[str, ...]
                       ) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Return symbol->equity id and symbol->(future id, expiry) mappings."""
    if master is None or master.empty:
        return {}, {}
    sec_col = _find_col(master, ("SEM_SECURITY_ID", "SEM_SMST_SECURITY_ID", "SECURITY_ID", "SecurityId"))
    seg_col = _find_col(master, ("SEM_SEGMENT", "SEGMENT", "EXCHANGE_SEGMENT"))
    exch_col = _find_col(master, ("SEM_EXM_EXCH_ID", "EXCHANGE", "EXCH_ID"))
    inst_col = _find_col(master, ("SEM_INSTRUMENT_NAME", "INSTRUMENT", "INSTRUMENT_NAME", "SEM_EXCH_INSTRUMENT_TYPE"))
    trade_col = _find_col(master, ("SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "SYMBOL"))
    custom_col = _find_col(master, ("SEM_CUSTOM_SYMBOL", "CUSTOM_SYMBOL"))
    under_col = _find_col(master, ("SEM_UNDERLYING_SYMBOL", "UNDERLYING_SYMBOL", "UNDERLYING"))
    expiry_col = _find_col(master, ("SEM_EXPIRY_DATE", "EXPIRY_DATE", "EXPIRY"))
    if not sec_col:
        return {}, {}
    frame = master.copy()
    frame["_sec"] = frame[sec_col].astype(str).str.strip()
    frame["_segment"] = frame[seg_col].astype(str).str.upper() if seg_col else ""
    frame["_exchange"] = frame[exch_col].astype(str).str.upper() if exch_col else ""
    frame["_instrument"] = frame[inst_col].astype(str).str.upper() if inst_col else ""
    frame["_underlying"] = frame.apply(
        lambda row: _underlying(row, trade_col, custom_col, under_col), axis=1)
    if expiry_col:
        frame["_expiry"] = pd.to_datetime(frame[expiry_col], errors="coerce", dayfirst=True)
    else:
        frame["_expiry"] = pd.NaT
    wanted = {str(s).strip().upper() for s in symbols}
    frame = frame[frame["_underlying"].isin(wanted) | frame["_underlying"].eq("")]
    equity_ids: dict[str, str] = {}
    futures: dict[str, tuple[str, str]] = {}
    today = datetime.now(IST).date()
    for symbol in wanted:
        matches = frame[frame["_underlying"] == symbol]
        if matches.empty and trade_col:
            matches = frame[frame[trade_col].astype(str).str.upper() == symbol]
        eq = matches[
            matches["_segment"].str.contains("NSE_EQ|EQ", na=False)
            & ~matches["_instrument"].str.contains("FUT|OPT", na=False)
        ]
        if not eq.empty:
            equity_ids[symbol] = str(eq.iloc[0]["_sec"])
        fut = matches[
            matches["_segment"].str.contains("NSE_FNO|FNO", na=False)
            & matches["_instrument"].str.contains("FUTSTK|FUT", na=False)
        ].copy()
        if not fut.empty:
            fut = fut.dropna(subset=["_expiry"])
            fut = fut[fut["_expiry"].dt.date >= today]
            if not fut.empty:
                fut = fut.sort_values("_expiry")
                row = fut.iloc[0]
                futures[symbol] = (str(row["_sec"]), row["_expiry"].date().isoformat())
    return equity_ids, futures


def _extract_prices(payload: Any, segment: str) -> dict[str, float]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return {}
    block = data.get(segment, {})
    if isinstance(block, dict):
        result = {}
        for sec_id, row in block.items():
            if isinstance(row, dict):
                price = row.get("last_price", row.get("lastPrice", row.get("LTP")))
                try:
                    if price is not None and float(price) > 0:
                        result[str(sec_id)] = float(price)
                except (TypeError, ValueError):
                    pass
        return result
    return {}


def fetch_dhan_quotes(client_id: str, access_token: str,
                      symbols: list[str] | tuple[str, ...],
                      master: pd.DataFrame | None = None) -> dict[str, Any]:
    """Fetch live LTPs in one/batched read-only Dhan marketfeed request."""
    if not client_id or not access_token:
        return {"equity": {}, "future": {}, "status": "not configured"}
    if master is None:
        master = load_instrument_master()
    equity_ids, future_ids = build_security_map(master, symbols)
    segments = {
        "NSE_EQ": list(equity_ids.values()),
        "NSE_FNO": [value[0] for value in future_ids.values()],
    }
    id_prices: dict[str, dict[str, float]] = {"NSE_EQ": {}, "NSE_FNO": {}}
    headers = {
        "access-token": access_token,
        "client-id": client_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    for segment, ids in segments.items():
        for start in range(0, len(ids), 100):
            batch = ids[start:start + 100]
            if not batch:
                continue
            try:
                response = requests.post(API_URL, headers=headers,
                                         json={segment: [int(x) for x in batch]}, timeout=8)
                response.raise_for_status()
                id_prices[segment].update(_extract_prices(response.json(), segment))
            except Exception:
                continue
    equity = {sym: id_prices["NSE_EQ"][sec]
              for sym, sec in equity_ids.items() if sec in id_prices["NSE_EQ"]}
    future = {}
    for sym, (sec, expiry) in future_ids.items():
        if sec in id_prices["NSE_FNO"]:
            future[sym] = {"ltp": id_prices["NSE_FNO"][sec],
                           "expiry": date.fromisoformat(expiry),
                           "symbol": f"{sym} FUT · {date.fromisoformat(expiry):%d-%b-%Y}",
                           "asof": datetime.now(IST), "source": "Dhan LIVE"}
    return {"equity": equity, "future": future,
            "status": "ok" if equity or future else "no quotes",
            "equity_mapped": len(equity_ids), "future_mapped": len(future_ids)}


# ===================================================================== zone tables
TV_INTERVAL = {
    "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
    "75m": "75", "1h": "60", "2h": "120", "4h": "240", "6h": "360",
    "1d": "D", "1w": "W", "1M": "M",
}
PINE_MTF_TIMEFRAMES = ("3m", "5m", "10m", "15m", "1h", "2h", "4h", "6h", "1d")


def tradingview_url(tv_symbol: str, timeframe: str, *, future: bool = False) -> str:
    symbol = str(tv_symbol).strip()
    if future and ":" not in symbol:
        symbol = f"NSE:{symbol}"
    if future and not symbol.endswith("1!"):
        symbol += "1!"
    if ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        continuous = ticker.endswith("1!")
        core = ticker[:-2] if continuous else ticker
        encoded = f"{quote(exchange, safe='')}%3A{quote(core, safe='')}"
        if continuous:
            encoded += "1!"
    else:
        encoded = quote(symbol, safe="")
    return f"https://in.tradingview.com/chart/?symbol={encoded}&interval={TV_INTERVAL.get(timeframe, '15')}"


def latest_ltp(data: dict[str, pd.DataFrame]) -> tuple[float | None, str, Any]:
    for layer, source in (("minute", "Yahoo 1m"), ("hour", "Yahoo 60m"),
                          ("daily", "Yahoo EOD")):
        frame = normalize_ohlcv(data.get(layer))
        if frame.empty:
            continue
        try:
            price = float(frame["Close"].iloc[-1])
            if np.isfinite(price) and price > 0:
                return price, source, frame.index[-1]
        except (TypeError, ValueError):
            pass
    return None, "—", None


def _safe_price(value: Any) -> float | None:
    try:
        value = float(value)
        return value if np.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _pine_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = normalize_ohlcv(frame)
    return normalized.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }).loc[:, ["open", "high", "low", "close", "volume"]]


def _distance_pct(price: float | None, level: float) -> float:
    if price is None:
        return float("nan")
    return abs(float(level) - price) / max(abs(price), 1e-9) * 100.0


def _zone_values(zone: Zone, price: float | None) -> dict[str, Any]:
    prox, dist = float(zone.proxVal), float(zone.distVal)
    lo, hi = min(prox, dist), max(prox, dist)
    distance = 0.0 if price is not None and lo <= price <= hi else _distance_pct(price, prox)
    return {
        "Zone": "Demand" if zone.isDemand else "Supply",
        "Pattern": zone.patternType,
        "Category": zone.zoneCategory,
        "Zone State": zone.state,
        "Density Score": int(zone.densityScore),
        "HQ": bool(zone.isHQ),
        "Touch Count": int(zone.touchCount),
        "Base Count": int(zone.baseCount),
        "Zone Low": lo,
        "Zone High": hi,
        "Proximal": prox,
        "Distal": dist,
        "SL": float(zone.slVal),
        "TP": float(zone.tpVal),
        "Leg-out Mid": float(zone.legOutMidLevel),
        "Nearest Gap %": distance,
        "Leg-in TR": float(zone.legInTR),
        "Leg-out TR": float(zone.legOutTR),
        "Leg-out High": float(zone.legOutHigh),
        "Leg-out Low": float(zone.legOutLow),
        "Overnight": bool(zone.isOvernight),
        "Created": zone.timestamp,
        "Broken At": zone.brokenAt,
        "Behind": bool(zone.shadowBehind),
        "Base Break": bool(zone.baseBreaksOld),
        "Swing Break": bool(zone.swingBreak),
        "Swing Break Direction": "High" if zone.brokeHigh else "Low" if zone.brokeLow else "—",
        "Shadow": zone.shadowBox is not None,
        "Shadow Top": zone.shadowTop,
        "Shadow Bottom": zone.shadowBottom,
        "Shadow Color": zone.shadowColor or "—",
        "Flag": zone.flagText or "—",
        "Start Bar Index": int(zone.startBarIndex),
        "Created Bar Index": int(zone.createdBarIndex),
    }


def _zone_row(symbol: str, timeframe: str, zone: Zone, *, ltp: float | None,
              ltp_source: str, ltp_asof: Any, future: dict[str, Any] | None = None) -> dict[str, Any]:
    future = future or {}
    future_ltp = _safe_price(future.get("ltp"))
    delta = ltp - future_ltp if ltp is not None and future_ltp is not None else None
    premium = ("—" if delta is None else "Future Premium" if delta < 0
               else "Future Discount" if delta > 0 else "At Par")
    return {
        "Equity / TV Chart": tradingview_url(f"NSE:{symbol}", timeframe),
        "Equity": symbol,
        "Equity LTP": ltp,
        "Equity LTP Source": ltp_source,
        "Current-Month Future / TV": tradingview_url(f"NSE:{symbol}", timeframe, future=True),
        "Future Symbol": future.get("symbol") or f"{symbol} FUT · —",
        "Future Expiry": future.get("expiry"),
        "Future LTP": future_ltp,
        "Future LTP Source": future.get("source", "No futures quote"),
        "Spot − Future Δ (₹)": delta,
        "Premium / Discount": premium,
        "TF": timeframe,
        **_zone_values(zone, ltp),
        "Equity Quote As Of": ltp_asof,
    }


def _confirmed_mtf_bars(frame: pd.DataFrame, timeframe: str, *, now: datetime | None,
                        market: str) -> pd.DataFrame:
    """Avoid a still-forming daily candle in the confirmed MTF context."""
    if frame.empty or timeframe != "1d":
        return frame
    now_ist = now.astimezone(IST) if now and now.tzinfo else (now.replace(tzinfo=IST) if now else datetime.now(IST))
    if market.upper() == "NSE" and now_ist.time() < datetime.strptime("15:30", "%H:%M").time():
        if frame.index[-1].date() >= now_ist.date() and len(frame) > 1:
            return frame.iloc[:-1]
    return frame


def _scan_frame(data: dict[str, pd.DataFrame], timeframe: str, *, market: str,
                ltp: float | None, params: dict[str, Any], chart_mode: bool,
                now: datetime | None = None) -> tuple[pd.DataFrame, list[Zone]]:
    candles = timeframe_candles(data, timeframe, market=market, now=now)
    if candles.empty:
        return candles, []
    core_frame = _pine_frame(candles)
    zones = run_pine_engine(core_frame, params=params, tf=timeframe, chart_mode=chart_mode)
    return core_frame, zones


def scan_equity_zones(
    symbol_data: dict[str, dict[str, pd.DataFrame]],
    symbols: list[str] | tuple[str, ...],
    timeframes: list[str] | tuple[str, ...] = TIMEFRAMES,
    future_quotes: dict[str, dict[str, Any]] | None = None,
    equity_live_quotes: dict[str, float] | None = None,
    *, params: dict[str, Any] | None = None, now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run Pine chart-zone state/visual rules for every selected symbol/TF.

    SPEED: each symbol's work is independent, so it runs on a small thread
    pool. ``ThreadPoolExecutor.map`` yields results in call order (not
    completion order), so row order / stable-sort tie-breaks are identical
    to the original serial loop — only wall-clock time changes.
    """
    config = pine_settings(**(params or {}))
    future_quotes = future_quotes or {}
    equity_live_quotes = equity_live_quotes or {}
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}

    def _one(raw_symbol: str):
        symbol = str(raw_symbol).strip().upper()
        data = symbol_data.get(symbol) or {}
        has_daily = not normalize_ohlcv(data.get("daily")).empty
        has_minute = not normalize_ohlcv(data.get("minute")).empty
        ltp, ltp_source, ltp_asof = latest_ltp(data)
        live_ltp = _safe_price(equity_live_quotes.get(symbol))
        if live_ltp is not None:
            ltp, ltp_source, ltp_asof = live_ltp, "Dhan LIVE", datetime.now(IST)
        local_rows: list[dict[str, Any]] = []
        for timeframe in timeframes:
            try:
                _, zones = _scan_frame(data, timeframe, market="NSE", ltp=ltp,
                                       params=config, chart_mode=True, now=now)
            except Exception:
                continue
            for zone in zones:
                local_rows.append(_zone_row(symbol, timeframe, zone, ltp=ltp,
                                            ltp_source=ltp_source, ltp_asof=ltp_asof,
                                            future=future_quotes.get(symbol)))
        return symbol, has_daily, has_minute, local_rows

    rows: list[dict[str, Any]] = []
    coverage = {"symbols_requested": len(symbols), "symbols_with_daily": 0,
                "symbols_with_minute": 0, "symbols_with_zones": 0, "zone_rows": 0}
    workers = max(1, min(16, len(symbols) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for symbol, has_daily, has_minute, local_rows in pool.map(_one, symbols):
            if has_daily:
                coverage["symbols_with_daily"] += 1
            if has_minute:
                coverage["symbols_with_minute"] += 1
            if local_rows:
                coverage["symbols_with_zones"] += 1
            rows.extend(local_rows)
    coverage["zone_rows"] = len(rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["_tf_order"] = frame["TF"].map(tf_order).fillna(999)
        frame = frame.sort_values(["Nearest Gap %", "_tf_order", "Density Score"],
                                  ascending=[True, True, False], kind="stable")
        frame = frame.drop(columns=["_tf_order"]).reset_index(drop=True)
    return frame, coverage


def scan_global_zones(
    global_data: dict[str, dict[str, pd.DataFrame]],
    instruments: list[dict[str, str]] | tuple[dict[str, str], ...],
    timeframes: list[str] | tuple[str, ...] = TIMEFRAMES,
    *, params: dict[str, Any] | None = None, now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    config = pine_settings(**(params or {}))
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}

    def _one(instrument: dict[str, str]):
        key = str(instrument["key"])
        data = global_data.get(key) or {}
        if normalize_ohlcv(data.get("daily")).empty:
            return key, False, []
        ltp, source, asof = latest_ltp(data)
        local_rows: list[dict[str, Any]] = []
        for timeframe in timeframes:
            try:
                _, zones = _scan_frame(data, timeframe, market="GLOBAL", ltp=ltp,
                                       params=config, chart_mode=True, now=now)
            except Exception:
                continue
            for zone in zones:
                local_rows.append({
                    "Global Instrument / TV Chart": tradingview_url(instrument["tv"], timeframe),
                    "Instrument": instrument.get("label", key),
                    "LTP": ltp,
                    "LTP Source": source,
                    "TF": timeframe,
                    **_zone_values(zone, ltp),
                    "Quote As Of": asof,
                })
        return key, True, local_rows

    rows: list[dict[str, Any]] = []
    coverage = {"instruments_requested": len(instruments), "instruments_with_data": 0,
                "zone_rows": 0}
    workers = max(1, min(10, len(instruments) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, has_data, local_rows in pool.map(_one, instruments):
            if has_data:
                coverage["instruments_with_data"] += 1
            rows.extend(local_rows)
    coverage["zone_rows"] = len(rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["_tf_order"] = frame["TF"].map(tf_order).fillna(999)
        frame = frame.sort_values(["Nearest Gap %", "_tf_order", "Density Score"],
                                  ascending=[True, True, False], kind="stable")
        frame = frame.drop(columns=["_tf_order"]).reset_index(drop=True)
    return frame, coverage


def scan_mtf_levels(
    symbol_data: dict[str, dict[str, pd.DataFrame]],
    symbols: list[str] | tuple[str, ...],
    instruments: list[dict[str, str]] | tuple[dict[str, str], ...],
    global_data: dict[str, dict[str, pd.DataFrame]],
    *, params: dict[str, Any] | None = None, show_mtf: bool = True,
    filter_distant: bool = True, mtf_near_pct: float = 8.0,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Pine MTF's latest active demand/supply proximal level per context."""
    if not show_mtf:
        return pd.DataFrame()
    config = pine_settings(**(params or {}))
    contexts = [(str(symbol).strip().upper(), symbol_data.get(str(symbol).strip().upper()) or {},
                 "NSE", f"NSE:{str(symbol).strip().upper()}") for symbol in symbols]
    contexts.extend((str(item["key"]), global_data.get(str(item["key"])) or {},
                     "GLOBAL", str(item["tv"])) for item in instruments)

    def _one(ctx):
        label, data, market, tv_symbol = ctx
        ltp, source, _ = latest_ltp(data)
        local_rows: list[dict[str, Any]] = []
        for timeframe in PINE_MTF_TIMEFRAMES:
            bars = timeframe_candles(data, timeframe, market=market, now=now)
            bars = _confirmed_mtf_bars(bars, timeframe, now=now, market=market)
            if bars.empty:
                continue
            core = _pine_frame(bars)
            try:
                zones = run_pine_engine(core, params=config, tf=timeframe, chart_mode=False)
            except Exception:
                continue
            for side, zone in latest_mtf_by_side(zones).items():
                level = float(zone.proxVal)
                distance = _distance_pct(ltp, level)
                near = ltp is not None and distance <= float(mtf_near_pct)
                if filter_distant and not near:
                    continue
                local_rows.append({
                    "Instrument": label,
                    "TradingView": tradingview_url(tv_symbol, timeframe),
                    "TF": timeframe,
                    "Side": side,
                    "Proximal Line": level,
                    "Zone State": zone.state,
                    "Touch Count": zone.touchCount,
                    "Created": zone.timestamp,
                    "Last Price": ltp,
                    "Distance %": distance,
                    "LTP Source": source,
                    "MTF Line Visible": bool(near or not filter_distant),
                })
        return local_rows

    rows: list[dict[str, Any]] = []
    workers = max(1, min(16, len(contexts) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for local_rows in pool.map(_one, contexts):
            rows.extend(local_rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values("Distance %", kind="stable").reset_index(drop=True)
    return frame


# ===================================================================== cached data + scans
@st.cache_data(ttl=90, show_spinner=False, max_entries=16)
def cached_yahoo_candles(tickers: tuple[str, ...], period: str, interval: str):
    return download_yahoo_candles(tickers, period, interval)


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def cached_fo_bhavcopy():
    return fetch_latest_fo_bhavcopy()


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def cached_dhan_master():
    return load_instrument_master()


def _dhan_credentials() -> tuple[str, str]:
    client_id, token = "", ""
    try:
        section = st.secrets.get("dhan", {})
        client_id = str(section.get("client_id", "") or "").strip()
        token = str(section.get("access_token", "") or "").strip()
    except Exception:
        pass
    return (client_id or os.getenv("DHAN_CLIENT_ID", "").strip(),
            token or os.getenv("DHAN_ACCESS_TOKEN", "").strip())


def _layers_for(tfs: tuple[str, ...], full: bool) -> dict[str, tuple[str, str]]:
    """Download only the Yahoo layers the selected timeframes really need."""
    if full:
        return {"minute": ("7d", "1m"), "hour": ("2y", "60m"), "daily": ("5y", "1d")}
    need_min = any(t in MINUTE_TFS for t in tfs)
    need_hour = any(t in HOUR_TFS for t in tfs)
    need_day = any(t in DAY_TFS for t in tfs)
    # A short 1m / 1d layer is always fetched so LTP stays fresh and daily-coverage checks work.
    layers = {"minute": ("7d" if need_min else "5d", "1m"),
              "daily": ("5y" if need_day else "1mo", "1d")}
    if need_hour:
        layers["hour"] = ("2y", "60m")
    return layers


def _load_bundle(tickers: tuple[str, ...], layers: dict[str, tuple[str, str]]) -> dict[str, dict[str, pd.DataFrame]]:
    tickers = tuple(dict.fromkeys(tickers))
    loaded: dict[str, dict[str, pd.DataFrame]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(layers))) as pool:
        jobs = {layer: pool.submit(cached_yahoo_candles, tickers, period, interval)
                for layer, (period, interval) in layers.items()}
        for layer, job in jobs.items():
            try:
                result = job.result()
            except Exception:
                result = {}
            for ticker, frame in result.items():
                loaded.setdefault(ticker, {})[layer] = frame
    return loaded


def _global_data(loaded: dict[str, dict[str, pd.DataFrame]]):
    return {item["key"]: loaded.get(item["yahoo"], {}) for item in GLOBAL_INSTRUMENTS}


@st.cache_data(ttl=120, show_spinner=False, max_entries=6)
def cached_equity_scan(symbols: tuple[str, ...], tfs: tuple[str, ...], params_items: tuple):
    params = dict(params_items)
    snapshot = cached_fo_bhavcopy()
    future_quotes = dict(snapshot.get("contracts") or {})
    loaded = _load_bundle(tuple(yahoo_ticker(s) for s in symbols), _layers_for(tfs, False))
    equity_data = {s: loaded.get(yahoo_ticker(s), {}) for s in symbols}
    live_quotes: dict[str, float] = {}
    dhan_status = "NSE/Yahoo fallback"
    client_id, access_token = _dhan_credentials()
    if client_id and access_token:
        try:
            live = fetch_dhan_quotes(client_id, access_token, list(symbols), cached_dhan_master())
            live_quotes = live.get("equity", {})
            future_quotes.update(live.get("future", {}))
            dhan_status = f"Dhan live · {len(live_quotes)} cash / {len(live.get('future', {}))} fut"
        except Exception as exc:
            dhan_status = f"Dhan unavailable · {type(exc).__name__}"
    frame, coverage = scan_equity_zones(equity_data, list(symbols), list(tfs),
                                        future_quotes, live_quotes, params=params)
    return frame, coverage, dhan_status


@st.cache_data(ttl=120, show_spinner=False, max_entries=6)
def cached_global_scan(tfs: tuple[str, ...], params_items: tuple):
    params = dict(params_items)
    loaded = _load_bundle(tuple(i["yahoo"] for i in GLOBAL_INSTRUMENTS), _layers_for(tfs, False))
    frame, coverage = scan_global_zones(_global_data(loaded), GLOBAL_INSTRUMENTS, list(tfs), params=params)
    return frame, coverage


@st.cache_data(ttl=120, show_spinner=False, max_entries=4)
def cached_mtf_scan(symbols: tuple[str, ...], params_items: tuple):
    params = dict(params_items)
    tickers = tuple(yahoo_ticker(s) for s in symbols) + tuple(i["yahoo"] for i in GLOBAL_INSTRUMENTS)
    loaded = _load_bundle(tickers, _layers_for((), True))
    equity_data = {s: loaded.get(yahoo_ticker(s), {}) for s in symbols}
    return scan_mtf_levels(
        equity_data, list(symbols), GLOBAL_INSTRUMENTS, _global_data(loaded),
        params=params, show_mtf=bool(params["showMTF"]),
        filter_distant=bool(params["filterDistantMTF"]),
        mtf_near_pct=float(params["mtfNearPct"]))


def _clear_scan_caches() -> None:
    """Scan / Refresh: only invalidate derived scan results. Raw Yahoo candles
    (90s TTL) and the NSE bhavcopy (6h TTL) keep serving from cache, which is
    the single biggest speed win — no more full re-download on every click."""
    for fn in (cached_equity_scan, cached_global_scan, cached_mtf_scan):
        fn.clear()


def _clear_all_caches() -> None:
    """Hard refresh: wipe everything, including raw market-data caches."""
    for fn in (cached_yahoo_candles, cached_fo_bhavcopy, cached_dhan_master,
               cached_equity_scan, cached_global_scan, cached_mtf_scan):
        fn.clear()


# ===================================================================== UI helpers
try:
    _ST_VER = tuple(int(p) for p in st.__version__.split(".")[:2])
except Exception:
    _ST_VER = (1, 0)
_STRETCH: dict[str, Any] = {"width": "stretch"} if _ST_VER >= (1, 50) else {"use_container_width": True}
_HAS_SEGMENTED = hasattr(st, "segmented_control")

st.markdown("""
<style>
:root { color-scheme: dark; }
.stApp { background:#0b0f14; color:#e8edf3; font-family:-apple-system,"Segoe UI",Roboto,sans-serif; }
[data-testid="stAppViewContainer"] .block-container { max-width:99vw; padding:1.1rem 1.1rem .6rem; }
[data-testid="stHeader"] { background:rgba(11,15,20,.92); }
div[data-testid="stVerticalBlockBorderWrapper"] { background:#101722; border-color:#1f2a38; border-radius:10px; }
[data-testid="stWidgetLabel"] p { font-size:.66rem; letter-spacing:.08em; text-transform:uppercase; color:#889bb0; font-weight:700; }
/* spreadsheet-ish grid look for the data table */
[data-testid="stDataFrame"] { border:1px solid #1f2a38; border-radius:8px; overflow:hidden; }
[data-testid="stMetricValue"] { font-size:1.35rem; }
[data-testid="stMetric"] { background:#101722; border:1px solid #1f2a38; border-radius:10px; padding:.5rem .7rem; }
button[kind="primary"] { font-weight:700; }
.hdr-card { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem;
  background:linear-gradient(135deg,#101722,#0c1016); border:1px solid #1f2a38; border-radius:12px;
  padding:.65rem 1rem; margin-bottom:.6rem; }
.hdr-title { font-size:1.25rem; font-weight:800; letter-spacing:-.02em; }
.hdr-title span { color:#5fb6ff; font-weight:600; font-size:.85rem; margin-left:.4rem; }
.hdr-meta { color:#8c9aaa; font-size:.78rem; text-align:right; }
.statusbar { display:flex; gap:1rem; align-items:center; color:#9fb0c3; font-size:.78rem;
  padding:.35rem .2rem; border-top:1px solid #1f2a38; margin-top:.3rem; flex-wrap:wrap; }
.statusbar b { color:#e8edf3; }
.pending-note { color:#f5c451; font-size:.8rem; }
.src { color:#67788c; font-size:.72rem; margin-top:.4rem; }
</style>
""", unsafe_allow_html=True)

LABELS = {
    "accountCapital": "Account Capital", "riskPct": "Risk %", "targetRR": "Target RR",
    "slBufferAtr": "SL Buffer ATR", "atrPeriod": "ATR Period", "volSmaPeriod": "Volume SMA Period",
    "legOutTrMult": "Leg-Out TR Multiplier", "legOutMinTrRatio": "Leg-Out Min TR Ratio vs Leg-In",
    "hqLegOutTrMult": "HQ Leg-Out TR Multiplier", "hqLegInAtrMult": "HQ Leg-In ATR Multiplier",
    "maxBaseAtrMult": "Max Base TR ATR Multiplier", "maxWickPct": "Max Wick %",
    "minBaseCountInput": "Min Base Count", "maxBaseCountInput": "Max Base Count",
    "legInMinAtrMult": "Leg-In Min ATR Multiplier", "minClvPct": "Min CLV %",
    "legInToBaseSizeMult": "Leg-In to Base Size Multiplier", "legInMinBodyPct": "Leg-In Min Body %",
    "useImbalance": "Use Imbalance", "maxImbalanceMult": "Max Imbalance vs Leg-In Mult",
    "relaxGapCapOvernight": "Relax Gap Cap on Overnight", "genuineGapBonus": "Genuine Gap Score Bonus",
    "overnightGapBonus": "Overnight Gap Score Bonus", "rejectOppositeCoverPct": "Reject Opposite Cover %",
    "minValidScore": "Min Valid Score", "hqScoreThreshold": "HQ Score Threshold",
    "legOutBodyHeavyPct": "Leg-Out Body Heavy Pressure %", "testedLegOutRetracePct": "Tested Leg-Out Retrace %",
    "maxTestedCount": "Max Tested Count", "legOutToLegInBodyMult": "Leg-Out to Leg-In Body Mult",
    "baseBoringMaxBodyPct": "Base Small-Body (Indecision) %", "eodHighBufferPct": "EOD High Buffer % (inactive)",
    "eodLowBufferPct": "EOD Low Buffer % (inactive)", "scanAfterCandleComplete": "Scan After Candle Complete",
    "scanMonthlyOnce": "Scan Monthly Once", "scanWeeklyOnce": "Scan Weekly Once", "scanDailyOnce": "Scan Daily Once",
    "enableClosingWickCheck": "Use Closing-Side Wick Guard", "legInMinClosingWickPct": "Leg-In Min Closing Wick %",
    "enableLegOutCoverCheck": "Use Leg-Out Coverage Guard", "legOutMaxCoverPct": "Leg-Out Max Cover %",
    "enableHQBaseColourCheck": "Use Boring-Colour HQ Flag", "hqBaseColourProbabilityPct": "HQ Base Colour Probability %",
    "enableWhiteAreaCheck": "Use White Area Check", "showMTF": "Show other-TF proximal lines",
    "filterDistantMTF": "Hide distant MTF lines (display only)", "mtfNearPct": "Show lines within % of last price",
    "showShadow": "Shadow when prior zone behind / broken / swing broken", "showFlags": "Show BASE BREAK / SWING labels",
    "shadowAtr": "Shadow padding × ATR", "swingLeft": "Confirmed swing: bars on left",
    "swingRight": "Confirmed swing: bars on right", "maxStored": "Max chart zones (boxes ≤ 240)",
}
CORE_KEYS = [
    "accountCapital", "riskPct", "targetRR", "slBufferAtr", "atrPeriod", "volSmaPeriod",
    "legOutTrMult", "legOutMinTrRatio", "hqLegOutTrMult", "hqLegInAtrMult",
    "maxBaseAtrMult", "maxWickPct", "minBaseCountInput", "maxBaseCountInput",
    "legInMinAtrMult", "minClvPct", "legInToBaseSizeMult", "legInMinBodyPct",
    "useImbalance", "maxImbalanceMult", "relaxGapCapOvernight", "genuineGapBonus",
    "overnightGapBonus", "rejectOppositeCoverPct", "minValidScore", "hqScoreThreshold",
    "legOutBodyHeavyPct", "testedLegOutRetracePct", "maxTestedCount",
]
PARITY_KEYS = [
    "legOutToLegInBodyMult", "baseBoringMaxBodyPct", "eodHighBufferPct", "eodLowBufferPct",
    "scanAfterCandleComplete", "scanMonthlyOnce", "scanWeeklyOnce", "scanDailyOnce",
    "enableClosingWickCheck", "legInMinClosingWickPct", "enableLegOutCoverCheck",
    "legOutMaxCoverPct", "enableHQBaseColourCheck", "hqBaseColourProbabilityPct",
    "enableWhiteAreaCheck",
]
DISPLAY_KEYS = [
    "showMTF", "filterDistantMTF", "mtfNearPct", "showShadow", "showFlags",
    "shadowAtr", "swingLeft", "swingRight", "maxStored",
]
INT_BOUNDS = {
    "atrPeriod": (1, 500), "volSmaPeriod": (1, 500),
    "minBaseCountInput": (0, 20), "maxBaseCountInput": (0, 20),
    "genuineGapBonus": (-500, 500), "overnightGapBonus": (-500, 500),
    "minValidScore": (-500, 1000), "hqScoreThreshold": (-500, 1000),
    "maxTestedCount": (-100, 100), "swingLeft": (1, 20),
    "swingRight": (1, 20), "maxStored": (10, 240),
}
FLOAT_BOUNDS = {
    "mtfNearPct": (0.1, 100.0, 0.5), "shadowAtr": (0.0, 1.0, 0.01),
    "accountCapital": (-1e9, 1e9, 1000.0), "riskPct": (-10000.0, 10000.0, 0.1),
    "targetRR": (-10000.0, 10000.0, 0.25), "slBufferAtr": (-10000.0, 10000.0, 0.01),
    "legOutTrMult": (-10000.0, 10000.0, 0.1), "legOutMinTrRatio": (-10000.0, 10000.0, 0.1),
    "hqLegOutTrMult": (-10000.0, 10000.0, 0.1), "hqLegInAtrMult": (-10000.0, 10000.0, 0.1),
    "maxBaseAtrMult": (-10000.0, 10000.0, 0.1), "maxWickPct": (-10000.0, 10000.0, 0.01),
    "legInMinAtrMult": (-10000.0, 10000.0, 0.1), "minClvPct": (-10000.0, 10000.0, 0.01),
    "legInToBaseSizeMult": (-10000.0, 10000.0, 0.1), "legInMinBodyPct": (-10000.0, 10000.0, 0.01),
    "maxImbalanceMult": (-10000.0, 10000.0, 0.1), "rejectOppositeCoverPct": (-10000.0, 10000.0, 0.01),
    "legOutBodyHeavyPct": (-10000.0, 10000.0, 0.01), "testedLegOutRetracePct": (-10000.0, 10000.0, 0.05),
    "legOutToLegInBodyMult": (-10000.0, 10000.0, 0.1), "baseBoringMaxBodyPct": (-10000.0, 10000.0, 0.01),
    "eodHighBufferPct": (-10000.0, 10000.0, 0.5), "eodLowBufferPct": (-10000.0, 10000.0, 0.5),
    "legInMinClosingWickPct": (-10000.0, 10000.0, 0.01), "legOutMaxCoverPct": (-10000.0, 10000.0, 0.5),
    "hqBaseColourProbabilityPct": (-10000.0, 10000.0, 0.5),
}


def _pine_controls() -> tuple[dict[str, Any], bool]:
    """All Pine v6 inputs live in a sidebar FORM: editing never triggers a rerun/scan."""
    values: dict[str, Any] = {}
    with st.sidebar:
        st.markdown("### ⚙️ Pine v6 inputs")
        with st.form("pine_inputs", border=False):
            for title, keys in (("Original zone inputs", CORE_KEYS),
                                ("Parity inputs (inactive in zone validation)", PARITY_KEYS),
                                ("MTF / shadow / swing display inputs", DISPLAY_KEYS)):
                st.markdown(f"**{title}**")
                for start in range(0, len(keys), 2):
                    cols = st.columns(2)
                    for col, key in zip(cols, keys[start:start + 2]):
                        default = PINE_DEFAULTS[key]
                        label = LABELS[key]
                        widget_key = f"pinev6_{key}"
                        if isinstance(default, bool):
                            values[key] = bool(col.toggle(label, value=default, key=widget_key))
                        elif isinstance(default, int):
                            low, high = INT_BOUNDS.get(key, (-10000, 10000))
                            values[key] = int(col.number_input(label, min_value=low, max_value=high,
                                                               value=default, step=1, key=widget_key))
                        else:
                            low, high, step = FLOAT_BOUNDS.get(key, (-10000.0, 10000.0, 0.1))
                            values[key] = float(col.number_input(label, min_value=float(low), max_value=float(high),
                                                                 value=float(default), step=float(step), key=widget_key))
            applied = st.form_submit_button("Apply inputs & rescan", type="primary", **_STRETCH)
        st.caption("EOD High/Low buffers, maxImbalanceMult, relaxGapCapOvernight और parity flags "
                   "Pine के मूल code की तरह zone validation में use नहीं होते। "
                   "Candle feed (Yahoo) और TradingView अलग होने से 1:1 price-data परिणाम की गारंटी नहीं।")
        st.divider()
        hard_refresh = st.button("⟳ Hard refresh (re-download everything)", **_STRETCH)
    return {**PINE_DEFAULTS, **values}, bool(applied), bool(hard_refresh)


CORE_FIELDS = ["TF", "Zone", "Pattern", "Zone State", "Density Score", "HQ",
               "Touch Count", "Proximal", "Distal", "SL", "TP", "Nearest Gap %", "Created"]
SHADOW_FIELDS = ["Behind", "Base Break", "Swing Break", "Swing Break Direction",
                 "Shadow", "Shadow Top", "Shadow Bottom", "Shadow Color"]
EXTRA_FIELDS = ["Category", "Base Count", "Zone Low", "Zone High", "Leg-out Mid", "Leg-in TR",
                "Leg-out TR", "Overnight", "Broken At"]
MAX_ROWS = 4000


def _column_config(cols: list[str]) -> dict[str, Any]:
    C = st.column_config
    cfg: dict[str, Any] = {
        "Equity / TV Chart": C.LinkColumn("Symbol", width="medium", display_text=r"symbol=NSE%3A([^&]+)"),
        "Current-Month Future / TV": C.LinkColumn("Future", width="medium", display_text=r"symbol=NSE%3A([^&]+)"),
        "Global Instrument / TV Chart": C.LinkColumn("Chart", width="small", display_text="📈"),
        "Instrument": C.TextColumn("Instrument", width="medium"),
        "Equity LTP": C.NumberColumn("LTP", width="small", format="₹%.2f"),
        "Future LTP": C.NumberColumn("Fut LTP", width="small", format="₹%.2f"),
        "Spot − Future Δ (₹)": C.NumberColumn("Spot−Fut Δ", width="small", format="%.2f"),
        "LTP": C.NumberColumn("LTP", width="small", format="%.2f"),
        "TF": C.TextColumn("TF", width="small"),
        "Zone": C.TextColumn("Zone", width="small"),
        "Pattern": C.TextColumn("Pattern", width="small"),
        "Category": C.TextColumn("Category", width="small"),
        "Zone State": C.TextColumn("State", width="small"),
        "Density Score": C.ProgressColumn("Score", width="small", format="%d", min_value=0, max_value=140),
        "HQ": C.TextColumn("HQ", width="small"),
        "Touch Count": C.NumberColumn("Touch", width="small", format="%d"),
        "Base Count": C.NumberColumn("Base", width="small", format="%d"),
        "Nearest Gap %": C.NumberColumn("Gap %", width="small", format="%.2f"),
        "Created": C.DatetimeColumn("Created", width="small", format="DD MMM HH:mm"),
        "Broken At": C.NumberColumn("Broken bar", width="small", format="%d"),
    }
    for key in ("Proximal", "Distal", "SL", "TP", "Zone Low", "Zone High", "Leg-out Mid",
                "Leg-in TR", "Leg-out TR", "Shadow Top", "Shadow Bottom"):
        cfg[key] = C.NumberColumn(key, width="small", format="%.2f")
    return {k: v for k, v in cfg.items() if k in cols}


def _prepare_display(view: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    cols = [c for c in order if c in view.columns]
    disp = view.loc[:, cols].head(MAX_ROWS).copy()
    if "Zone" in disp:
        disp["Zone"] = disp["Zone"].map({"Demand": "🟢 Demand", "Supply": "🔴 Supply"}).fillna(disp["Zone"])
    if "Zone State" in disp:
        disp["Zone State"] = disp["Zone State"].map(
            {"Fresh": "🟢 Fresh", "Tested": "🟡 Tested", "Broken": "⚪ Broken"}).fillna(disp["Zone State"])
    if "HQ" in disp:
        disp["HQ"] = disp["HQ"].map({True: "★", False: ""}).fillna("")
    if "Created" in disp:
        disp["Created"] = pd.to_datetime(disp["Created"], errors="coerce")
    if "Broken At" in disp:
        disp["Broken At"] = pd.to_numeric(disp["Broken At"], errors="coerce")
    if "Future Expiry" in disp:
        disp["Future Expiry"] = disp["Future Expiry"].astype(str).replace({"None": "—", "NaT": "—"})
    return disp


def _show_zone_table(view: pd.DataFrame, *, global_table: bool, all_cols: bool,
                     show_shadow: bool, show_flags: bool, csv_name: str) -> None:
    if view.empty:
        st.info("इन filters में कोई zone नहीं मिला।")
        return
    if global_table:
        order = ["Global Instrument / TV Chart", "Instrument", "LTP", *CORE_FIELDS]
        if all_cols:
            order += EXTRA_FIELDS + (SHADOW_FIELDS if show_shadow else []) + (["Flag"] if show_flags else []) + ["LTP Source"]
    else:
        order = ["Equity / TV Chart", "Equity LTP", "Future LTP", *CORE_FIELDS]
        if all_cols:
            order = ["Equity / TV Chart", "Equity LTP", "Current-Month Future / TV", "Future Symbol",
                     "Future LTP", "Spot − Future Δ (₹)", "Premium / Discount", *CORE_FIELDS,
                     *EXTRA_FIELDS, *(SHADOW_FIELDS if show_shadow else []),
                     *(["Flag"] if show_flags else []),
                     "Future Expiry", "Future LTP Source", "Equity LTP Source"]
    disp = _prepare_display(view, order)
    height = max(280, min(820, 34 * (len(disp) + 1) + 3))
    st.dataframe(disp, column_config=_column_config(list(disp.columns)),
                 hide_index=True, height=height, **_STRETCH)
    left, right = st.columns([1, 5])
    left.download_button("⬇️ CSV", view.to_csv(index=False).encode("utf-8-sig"),
                         file_name=csv_name, mime="text/csv")
    if len(view) > MAX_ROWS:
        right.caption(f"Showing first {MAX_ROWS:,} of {len(view):,} rows (CSV has all).")


def _show_mtf_table(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("MTF में अभी कोई active proximal line नहीं दिख रहा (या showMTF बंद है)।")
        return
    view = frame[["Instrument", "TradingView", "TF", "Side", "Proximal Line", "Zone State",
                  "Touch Count", "Created", "Last Price", "Distance %", "LTP Source",
                  "MTF Line Visible"]].copy()
    view["Side"] = view["Side"].map({"Demand": "🟢 Demand", "Supply": "🔴 Supply"}).fillna(view["Side"])
    view["Zone State"] = view["Zone State"].map(
        {"Fresh": "🟢 Fresh", "Tested": "🟡 Tested"}).fillna(view["Zone State"])
    view["Created"] = pd.to_datetime(view["Created"], errors="coerce")
    C = st.column_config
    st.dataframe(view, hide_index=True, height=max(280, min(820, 34 * (len(view) + 1) + 3)),
                 column_config={
                     "TradingView": C.LinkColumn("Chart", display_text="📈"),
                     "Proximal Line": C.NumberColumn("Proximal", format="%.2f"),
                     "Zone State": C.TextColumn("State"),
                     "Touch Count": C.NumberColumn("Touches", format="%d"),
                     "Last Price": C.NumberColumn("LTP", format="%.2f"),
                     "Distance %": C.NumberColumn("Dist %", format="%.2f"),
                     "Created": C.DatetimeColumn("Created", format="DD MMM HH:mm"),
                 }, **_STRETCH)
    st.download_button("⬇️ CSV", view.to_csv(index=False).encode("utf-8-sig"),
                       file_name="mtf_proximal_lines.csv", mime="text/csv")


def _kpi_strip(frame: pd.DataFrame, side_col: str = "Zone") -> None:
    if frame is None or frame.empty:
        cols = st.columns(6)
        for c, label in zip(cols, ["Total", "Fresh", "Tested", "Broken", "Demand", "Supply"]):
            c.metric(label, 0)
        return
    counts = frame["Zone State"].value_counts()
    sides = frame[side_col].value_counts() if side_col in frame.columns else pd.Series(dtype=int)
    hq = int(frame["HQ"].sum()) if "HQ" in frame.columns else 0
    cols = st.columns(7)
    cols[0].metric("Total zones", f"{len(frame):,}")
    cols[1].metric("🟢 Fresh", int(counts.get("Fresh", 0)))
    cols[2].metric("🟡 Tested", int(counts.get("Tested", 0)))
    cols[3].metric("⚪ Broken", int(counts.get("Broken", 0)))
    cols[4].metric("Demand", int(sides.get("Demand", 0)))
    cols[5].metric("Supply", int(sides.get("Supply", 0)))
    cols[6].metric("★ HQ", hq)


# ===================================================================== page
ss = st.session_state
UNIVERSE_N = {"50": 50, "100": 100, "150": 150, "All": None}
DEFAULT_TFS = ["5m", "15m", "1h", "1d"]
VIEWS = ["NSE Futures", "Global", "MTF Lines"]
INTRADAY_TFS = [t for t in TIMEFRAMES if t in MINUTE_TFS or t in HOUR_TFS]

header_slot = st.empty()
params, form_apply, hard_refresh = _pine_controls()


def _single(label: str, options: list[str], default: str, key: str) -> str:
    if key not in ss:
        ss[key] = default
    if _HAS_SEGMENTED:
        val = st.segmented_control(label, options, default=ss[key], key=key)
        return val if val is not None else default
    return st.selectbox(label, options, index=options.index(ss.get(key, default)), key=key)


def _set_tfs(values: list[str]) -> None:
    ss["tfs"] = list(values)


with st.container(border=True):
    top = st.columns([1.0, 4.4, 1.3, 1.3])
    with top[0]:
        universe_choice = _single("Universe", list(UNIVERSE_N), "50", "universe")
    with top[1]:
        if "tfs" not in ss:
            ss["tfs"] = list(DEFAULT_TFS)
        selected_tfs = st.multiselect("Timeframes", list(TIMEFRAMES), key="tfs")
        q = st.columns([1, 1, 1, 1, 8])
        q[0].button("Intraday", on_click=_set_tfs, args=(INTRADAY_TFS,), key="tf_intra")
        q[1].button("Daily+", on_click=_set_tfs, args=(["1d", "1w", "1M"],), key="tf_daily")
        q[2].button("All", on_click=_set_tfs, args=(list(TIMEFRAMES),), key="tf_all")
        q[3].button("Clear", on_click=_set_tfs, args=([],), key="tf_clear")
    with top[2]:
        if "states" not in ss:
            ss["states"] = ["Fresh", "Tested"]
        selected_states = st.multiselect("State", ["Fresh", "Tested", "Broken"], key="states")
    with top[3]:
        if "sides" not in ss:
            ss["sides"] = ["Demand", "Supply"]
        selected_sides = st.multiselect("Side", ["Demand", "Supply"], key="sides")

    bot = st.columns([1.5, 2.3, 1.6, 1.1, 0.9, 0.9, 1.3])
    with bot[0]:
        view_name = _single("View", VIEWS, "NSE Futures", "view")
    symbol_query = bot[1].text_input("Filter", placeholder="RELIANCE, SBIN, Gold", key="query")
    with bot[2]:
        sort_mode = _single("Distance", ["Nearest ↑", "Farthest ↓"], "Nearest ↑", "sort")
    with bot[3]:
        all_cols = st.toggle("All cols", value=False, key="all_cols")
    with bot[4]:
        auto_refresh = st.toggle("Auto 2m", value=False, key="auto_refresh")
    with bot[5]:
        st.write("")
    with bot[6]:
        run_scan = st.button("🚀 Scan / Refresh", type="primary", **_STRETCH)

if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=120_000, key="pine_zone_autorefresh")
    except Exception:
        st.caption("Auto-refresh package unavailable; press Scan / Refresh.")

if not selected_tfs:
    st.warning("कम-से-कम एक timeframe चुनें।")
    st.stop()

scheduled = bool(auto_refresh and time.time() - float(ss.get("last_scan_epoch", 0.0)) >= 115)
if hard_refresh:
    _clear_all_caches()
elif run_scan or scheduled:
    _clear_scan_caches()

with st.spinner("NSE F&O contract list…"):
    try:
        fo_snapshot = cached_fo_bhavcopy()
    except Exception:
        fo_snapshot = {"contracts": {}, "symbols": [], "asof": None, "source": "", "status": "unavailable"}
symbols = list(fo_snapshot.get("symbols") or FNO_FALLBACK)
using_fallback = not bool(fo_snapshot.get("symbols"))
fo_label = "static fallback" if using_fallback else f"NSE bhavcopy {fo_snapshot.get('asof')}"

if "applied" not in ss or run_scan or form_apply or hard_refresh or scheduled:
    n = UNIVERSE_N[universe_choice]
    ss["applied"] = {
        "symbols": tuple(symbols[:n] if n else symbols),
        "tfs": tuple(selected_tfs),
        "universe": universe_choice,
        "params": tuple(sorted(params.items())),
        "at": datetime.now(IST).strftime("%d-%b %H:%M IST"),
    }
    ss["last_scan_epoch"] = time.time()
ap = ss["applied"]
params_applied = dict(ap["params"])

header_slot.markdown(
    f'<div class="hdr-card"><div class="hdr-title">🎯 Zone MTF <span>Pine v6</span></div>'
    f'<div class="hdr-meta">Universe {len(ap["symbols"])}/{len(symbols)} · {fo_label}'
    f'<br>last scan {ap["at"]}</div></div>',
    unsafe_allow_html=True)

pending = (ap["universe"] != universe_choice) or (set(ap["tfs"]) != set(selected_tfs))
if pending:
    st.markdown('<p class="pending-note">⚠️ Universe / timeframes बदले हैं — नए data के लिए '
               '<b>Scan / Refresh</b> दबाएँ।</p>', unsafe_allow_html=True)


def _filter_and_sort(frame: pd.DataFrame, instrument_col: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    view = frame[frame["TF"].isin(selected_tfs)]
    view = view[view["Zone State"].isin(selected_states)] if selected_states else view.iloc[0:0]
    side_col = "Zone"
    view = view[view[side_col].isin(selected_sides)] if selected_sides else view.iloc[0:0]
    if symbol_query.strip():
        parts = [p.strip() for p in re.split(r"[,\s]+", symbol_query) if p.strip()]
        pattern = "|".join(re.escape(p) for p in parts)
        view = view[view[instrument_col].astype(str).str.contains(pattern, case=False, na=False, regex=True)]
    return view.sort_values("Nearest Gap %", ascending=sort_mode.startswith("Nearest"), kind="stable")


try:
    if view_name == "NSE Futures":
        t0 = time.time()
        with st.spinner(f"Scanning {len(ap['symbols'])} NSE futures × {len(ap['tfs'])} timeframes …"):
            rows, cov, dhan_status = cached_equity_scan(ap["symbols"], ap["tfs"], ap["params"])
        elapsed = time.time() - t0
        view = _filter_and_sort(rows, "Equity")
        _kpi_strip(view)
        st.markdown(
            f'<div class="statusbar"><b>{len(view):,}</b> shown / {len(rows):,} total zones · '
            f'coverage <b>{cov.get("symbols_with_daily", 0)}/{cov.get("symbols_requested", 0)}</b> daily · '
            f'{cov.get("symbols_with_minute", 0)} minute · {dhan_status} · {elapsed:.1f}s</div>',
            unsafe_allow_html=True)
        _show_zone_table(view, global_table=False, all_cols=all_cols,
                         show_shadow=bool(params_applied["showShadow"]),
                         show_flags=bool(params_applied["showFlags"]), csv_name="nse_pine_zone_scan.csv")
    elif view_name == "Global":
        t0 = time.time()
        with st.spinner("Scanning global instruments …"):
            grows, gcov = cached_global_scan(ap["tfs"], ap["params"])
        elapsed = time.time() - t0
        view = _filter_and_sort(grows, "Instrument")
        _kpi_strip(view)
        st.markdown(
            f'<div class="statusbar"><b>{len(view):,}</b> shown / {len(grows):,} total zones · '
            f'{gcov.get("instruments_with_data", 0)}/{gcov.get("instruments_requested", 0)} instruments with data · '
            f'{elapsed:.1f}s</div>', unsafe_allow_html=True)
        _show_zone_table(view, global_table=True, all_cols=all_cols,
                         show_shadow=bool(params_applied["showShadow"]),
                         show_flags=bool(params_applied["showFlags"]), csv_name="global_pine_zone_scan.csv")
    else:
        t0 = time.time()
        with st.spinner("Scanning MTF contexts (3/5/10/15m · 1/2/4/6h · 1d) — heaviest view …"):
            mrows = cached_mtf_scan(ap["symbols"], ap["params"])
        elapsed = time.time() - t0
        if not mrows.empty:
            if symbol_query.strip():
                parts = [p.strip() for p in re.split(r"[,\s]+", symbol_query) if p.strip()]
                mrows = mrows[mrows["Instrument"].astype(str).str.contains(
                    "|".join(re.escape(p) for p in parts), case=False, na=False, regex=True)]
            if selected_sides:
                mrows = mrows[mrows["Side"].isin(selected_sides)]
            else:
                mrows = mrows.iloc[0:0]
            if selected_states:
                mrows = mrows[mrows["Zone State"].isin(selected_states)]
            mrows = mrows.sort_values("Distance %", ascending=sort_mode.startswith("Nearest"), kind="stable")
        st.markdown(f'<div class="statusbar"><b>{len(mrows):,}</b> MTF proximal lines · proximity hides lines only, '
                    f'never changes zone validity · {elapsed:.1f}s</div>', unsafe_allow_html=True)
        _show_mtf_table(mrows)
except Exception as exc:
    st.error(f"Scan failed: {type(exc).__name__}: {exc}")
    st.caption("requirements.txt और market-feed availability जाँचें, फिर Scan / Refresh दबाएँ।")

st.markdown(
    '<p class="src">Pine logic Python में port है; candles Yahoo Finance से आते हैं और TradingView exchange feed से अलग हो सकते हैं. '
    'Futures quote: Dhan live (अगर configured) वरना NSE FO EOD close. Informational scanner only — कोई order नहीं भेजा जाता.</p>',
    unsafe_allow_html=True)
