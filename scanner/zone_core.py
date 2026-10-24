"""Pine-style Supply/Demand Zone engine used by the Streamlit scanner.

This module implements the supplied zone_core.py rules, including leg-in,
base, leg-out, imbalance, engulf rejection, classification/scoring, duplicate
checks, SL/TP levels, EOD ±10% proximal filter and the proximal-touch state
machine. The supplied parity-only fields remain in PINE_DEFAULTS but are
intentionally inert, as documented in the source specification.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

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

    # Pine parity inputs retained for configuration parity; inert in the source.
    "legOutToLegInBodyMult": 1.0,
    "baseBoringMaxBodyPct": 0.55,
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

    # Scanner-only EOD gate. The app fixes both buffers at 10%.
    "eodHighBufferPct": 10.0,
    "eodLowBufferPct": 10.0,
    "useEodRange": True,
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
    timestamp: object = None
    riskPct: float = float("nan")
    score10: float = 0.0
    hasGenuineGap: bool = False
    gapToLegIn: float = 0.0
    legInVolX: float = float("nan")
    legOutVolX: float = float("nan")
    entryBarIndex: Optional[int] = None
    entryTimestamp: object = None
    breakBarIndex: Optional[int] = None
    baseColourOK: bool = False
    retestVolX: float = float("nan")
    entryStatus: str = ""
    entryPrice: float = 0.0
    hqProbabilityPct: float = float("nan")
    hqReason: str = ""
    whiteAreaOK: Optional[bool] = None
    breakReason: str = ""
    baseIndecision: bool = False
    baseDojiCount: int = 0


def _positive_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not np.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _core_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLCV names/index without changing candle values."""
    result = frame.copy()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [str(column[-1]) for column in result.columns]
    aliases = {"open": "open", "high": "high", "low": "low",
               "close": "close", "volume": "volume"}
    rename = {column: aliases[str(column).strip().lower()]
              for column in result.columns
              if str(column).strip().lower() in aliases}
    result = result.rename(columns=rename)
    result = result.loc[:, ~result.columns.duplicated(keep="first")]
    for column in ("open", "high", "low", "close"):
        if column not in result.columns:
            raise ValueError(f"ZoneEngine needs an OHLC column: {column}")
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if "volume" not in result.columns:
        result["volume"] = 0.0
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce").fillna(0.0)
    result = result.dropna(subset=["open", "high", "low", "close"])
    index = pd.DatetimeIndex(pd.to_datetime(result.index, errors="coerce"))
    valid = ~index.isna()
    result = result.loc[valid].copy()
    result.index = index[valid]
    return result.sort_index()


def get_eod_range(df: pd.DataFrame, idx: int, high_buffer_pct: float = 10.0,
                  low_buffer_pct: float = 10.0) -> tuple[float, float]:
    """Return the candidate bar's day Low−buffer and High+buffer envelope."""
    frame = _core_frame(df)
    try:
        day = frame.index[idx].date()
        same_day = frame.loc[frame.index.date == day]
        if same_day.empty:
            return (float(frame["low"].iloc[idx] * 0.9),
                    float(frame["high"].iloc[idx] * 1.1))
        return (
            float(same_day["low"].min() * (1 - float(low_buffer_pct) / 100.0)),
            float(same_day["high"].max() * (1 + float(high_buffer_pct) / 100.0)),
        )
    except Exception:
        return (float(frame["low"].iloc[idx] * 0.9),
                float(frame["high"].iloc[idx] * 1.1))


def check_white_area(df: pd.DataFrame, base_start_idx: int, base_end_idx: int,
                     leg_out_idx: int, curr_idx: int) -> bool:
    """Legacy helper retained for import compatibility; not called by engine."""
    frame = _core_frame(df)
    base_high = float(frame["high"].iloc[base_start_idx:base_end_idx + 1].max())
    base_low = float(frame["low"].iloc[base_start_idx:base_end_idx + 1].min())
    if leg_out_idx + 1 >= curr_idx:
        return True
    window = frame.iloc[leg_out_idx + 1:curr_idx]
    return not bool(((window["low"] <= base_high) & (window["high"] >= base_low)).any())


