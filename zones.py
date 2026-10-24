"""Price-action supply/demand zones and timeframe aggregation.

The source application was not present in the workspace, so this is an
explicit, testable reconstruction using compact-base, ATR leg-out, wick,
Fresh/Tested, and completed-EOD range rules. Prices are never fabricated: an
unavailable candle series returns no zones. Exact parity with a hidden source
engine cannot be claimed without its code.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
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


def last_completed_daily(daily: pd.DataFrame | None, market: str = "NSE",
                         now: datetime | None = None) -> pd.Series | None:
    """Return the latest completed daily candle used for the EOD range.

    During the NSE session, today's partial daily candle is excluded. After
    15:40 IST it is accepted as the EOD candle if the feed has published it.
    """
    df = normalize_ohlcv(daily)
    if df.empty:
        return None
    now_ist = now.astimezone(IST) if now and now.tzinfo else (now.replace(tzinfo=IST) if now else datetime.now(IST))
    today = now_ist.date()
    dates = df.index.date
    if market.upper() == "NSE" and now_ist.time() < dtime(15, 40):
        df = df.loc[np.asarray(dates) < today]
    elif market.upper() != "NSE" and now_ist.hour < 6:
        # Avoid consuming a still-forming overseas session candle overnight.
        df = df.loc[np.asarray(dates) < today]
    if df.empty:
        # Very short history: keep the only available candle rather than making
        # up an EOD reference.
        df = normalize_ohlcv(daily)
    return df.iloc[-1]


def eod_price_range(daily: pd.DataFrame | None, range_pct: float,
                    market: str = "NSE", now: datetime | None = None
                    ) -> tuple[float, float, pd.Timestamp] | None:
    """Low = completed EOD low minus %, High = EOD high plus %."""
    candle = last_completed_daily(daily, market=market, now=now)
    if candle is None:
        return None
    try:
        low = float(candle["Low"])
        high = float(candle["High"])
        pct = max(0.0, float(range_pct)) / 100.0
        if not np.isfinite(low) or not np.isfinite(high) or low <= 0 or high < low:
            return None
        return low * (1.0 - pct), high * (1.0 + pct), candle.name
    except (TypeError, ValueError, KeyError):
        return None


def detect_zones(frame: pd.DataFrame, *, spot: float | None = None,
                 range_low: float | None = None, range_high: float | None = None,
                 legout_mult: float = 1.5, max_wick_pct: float = 50.0,
                 tight_base: bool = True, max_zones: int = 3) -> list[dict[str, Any]]:
    """Detect compact base + decisive leg-out supply/demand zones.

    Rules:
      * A base is 1–3 consecutive small-range / small-body candles.
      * A leg-out must close beyond the base by ``legout_mult × ATR(14)``.
      * The combined wick share of each base candle cannot exceed
        ``max_wick_pct``; ``tight_base`` applies the stricter ATR width cap.
      * A post-confirmation close through the distal edge invalidates the zone.
      * Any later overlap marks it Tested; otherwise it is Fresh.
      * When an EOD range is supplied, only zones intersecting that range pass.

    This is a compact, deterministic price-action implementation; it does not
    use indicators, news, AI scores, or order execution.
    """
    df = normalize_ohlcv(frame)
    if len(df) < 20:
        return []
    df = df.tail(1200)
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    spans = np.maximum(h - l, 1e-9)
    tr = np.maximum.reduce((h - l, np.abs(h - np.r_[c[0], c[:-1]]),
                            np.abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr, index=df.index).rolling(14, min_periods=5).mean().to_numpy()
    valid_atr = atr[np.isfinite(atr) & (atr > 0)]
    fallback_atr = float(np.nanmedian(valid_atr)) if valid_atr.size else float(np.nanmedian(spans))
    atr = np.where(np.isfinite(atr) & (atr > 0), atr, fallback_atr)
    bodies = np.abs(c - o)
    wick_pct = np.clip((spans - bodies) / spans * 100.0, 0.0, 100.0)
    cap = 0.78 if tight_base else 1.05
    base_mask = (spans <= np.maximum(atr * cap, fallback_atr * 0.12))
    base_mask &= bodies <= spans * 0.62
    base_mask &= wick_pct <= max(0.0, float(max_wick_pct))

    candidates: list[dict[str, Any]] = []
    n = len(df)
    i = 0
    while i < n - 4:
        if not base_mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and base_mask[j + 1] and (j - i + 1) < 3:
            j += 1
        base_len = j - i + 1
        base_high = float(np.max(h[i:j + 1]))
        base_low = float(np.min(l[i:j + 1]))
        local_atr = max(float(np.nanmean(atr[i:j + 1])), 1e-9)
        threshold = max(local_atr * max(0.1, float(legout_mult)), fallback_atr * 0.12)
        confirmation = None
        side = ""
        strength = 0.0
        for k in range(j + 1, min(j + 4, n)):
            up = float(c[k] - base_high)
            down = float(base_low - c[k])
            up_strength = up / local_atr
            down_strength = down / local_atr
            if up >= threshold and up_strength >= down_strength:
                confirmation, side, strength = k, "Demand", up_strength
                break
            if down >= threshold:
                confirmation, side, strength = k, "Supply", down_strength
                break
        i = j + 1
        if confirmation is None or base_high <= base_low:
            continue
        zone_low, zone_high = base_low, base_high
        if range_low is not None and range_high is not None:
            if zone_high < float(range_low) or zone_low > float(range_high):
                continue
        later = slice(confirmation + 1, n)
        later_close = c[later]
        later_high = h[later]
        later_low = l[later]
        if side == "Demand":
            invalidated = bool(np.any(later_close < zone_low))
            proximal, distal = zone_high, zone_low
        else:
            invalidated = bool(np.any(later_close > zone_high))
            proximal, distal = zone_low, zone_high
        if invalidated:
            continue
        tested = bool(np.any((later_low <= zone_high) & (later_high >= zone_low)))
        state = "Tested" if tested else "Fresh"
        px = float(spot) if spot is not None and np.isfinite(float(spot)) else float(c[-1])
        gap_pct = abs(px - proximal) / max(abs(proximal), 1e-9) * 100.0
        if zone_low <= px <= zone_high:
            gap_pct = 0.0
        candidates.append({
            "Zone": side,
            "Zone Low": zone_low,
            "Zone High": zone_high,
            "Proximal": float(proximal),
            "Distal": float(distal),
            "Zone State": state,
            "Distance %": float(gap_pct),
            "Formed": df.index[i - base_len],
            "Confirmed": df.index[confirmation],
            "Strength (ATR)": float(strength),
        })

    # Collapse heavily overlapping same-side zones; retain the most recent
    # candidate first. This keeps the all-symbol, all-timeframe table readable.
    candidates.sort(key=lambda z: (z["Confirmed"], z["Strength (ATR)"]), reverse=True)
    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        duplicate = False
        for prior in kept:
            if prior["Zone"] != candidate["Zone"]:
                continue
            overlap = max(0.0, min(prior["Zone High"], candidate["Zone High"])
                          - max(prior["Zone Low"], candidate["Zone Low"]))
            width = max(1e-9, min(prior["Zone High"] - prior["Zone Low"],
                                   candidate["Zone High"] - candidate["Zone Low"]))
            if overlap / width >= 0.7:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
        if len(kept) >= max(1, int(max_zones)):
            break
    kept.sort(key=lambda z: (z["Distance %"], -z["Strength (ATR)"], z["Confirmed"]))
    return kept
