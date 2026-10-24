"""OHLCV normalization and candle aggregation for zone_core.py.

Zone detection, EOD gating, scoring and state transitions live in
``scanner.zone_core``; this module only prepares requested timeframe candles.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
OHLCV = ("Open", "High", "Low", "Close", "Volume")


def normalize_ohlcv(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return a sorted OHLCV frame with numeric columns and an IST index."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(OHLCV))
    df = frame.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # A single-ticker yfinance response can still have a MultiIndex.
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
    # Yahoo may expose both Close and Adj Close; after alias normalization these
    # can collide. Keep the first (unadjusted Close) column only.
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


def _aggregate(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=list(OHLCV))
    result = frame.resample(
        "1min", origin="start_day", label="left", closed="left"
    ).agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    })
    return result


def _session_filter(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    df = normalize_ohlcv(frame)
    if df.empty:
        return df
    # Indian cash market hours. Global instruments use their own Yahoo exchange
    # timestamps, so only weekdays are filtered for them.
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
            # This is only an incomplete-bar guard. Global market sessions vary;
            # the source candle timestamp remains the exchange-local timestamp.
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
    if timeframe in {"3m", "5m", "10m", "15m", "30m", "75m"}:
        source = data.get("minute")
        if source is None or source.empty:
            # No synthetic minute bars: return empty rather than implying a scan.
            return pd.DataFrame(columns=list(OHLCV))
        return _resample_intraday(source, int(timeframe[:-1]), market, now=now)
    if timeframe in {"1h", "2h", "4h", "6h"}:
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


