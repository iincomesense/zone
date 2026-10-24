"""Zone-table construction for NSE stock futures and global instruments."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import numpy as np
import pandas as pd

from .universe import TIMEFRAMES
from .zones import IST, normalize_ohlcv, timeframe_candles
from .zone_core import (
    PINE_DEFAULTS, get_eod_range, latest_active_zones, scan_zones as run_zone_core,
    settings as zone_settings,
)

TV_INTERVAL = {
    "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
    "75m": "75", "1h": "60", "2h": "120", "4h": "240", "6h": "360",
    "1d": "D", "1w": "W", "1M": "M",
}
EOD_HIGH_BUFFER_PCT = 10.0
EOD_LOW_BUFFER_PCT = 10.0


def tradingview_url(tv_symbol: str, timeframe: str, *, future: bool = False) -> str:
    """Build a TradingView link; NSE futures use the 1! front-month symbol."""
    symbol = str(tv_symbol).strip()
    if future and ":" not in symbol:
        symbol = f"NSE:{symbol}"
    if future and not symbol.endswith("1!"):
        symbol += "1!"
    if ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        continuous = ticker.endswith("1!")
        ticker_core = ticker[:-2] if continuous else ticker
        encoded = f"{quote(exchange, safe='')}%3A{quote(ticker_core, safe='')}"
        if continuous:
            encoded += "1!"
    else:
        encoded = quote(symbol, safe="")
    interval = TV_INTERVAL.get(timeframe, "15")
    return f"https://in.tradingview.com/chart/?symbol={encoded}&interval={interval}"


def latest_ltp(data: dict[str, pd.DataFrame], *, market: str = "NSE") -> tuple[float | None, str, Any]:
    """Latest observed close in preferred-source order; return value/source/asof."""
    for layer, source in (("minute", "Yahoo 1m"), ("hour", "Yahoo 60m"),
                          ("daily", "Yahoo EOD")):
        frame = normalize_ohlcv(data.get(layer))
        if frame.empty:
            continue
        try:
            value = float(frame["Close"].iloc[-1])
            if np.isfinite(value) and value > 0:
                return value, source, frame.index[-1]
        except (TypeError, ValueError):
            continue
    return None, "—", None


def _safe_price(value: Any) -> float | None:
    try:
        result = float(value)
        return result if np.isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def _core_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert the app's canonical Title-case OHLCV frame to core format."""
    clean = normalize_ohlcv(frame)
    clean = clean.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    return clean.loc[:, ["open", "high", "low", "close", "volume"]]


def _zone_metrics(zone: Any, ltp: float | None) -> dict[str, Any]:
    proximal = float(zone.proxVal)
    distal = float(zone.distVal)
    low, high = min(proximal, distal), max(proximal, distal)
    if ltp is None:
        distance_pct = np.nan
    elif low <= ltp <= high:
        distance_pct = 0.0
    else:
        distance_pct = abs(ltp - proximal) / max(abs(proximal), 1e-9) * 100.0
    return {
        "Zone": "Demand" if zone.isDemand else "Supply",
        "Zone Low": low,
        "Zone High": high,
        "Proximal": proximal,
        "Distal": distal,
        "SL": float(zone.slVal),
        "TP": float(zone.tpVal),
        "Zone State": zone.state,
        "Nearest Gap %": float(distance_pct),
        "Pattern": zone.patternType,
        "Category": zone.zoneCategory,
        "Density Score": int(zone.densityScore),
        "Score / 10": float(zone.score10),
        "HQ": bool(zone.isHQ),
        "Touch Count": int(zone.touchCount),
        "Base Count": int(zone.baseCount),
        "Leg-in TR": float(zone.legInTR),
        "Leg-out TR": float(zone.legOutTR),
        "Leg-out High": float(zone.legOutHigh),
        "Leg-out Low": float(zone.legOutLow),
        "Leg-out Mid": float(zone.legOutMidLevel),
        "Genuine Gap": bool(zone.hasGenuineGap),
        "Gap to Leg-in": float(zone.gapToLegIn),
        "Leg-in Volume ×": float(zone.legInVolX),
        "Leg-out Volume ×": float(zone.legOutVolX),
        "Overnight": bool(zone.isOvernight),
        "Formed": zone.timestamp,
        "Entry Time": zone.entryTimestamp,
        "Break Reason": zone.breakReason,
    }


def _fixed_core_params(zone_params: dict[str, Any] | None) -> dict[str, Any]:
    params = zone_settings(**(zone_params or {}))
    # User requirement: scanner always uses the EOD low−10% / high+10% gate.
    params["useEodRange"] = True
    params["eodHighBufferPct"] = EOD_HIGH_BUFFER_PCT
    params["eodLowBufferPct"] = EOD_LOW_BUFFER_PCT
    return params