def check_leg_out_coverage(df: pd.DataFrame, leg_out_idx: int, curr_idx: int,
                           max_cover_pct: float = 90.0) -> bool:
    """Legacy helper retained for import compatibility; not called by engine."""
    frame = _core_frame(df)
    high = float(frame["high"].iloc[leg_out_idx])
    low = float(frame["low"].iloc[leg_out_idx])
    span = high - low
    if span <= 0 or leg_out_idx + 1 > curr_idx:
        return True
    window = frame.iloc[leg_out_idx + 1:curr_idx + 1]
    overlap = (np.minimum(window["high"], high) - np.maximum(window["low"], low)).clip(lower=0)
    return not bool(((overlap / span) > float(max_cover_pct) / 100.0).any())


SCAN_TRACKER: Dict[str, datetime] = {}


def should_scan_now(tf: str, force: bool = False) -> bool:
    """Legacy scan cadence helper, preserved as supplied; not used by UI."""
    now = datetime.now()
    if force:
        return True
    days = {"Monthly": 30, "Weekly": 7, "Daily": 1}
    key = tf if tf in days else f"Intraday_{tf}"
    last = SCAN_TRACKER.get(key)
    minutes = {"3M": 3, "5M": 5, "10M": 10, "15M": 15, "30M": 30,
               "75M": 75, "1H": 60, "2H": 120, "4H": 240, "6H": 360}.get(tf, 15)
    if last is None or (now - last).total_seconds() >= (
        days[tf] * 86400 if tf in days else minutes * 60
    ):
        SCAN_TRACKER[key] = now
        return True
    return False


