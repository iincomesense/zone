"""Optional DhanHQ live-quote override (NSE cash + front-month stock futures).

No broker order endpoints are used anywhere in this project.
"""
from __future__ import annotations

from datetime import date, datetime
from io import StringIO
import re
from typing import Any

import pandas as pd
import requests

from .zones import IST

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
        # Dhan futures symbols are commonly UNDERLYING-DDMONYY-FUT.
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
        # Equity contracts: NSE_EQ + EQUITY (or segment NSE_EQ when instrument
        # descriptions are absent).
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
        # Dhan's marketfeed request accepts up to 1000 IDs. Keep batches small
        # to avoid request-size or broker throttling issues.
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