def _scan_one_series(data: dict[str, pd.DataFrame], timeframe: str, *,
                     market: str, ltp: float | None,
                     params: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    candles = timeframe_candles(data, timeframe, market=market, now=now)
    if candles.empty:
        return []
    core_candles = _core_frame(candles)
    zones = latest_active_zones(run_zone_core(core_candles, params=params, tf=timeframe))
    rows: list[dict[str, Any]] = []
    for zone in zones:
        metrics = _zone_metrics(zone, ltp)
        try:
            eod_low, eod_high = get_eod_range(
                core_candles, zone.createdBarIndex,
                high_buffer_pct=EOD_HIGH_BUFFER_PCT,
                low_buffer_pct=EOD_LOW_BUFFER_PCT,
            )
            eod_candle = core_candles.index[zone.createdBarIndex].date()
        except Exception:
            eod_low, eod_high, eod_candle = np.nan, np.nan, None
        metrics.update({
            "EOD Range Low": eod_low,
            "EOD Range High": eod_high,
            "EOD Candle": eod_candle,
        })
        rows.append(metrics)
    return rows


def _equity_row(symbol: str, timeframe: str, zone: dict[str, Any], *,
                ltp: float | None, ltp_source: str, ltp_asof: Any,
                future: dict[str, Any] | None = None) -> dict[str, Any]:
    future = future or {}
    future_ltp = _safe_price(future.get("ltp"))
    delta = ltp - future_ltp if ltp is not None and future_ltp is not None else None
    if delta is None:
        premium_discount = "—"
    elif delta < 0:
        premium_discount = "Future Premium"
    elif delta > 0:
        premium_discount = "Future Discount"
    else:
        premium_discount = "At Par"
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
        "Premium / Discount": premium_discount,
        "TF": timeframe,
        **zone,
        "Equity Quote As Of": ltp_asof,
    }


def scan_equity_zones(
    symbol_data: dict[str, dict[str, pd.DataFrame]],
    symbols: list[str] | tuple[str, ...],
    timeframes: list[str] | tuple[str, ...] = TIMEFRAMES,
    range_pct: float = 10.0,
    future_quotes: dict[str, dict[str, Any]] | None = None,
    equity_live_quotes: dict[str, float] | None = None,
    *, zone_params: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Scan the Pine-style engine across all selected NSE symbol/timeframe pairs.

    ``range_pct`` remains for older callers but is deliberately ignored: the
    scanner's EOD buffers are fixed to ±10%, per the requested specification.
    """
    _ = range_pct
    future_quotes = future_quotes or {}
    equity_live_quotes = equity_live_quotes or {}
    params = _fixed_core_params(zone_params)
    rows: list[dict[str, Any]] = []
    coverage = {"symbols_requested": len(symbols), "symbols_with_daily": 0,
                "symbols_with_minute": 0, "symbols_with_zones": 0,
                "zone_rows": 0}
    timeframe_order = {timeframe: index for index, timeframe in enumerate(TIMEFRAMES)}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        data = symbol_data.get(symbol) or {}
        daily = normalize_ohlcv(data.get("daily"))
        minute = normalize_ohlcv(data.get("minute"))
        if not daily.empty:
            coverage["symbols_with_daily"] += 1
        if not minute.empty:
            coverage["symbols_with_minute"] += 1
        ltp, ltp_source, ltp_asof = latest_ltp(data)
        override = _safe_price(equity_live_quotes.get(symbol))
        if override is not None:
            ltp, ltp_source, ltp_asof = override, "Dhan LIVE", datetime.now(IST)
        symbol_has_zone = False
        future = future_quotes.get(symbol)
        for timeframe in timeframes:
            for zone in _scan_one_series(data, timeframe, market="NSE", ltp=ltp,
                                         params=params, now=now):
                rows.append(_equity_row(
                    symbol, timeframe, zone, ltp=ltp, ltp_source=ltp_source,
                    ltp_asof=ltp_asof, future=future,
                ))
                symbol_has_zone = True
        if symbol_has_zone:
            coverage["symbols_with_zones"] += 1
    coverage["zone_rows"] = len(rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["_tf_order"] = frame["TF"].map(timeframe_order).fillna(999)
        frame = frame.sort_values(["Nearest Gap %", "_tf_order", "Density Score"],
                                  ascending=[True, True, False], kind="stable")
        frame = frame.drop(columns=["_tf_order"]).reset_index(drop=True)
    return frame, coverage


def scan_global_zones(
    global_data: dict[str, dict[str, pd.DataFrame]],
    instruments: list[dict[str, str]] | tuple[dict[str, str], ...],
    timeframes: list[str] | tuple[str, ...] = TIMEFRAMES,
    range_pct: float = 10.0,
    *, zone_params: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Zone-only scan for configured global indices, FX and commodities."""
    _ = range_pct
    params = _fixed_core_params(zone_params)
    rows: list[dict[str, Any]] = []
    coverage = {"instruments_requested": len(instruments), "instruments_with_data": 0,
                "zone_rows": 0}
    timeframe_order = {timeframe: index for index, timeframe in enumerate(TIMEFRAMES)}
    for instrument in instruments:
        key = str(instrument["key"])
        tv_symbol = str(instrument["tv"])
        data = global_data.get(key) or {}
        if normalize_ohlcv(data.get("daily")).empty:
            continue
        coverage["instruments_with_data"] += 1
        ltp, ltp_source, ltp_asof = latest_ltp(data, market="GLOBAL")
        for timeframe in timeframes:
            for zone in _scan_one_series(data, timeframe, market="GLOBAL", ltp=ltp,
                                         params=params, now=now):
                rows.append({
                    "Global Instrument / TV Chart": tradingview_url(tv_symbol, timeframe),
                    "Instrument": instrument.get("label", key),
                    "LTP": ltp,
                    "LTP Source": ltp_source,
                    "TF": timeframe,
                    **zone,
                    "Quote As Of": ltp_asof,
                })
    coverage["zone_rows"] = len(rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["_tf_order"] = frame["TF"].map(timeframe_order).fillna(999)
        frame = frame.sort_values(["Nearest Gap %", "_tf_order", "Density Score"],
                                  ascending=[True, True, False], kind="stable")
        frame = frame.drop(columns=["_tf_order"]).reset_index(drop=True)
    return frame, coverage
