from datetime import datetime

import numpy as np
import pandas as pd

from scanner.scan import tradingview_url
from scanner.zones import detect_zones, eod_price_range


def _demand_frame(touch: bool = False, broken: bool = False) -> pd.DataFrame:
    index = pd.date_range("2026-01-01 09:15", periods=28, freq="5min", tz="Asia/Kolkata")
    rows = []
    # Pre-base candles have normal ranges and low volatility.
    for _ in range(18):
        rows.append((99.2, 100.0, 99.0, 99.6, 1000))
    # Two-candle compact base around 99.5-100.0.
    rows.extend([(99.70, 100.00, 99.50, 99.75, 800),
                 (99.72, 100.00, 99.50, 99.76, 750)])
    # Decisive bullish leg-out.
    rows.append((99.90, 104.0, 99.80, 103.5, 5000))
    # Continue higher without retesting the demand zone.
    for _ in range(5):
        rows.append((103.5, 105.0, 103.0, 104.5, 1800))
    # Optional non-breaking retest or distal close-through.
    if touch:
        rows.append((101.0, 101.3, 99.7, 100.8, 2500))
    if broken:
        rows.append((100.0, 100.2, 98.8, 99.0, 3500))
    rows = rows[:len(index)]
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=index[:len(rows)])


def test_demand_zone_is_fresh_until_price_retests():
    zones = detect_zones(_demand_frame(), spot=105.0, range_low=90, range_high=110,
                         legout_mult=1.5, max_wick_pct=95, tight_base=True)
    assert zones
    assert zones[0]["Zone"] == "Demand"
    assert zones[0]["Zone State"] == "Fresh"
    assert zones[0]["Zone Low"] < zones[0]["Zone High"]


def test_retested_zone_is_marked_tested():
    zones = detect_zones(_demand_frame(touch=True), spot=101.0,
                         range_low=90, range_high=110, legout_mult=1.5,
                         max_wick_pct=95, tight_base=True)
    assert zones
    assert zones[0]["Zone State"] == "Tested"


def test_close_through_distal_invalidates_zone():
    zones = detect_zones(_demand_frame(broken=True), spot=99.0,
                         range_low=90, range_high=110, legout_mult=1.5,
                         max_wick_pct=95, tight_base=True)
    assert zones == []


def test_eod_range_uses_low_minus_and_high_plus_percent():
    daily = pd.DataFrame(
        {"Open": [99, 100], "High": [105, 110], "Low": [95, 98],
         "Close": [101, 106], "Volume": [100, 100]},
        index=pd.to_datetime(["2026-10-22", "2026-10-23"]).tz_localize("Asia/Kolkata"),
    )
    # Simulate market hours on the following day; the prior EOD candle is used.
    now = datetime.fromisoformat("2026-10-26T11:00:00+05:30")
    result = eod_price_range(daily, 10, market="NSE", now=now)
    assert result is not None
    low, high, _ = result
    assert round(low, 2) == 88.20
    assert round(high, 2) == 121.00


def test_tradingview_links_are_built_for_cash_and_front_future():
    cash = tradingview_url("NSE:M&M", "15m")
    future = tradingview_url("NSE:M&M", "15m", future=True)
    assert "NSE%3AM%26M" in cash
    assert "NSE%3AM%26M1!" in future
    assert "interval=15" in future
