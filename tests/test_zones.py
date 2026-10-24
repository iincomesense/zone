import pandas as pd

from scanner.scan import scan_equity_zones, tradingview_url
from scanner.universe import TIMEFRAMES
from scanner.zone_core import (
    PINE_DEFAULTS, get_eod_range, latest_active_zones, scan_zones,
)


def _pine_demand_frame(touch: str | None = None) -> pd.DataFrame:
    rows = [(100.0, 101.0, 99.0, 100.0, 100.0) for _ in range(20)]
    rows.extend([
        (100.0, 100.5, 99.5, 100.0, 100.0),  # preceding candle
        (100.0, 104.0, 99.5, 103.7, 110.0),   # bullish leg-in
        (103.3, 103.5, 103.0, 103.4, 90.0),   # compact base
        (103.6, 109.0, 103.6, 108.8, 1000.0), # bullish leg-out + imbalance
    ])
    if touch == "proximal":
        rows.append((103.8, 104.2, 103.4, 103.9, 500.0))
    elif touch == "distal":
        rows.append((103.1, 103.2, 102.5, 102.8, 500.0))
    else:
        rows.extend([(108.8, 110.0, 108.0, 109.5, 150.0)] * 2)
    index = pd.date_range("2026-10-20 09:15", periods=len(rows), freq="5min",
                          tz="Asia/Kolkata")
    return pd.DataFrame(rows, index=index,
                        columns=["open", "high", "low", "close", "volume"])


def test_supplied_core_creates_fresh_rbr_zone_with_pine_score():
    zones = latest_active_zones(scan_zones(_pine_demand_frame()))
    assert len(zones) == 1
    zone = zones[0]
    assert zone.patternType == "RBR"
    assert zone.isDemand is True
    assert zone.state == "Fresh"
    assert zone.densityScore >= PINE_DEFAULTS["minValidScore"]
    assert zone.proxVal == 103.5
    assert zone.distVal == 103.0


def test_fresh_to_tested_uses_proximal_and_broken_uses_distal():
    tested = latest_active_zones(scan_zones(_pine_demand_frame("proximal")))
    broken = latest_active_zones(scan_zones(_pine_demand_frame("distal")))
    assert len(tested) == 1
    assert tested[0].state == "Tested"
    assert tested[0].touchCount == 1
    assert broken == []


def test_core_eod_gate_is_fixed_at_ten_percent():
    frame = _pine_demand_frame()
    low, high = get_eod_range(frame, 23, high_buffer_pct=10, low_buffer_pct=10)
    assert round(low, 2) == 89.10
    assert round(high, 2) == 121.00
    assert PINE_DEFAULTS["eodLowBufferPct"] == 10.0
    assert PINE_DEFAULTS["eodHighBufferPct"] == 10.0
    assert PINE_DEFAULTS["useEodRange"] is True


def test_all_requested_timeframes_are_available_and_scan_api_uses_core():
    assert TIMEFRAMES == (
        "3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h",
        "4h", "6h", "1d", "1w", "1M",
    )
    rows, coverage = scan_equity_zones(
        {"RELIANCE": {"minute": _pine_demand_frame()}},
        ["RELIANCE"], ["5m"],
    )
    assert coverage["zone_rows"] == 1
    assert rows.iloc[0]["Pattern"] == "RBR"
    assert rows.iloc[0]["Zone State"] == "Fresh"
    assert round(rows.iloc[0]["EOD Range Low"], 2) == 89.1
    assert round(rows.iloc[0]["EOD Range High"], 2) == 121.0


def test_tradingview_links_are_built_for_cash_and_front_future():
    cash = tradingview_url("NSE:M&M", "15m")
    future = tradingview_url("NSE:M&M", "15m", future=True)
    assert "NSE%3AM%26M" in cash
    assert "NSE%3AM%26M1!" in future
    assert "interval=15" in future