class ZoneEngine:
    """Deterministic implementation of the supplied Pine zone rules."""

    def __init__(self, df: pd.DataFrame, **kwargs: Any):
        self.df = _core_frame(df)
        for key, default in PINE_DEFAULTS.items():
            setattr(self, key, kwargs.get(key, default))
        self.accountCapital = _positive_float(self.accountCapital, "accountCapital")
        self.atrPeriod = max(1, int(self.atrPeriod))
        self.volSmaPeriod = max(1, int(self.volSmaPeriod))
        self.minBaseCountInput = int(self.minBaseCountInput)
        self.maxBaseCountInput = int(self.maxBaseCountInput)
        self.minBaseCount = max(1, min(self.minBaseCountInput, self.maxBaseCountInput))
        self.maxBaseCount = min(self.maxBaseCountInput, HARD_MAX_BASE_COUNT)
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
        self.live_zones: List[Zone] = []
        self._prepare()

    def _rma(self, values: np.ndarray, length: int) -> np.ndarray:
        result = np.full(len(values), np.nan)
        if len(values) < length:
            return result
        result[length - 1] = np.mean(values[:length])
        for index in range(length, len(values)):
            result[index] = (values[index] - result[index - 1]) / length + result[index - 1]
        return result

    def _prepare(self) -> None:
        n = self.n
        true_range = self.h - self.l
        if n > 1:
            previous_close = self.c[:-1]
            true_range[1:] = np.maximum(
                true_range[1:],
                np.maximum(np.abs(self.h[1:] - previous_close),
                           np.abs(self.l[1:] - previous_close)),
            )
        self.atr = self._rma(true_range, self.atrPeriod)
        self.vol_sma = self.df["volume"].rolling(self.volSmaPeriod).mean().to_numpy()
        day_key = self.df.index.normalize()
        self.day_high = self.df["high"].groupby(day_key).transform("max").to_numpy()
        self.day_low = self.df["low"].groupby(day_key).transform("min").to_numpy()

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
        if span == 0:
            return 0.0
        return abs(self.c[index] - self.o[index]) / span

    def _overnight(self, index: int) -> bool:
        if index == 0:
            return False
        return bool(self.dow[index] != self.dow[index - 1]
                    or (self.time_ms[index] - self.time_ms[index - 1]) > 86400000)

    def _scan_bar(self, i: int) -> None:
        atr_now = self.atr[i]
        found = False
        for base_count in range(self.minBaseCount, self.maxBaseCount + 1):
            if found:
                break
            leg_in_offset = base_count + 1
            previous_offset = leg_in_offset + 1
            p_in, p_prev, p_out = i - leg_in_offset, i - previous_offset, i
            if p_prev < 0 or np.isnan(self.atr[p_in]):
                continue

            leg_in_tr = self._tr(p_in)
            in_low, in_high, in_close = self.l[p_in], self.h[p_in], self.c[p_in]
            in_vol = self.v[p_in]
            in_range = in_high - in_low
            in_bull, in_bear = self._bull(p_in), self._bear(p_in)
            if in_range == 0 or self._body_pct(p_in) < self.legInMinBodyPct:
                continue

            if (in_bull and self._bear(p_prev)) or (in_bear and self._bull(p_prev)):
                prev_body_high = max(self.o[p_prev], self.c[p_prev])
                prev_body_low = min(self.o[p_prev], self.c[p_prev])
                overlap = max(0.0, min(prev_body_high, in_high)
                              - max(prev_body_low, in_low))
                if overlap / in_range >= self.rejectOppositeCoverPct:
                    continue

            bull_clv = (in_close - in_low) / in_range
            bear_clv = (in_high - in_close) / in_range
            ok = True
            max_base_tr = 0.0
            max_base_high = -1.0
            min_base_low = 1_000_000_000.0
            for base_offset in range(1, base_count + 1):
                base_index = i - base_offset
                if np.isnan(self.atr[base_index]):
                    ok = False
                    break
                base_tr = self._tr(base_index)
                if base_tr > self.maxBaseAtrMult * self.atr[base_index]:
                    ok = False
                    break
                max_base_tr = max(max_base_tr, base_tr)
                max_base_high = max(max_base_high, self.h[base_index])
                min_base_low = min(min_base_low, self.l[base_index])
            if not ok or max_base_tr == 0:
                continue

            effective_mult = 1.5 if base_count == 1 else self.legInToBaseSizeMult
            if leg_in_tr < effective_mult * max_base_tr:
                continue
            if leg_in_tr < self.legInMinAtrMult * self.atr[p_in]:
                continue

            leg_out_tr = self._tr(p_out)
            out_high, out_low = self.h[p_out], self.l[p_out]
            out_close, out_open = self.c[p_out], self.o[p_out]
            out_vol = self.v[p_out]
            is_demand = self._bull(p_out)
            is_supply = self._bear(p_out)
            if not (is_demand or is_supply):
                continue

            explosive = leg_out_tr >= self.legOutTrMult * self.atr[p_out]
            wick_ok = self._wick_pct(p_out) <= self.maxWickPct
            tr_hierarchy = (leg_out_tr >= self.legOutMinTrRatio * leg_in_tr
                            and leg_in_tr > max_base_tr)
            volume_ok = out_vol > in_vol
            is_overnight = self._overnight(i)

            has_imbalance = True
            has_gap = False
            gap_size = 0.0
            if self.useImbalance:
                if is_demand:
                    has_gap = out_low > max_base_high
                    has_imbalance = has_gap or (out_close > in_high)
                    gap_size = max(0.0, out_low - max_base_high)
                else:
                    has_gap = out_high < min_base_low
                    has_imbalance = has_gap or (out_close < in_low)
                    gap_size = max(0.0, min_base_low - out_high)

            if (min(out_open, out_close) <= min_base_low
                    and max(out_open, out_close) >= max_base_high and not has_gap):
                continue

            is_rbr = in_bull and bull_clv >= self.minClvPct and is_demand
            is_dbr = in_bear and bear_clv >= self.minClvPct and is_demand
            is_dbd = in_bear and bear_clv >= self.minClvPct and is_supply
            is_rbd = in_bull and bull_clv >= self.minClvPct and is_supply
            valid_pattern = is_rbr or is_dbr or is_dbd or is_rbd
            if not (valid_pattern and explosive and wick_ok and tr_hierarchy
                    and volume_ok and has_imbalance):
                continue

            score = 0
            if base_count == 1:
                score += 15
            if leg_in_tr >= self.hqLegInAtrMult * self.atr[p_in]:
                score += 10
            if leg_out_tr >= self.hqLegOutTrMult * leg_in_tr:
                score += 15
            if (leg_in_tr >= 2.0 * max_base_tr
                    and leg_out_tr >= 2.0 * leg_in_tr):
                score += 15
            if out_vol > self.vol_sma[p_out]:
                score += 10
            out_range = out_high - out_low
            if is_demand:
                position = (out_close - out_low) / out_range if out_range > 0 else 0
                own_body = self._body_pct(p_out)
                if is_dbr:
                    if position >= 0.80 or own_body >= self.legOutBodyHeavyPct:
                        score += 15
                elif position >= 0.80:
                    score += 15
            else:
                position = (out_high - out_close) / out_range if out_range > 0 else 0
                if position >= 0.80:
                    score += 15

            opposite_base = False
            for base_offset in range(1, base_count + 1):
                if ((is_demand and self._bear(i - base_offset))
                        or (is_supply and self._bull(i - base_offset))):
                    opposite_base = True
                    break
            if opposite_base:
                score += 10
            score += 10
            if has_gap:
                score += self.genuineGapBonus
            if is_overnight and has_gap:
                score += self.overnightGapBonus
            if score < self.minValidScore:
                continue

            proximal = max_base_high if is_demand else min_base_low
            distal = min_base_low if is_demand else max_base_high
            if self.useEodRange:
                eod_high = self.day_high[i] * (1 + self.eodHighBufferPct / 100.0)
                eod_low = self.day_low[i] * (1 - self.eodLowBufferPct / 100.0)
                if not (eod_low <= proximal <= eod_high):
                    continue

            is_hq = score >= self.hqScoreThreshold
            found = True
            sl = (distal - self.slBufferAtr * atr_now if is_demand
                  else distal + self.slBufferAtr * atr_now)
            risk = abs(proximal - sl)
            tp = (proximal + risk * self.targetRR if is_demand
                  else proximal - risk * self.targetRR)
            if is_demand:
                mid_level = out_high - self.testedLegOutRetracePct * (out_high - out_low)
            else:
                mid_level = out_low + self.testedLegOutRetracePct * (out_high - out_low)

            duplicate = False
            checked = 0
            for prior in reversed(self.live_zones):
                if prior.isDemand == is_demand and abs(prior.proxVal - proximal) < atr_now * 0.25:
                    duplicate = True
                    break
                checked += 1
                if checked >= 11:
                    break
            if duplicate:
                continue

            pattern = ("RBR" if is_rbr else "DBR" if is_dbr
                       else "DBD" if is_dbd else "RBD")
            category = "Continuation" if (is_rbr or is_dbd) else "Reversal"
            border = "green" if is_demand else "red"
            fill = ("green", 0.15) if is_demand else ("red", 0.15)
            volume_sma_in, volume_sma_out = self.vol_sma[p_in], self.vol_sma[p_out]
            zone = Zone(
                proxVal=proximal, distVal=distal, slVal=sl, tpVal=tp,
                isDemand=is_demand, isHQ=is_hq, densityScore=score,
                patternType=pattern, zoneCategory=category, state="Fresh",
                touchCount=0, startBarIndex=i - base_count,
                createdBarIndex=i, baseCount=base_count,
                legOutHigh=out_high, legOutLow=out_low,
                legOutMidLevel=mid_level, isOvernight=is_overnight,
                legInTR=leg_in_tr, legOutTR=leg_out_tr,
                zoneBox=Box(i - base_count - 1, proximal, i + 15,
                            distal, border, fill),
                timestamp=self.df.index[i],
                riskPct=risk / proximal * 100.0 if proximal else float("nan"),
                score10=round(score / 10.0, 1), hasGenuineGap=has_gap,
                gapToLegIn=gap_size,
                legInVolX=(in_vol / volume_sma_in
                           if volume_sma_in and not np.isnan(volume_sma_in)
                           else float("nan")),
                legOutVolX=(out_vol / volume_sma_out
                            if volume_sma_out and not np.isnan(volume_sma_out)
                            else float("nan")),
            )
            self.active_zones.append(zone)
            self.live_zones.append(zone)

    def _update_states(self, i: int) -> None:
        """Fresh→Tested on proximal touch; either state breaks at distal."""
        if not self.live_zones:
            return
        low, high = self.l[i], self.h[i]
        for position in range(len(self.live_zones) - 1, -1, -1):
            zone = self.live_zones[position]
            if zone.state == "Fresh":
                if zone.isDemand:
                    if low <= zone.distVal:
                        zone.state = "Broken"
                        zone.breakReason = "distal_break_before_test"
                    elif low <= zone.proxVal:
                        zone.state = "Tested"
                        zone.touchCount += 1
                        if zone.entryBarIndex is None:
                            zone.entryBarIndex = i
                            zone.entryTimestamp = self.df.index[i]
                else:
                    if high >= zone.distVal:
                        zone.state = "Broken"
                        zone.breakReason = "distal_break_before_test"
                    elif high >= zone.proxVal:
                        zone.state = "Tested"
                        zone.touchCount += 1
                        if zone.entryBarIndex is None:
                            zone.entryBarIndex = i
                            zone.entryTimestamp = self.df.index[i]
            elif zone.state == "Tested":
                if zone.isDemand:
                    if low <= zone.distVal:
                        zone.state = "Broken"
                        zone.breakReason = "distal_break"
                    elif low <= zone.proxVal:
                        zone.touchCount += 1
                else:
                    if high >= zone.distVal:
                        zone.state = "Broken"
                        zone.breakReason = "distal_break"
                    elif high >= zone.proxVal:
                        zone.touchCount += 1

            if zone.state == "Broken":
                zone.breakBarIndex = i
                zone.zoneBox.set_bgcolor(("gray", 0.05))
                zone.zoneBox.set_border_color(("gray", 0.20))
                self.live_zones.pop(position)
            else:
                zone.zoneBox.set_right(i + 15)

    def run(self) -> List[Zone]:
        min_bar = max(self.atrPeriod, self.maxBaseCount + 3, 11)
        for i in range(self.n):
            if i >= min_bar and not np.isnan(self.atr[i]):
                self._scan_bar(i)
            self._update_states(i)
        return self.active_zones


def settings(accountCapital: Optional[float] = None, **overrides: Any) -> Dict[str, Any]:
    result = dict(PINE_DEFAULTS)
    if accountCapital is not None:
        overrides["accountCapital"] = accountCapital
    for key, value in overrides.items():
        if key in PINE_DEFAULTS:
            result[key] = value
    result["accountCapital"] = _positive_float(result["accountCapital"], "accountCapital")
    return result


def scan_zones(df: pd.DataFrame, params: Optional[Dict[str, Any]] = None,
               accountCapital: Optional[float] = None,
               tf: Optional[str] = None) -> List[Zone]:
    incoming = dict(params or {})
    if accountCapital is not None:
        incoming["accountCapital"] = accountCapital
    config = settings(**incoming)
    return ZoneEngine(df, **config).run()


def latest_active_zones(zones: List[Zone]) -> List[Zone]:
    return [zone for zone in zones if zone.state in ("Fresh", "Tested")]


def high_quality_zones(zones: List[Zone]) -> List[Zone]:
    return [zone for zone in zones if zone.isHQ]
