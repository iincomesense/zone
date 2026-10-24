"""Zone-table construction for NSE stock futures and global instruments."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import numpy as np
import pandas as pd

from .universe import TIMEFRAMES
from .zones import detect_zones, eod_price_range, normalize_ohlcv, timeframe_candles, IST

TV_INTERVAL = {
    "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
    "75m": "75", "1h": "60", "2h": "120", "4h": "240", "6h": "360",
    "1d": "D", "1w": "W", "1M": "M",
}


def tradingview_url(tv_symbol: str, timeframe: str, *, future: bool = False) -> str:
    """Build a TradingView link; NSE futures use the 1! front-month symbol."""
    symbol = str(tv_symbol).strip()
    if future and ":" not in symbol:
        symbol = f"NSE:{symbol}"
    if future and not symbol.endswith("1!"):
        symbol += "1!"
    if ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        # TradingView recognises a literal 1! for continuous front-month futures.
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
        row = frame.iloc[-1]
        try:
            value = float(row["Close"])
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


def _zone_row(symbol: str, timeframe: str, zone: dict[str, Any],
              *, ltp: float | None, ltp_source: str, ltp_asof: Any,
              range_info: tuple[float, float, Any] | None,
              future: dict[str, Any] | None = None,
              now: datetime | None = None) -> dict[str, Any]:
    future = future or {}
    future_ltp = _safe_price(future.get("ltp"))
    delta = (ltp - future_ltp) if ltp is not None and future_ltp is not None else None
    if delta is None:
        premium_discount = "—"
    elif delta < 0:
        premium_discount = "Future Premium"
    elif delta > 0:
        premium_discount = "Future Discount"
    else:
        premium_discount = "At Par"
    low_range, high_range, range_asof = range_info or (None, None, None)
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
        "Zone": zone.get("Zone"),
        "Zone Low": zone.get("Zone Low"),
        "Zone High": zone.get("Zone High"),
        "Proximal": zone.get("Proximal"),
        "Distal": zone.get("Distal"),
        "Zone State": zone.get("Zone State"),
        "Nearest Gap %": zone.get("Distance %"),
        "Strength (ATR)": zone.get("Strength (ATR)"),
        "Formed": zone.get("Formed"),
        "Confirmed": zone.get("Confirmed"),
        "EOD Range Low": low_range,
        "EOD Range High": high_range,
        "EOD Candle": range_asof,
        "Equity Quote As Of": ltp_asof,
    }


def scan_equity_zones(
    symbol_data: dict[str, dict[str, pd.DataFrame]],
    symbols: list[str] | tuple[str, ...],
    timeframes: list[str] | tuple[str, ...] = TIMEFRAMES,
    range_pct: float = 10.0,
    future_quotes: dict[str, dict[str, Any]] | None = None,
    equity_live_quotes: dict[str, float] | None = None,
    *, legout_mult: float = 1.5, max_wick_pct: float = 50.0,
    tight_base: bool = True, max_zones_per_tf: int = 3,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Scan every requested NSE stock/timeframe and return only valid zones."""
    future_quotes = future_quotes or {}
    equity_live_quotes = equity_live_quotes or {}
    rows: list[dict[str, Any]] = []
    coverage = {"symbols_requested": len(symbols), "symbols_with_daily": 0,
                "symbols_with_minute": 0, "symbols_with_zones": 0,
                "zone_rows": 0}
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        data = symbol_data.get(symbol) or {}
        daily = normalize_ohlcv(data.get("daily"))
        if not daily.empty:
            coverage["symbols_with_daily"] += 1
        minute = normalize_ohlcv(data.get("minute"))
        if not minute.empty:
            coverage["symbols_with_minute"] += 1
        ltp, ltp_source, ltp_asof = latest_ltp(data)
        override = _safe_price(equity_live_quotes.get(symbol))
        if override is not None:
            ltp, ltp_source, ltp_asof = override, "Dhan LIVE", datetime.now(IST)
        if ltp is None:
            continue
        range_info = eod_price_range(daily, range_pct, market="NSE", now=now)
        if range_info is None:
            continue
        range_low, range_high, _ = range_info
        symbol_has_zone = False
        for timeframe in timeframes:
            candles = timeframe_candles(data, timeframe, market="NSE", now=now)
            if candles.empty:
                continue
            zones = detect_zones(
                candles, spot=ltp, range_low=range_low, range_high=range_high,
                legout_mult=legout_mult, max_wick_pct=max_wick_pct,
                tight_base=tight_base, max_zones=max_zones_per_tf,
            )
            for zone in zones:
                rows.append(_zone_row(
                    symbol, timeframe, zone, ltp=ltp, ltp_source=ltp_source,
                    ltp_asof=ltp_asof, range_info=range_info,
                    future=future_quotes.get(symbol), now=now,
                ))
                symbol_has_zone = True
        if symbol_has_zone:
            coverage["symbols_with_zones"] += 1
    coverage["zone_rows"] = len(rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["_tf_order"] = frame["TF"].map(tf_order).fillna(999)
        frame = frame.sort_values(["Nearest Gap %", "_tf_order", "Strength (ATR)"],
                                  ascending=[True, True, False], kind="stable")
        frame = frame.drop(columns=["_tf_order"]).reset_index(drop=True)
    return frame, coverage


def scan_global_zones(
    global_data: dict[str, dict[str, pd.DataFrame]],
    instruments: list[dict[str, str]] | tuple[dict[str, str], ...],
    timeframes: list[str] | tuple[str, ...] = TIMEFRAMES,
    range_pct: float = 10.0,
    *, legout_mult: float = 1.5, max_wick_pct: float = 50.0,
    tight_base: bool = True, max_zones_per_tf: int = 2,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Zone-only scan for the configured global indices, FX and commodities."""
    rows: list[dict[str, Any]] = []
    coverage = {"instruments_requested": len(instruments), "instruments_with_data": 0,
                "zone_rows": 0}
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}
    for instrument in instruments:
        key = str(instrument["key"])
        tv_symbol = str(instrument["tv"])
        data = global_data.get(key) or {}
        daily = normalize_ohlcv(data.get("daily"))
        if daily.empty:
            continue
        coverage["instruments_with_data"] += 1
        ltp, ltp_source, ltp_asof = latest_ltp(data, market="GLOBAL")
        if ltp is None:
            continue
        range_info = eod_price_range(daily, range_pct, market="GLOBAL", now=now)
        if range_info is None:
            continue
        range_low, range_high, range_asof = range_info
        for timeframe in timeframes:
            candles = timeframe_candles(data, timeframe, market="GLOBAL", now=now)
            if candles.empty:
                continue
            zones = detect_zones(
                candles, spot=ltp, range_low=range_low, range_high=range_high,
                legout_mult=legout_mult, max_wick_pct=max_wick_pct,
                tight_base=tight_base, max_zones=max_zones_per_tf,
            )
            for zone in zones:
                rows.append({
                    "Global Instrument / TV Chart": tradingview_url(tv_symbol, timeframe),
                    "Instrument": instrument.get("label", key),
                    "LTP": ltp,
                    "LTP Source": ltp_source,
                    "TF": timeframe,
                    "Zone": zone.get("Zone"),
                    "Zone Low": zone.get("Zone Low"),
                    "Zone High": zone.get("Zone High"),
                    "Proximal": zone.get("Proximal"),
                    "Distal": zone.get("Distal"),
                    "Zone State": zone.get("Zone State"),
                    "Nearest Gap %": zone.get("Distance %"),
                    "Strength (ATR)": zone.get("Strength (ATR)"),
                    "Formed": zone.get("Formed"),
                    "EOD Range Low": range_low,
                    "EOD Range High": range_high,
                    "EOD Candle": range_asof,
                    "Quote As Of": ltp_asof,
                })
    coverage["zone_rows"] = len(rows)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["_tf_order"] = frame["TF"].map(tf_order).fillna(999)
        frame = frame.sort_values(["Nearest Gap %", "_tf_order", "Strength (ATR)"],
                                  ascending=[True, True, False], kind="stable")
        frame = frame.drop(columns=["_tf_order"]).reset_index(drop=True)
    return frame, coverage
