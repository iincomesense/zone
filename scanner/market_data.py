"""Yahoo candles and NSE derivatives bhavcopy adapters.

Yahoo Finance is the zero-key OHLC fallback used for zone history and spot
prices. NSE FO bhavcopy supplies the official end-of-day front-month future
close and the live stock-futures universe. Optional Dhan live quotes are in
``scanner.dhan`` and override these fallback quotes when configured.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from io import BytesIO
import re
import time
import zipfile
from typing import Any

import pandas as pd
import requests

from .zones import normalize_ohlcv, IST

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
        # The ticker has normally been selected already. Keep only the OHLCV
        # level if yfinance returned a nested column index.
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
    # Drop duplicate columns (e.g. Close and Adj Close both normalized to Close).
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
    except Exception as exc:  # pragma: no cover - covered by app-level message
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
                           interval: str, *, chunk_size: int = 28,
                           workers: int = 4) -> dict[str, pd.DataFrame]:
    """Download many symbols in small parallel batches, failing soft per batch."""
    clean = tuple(dict.fromkeys(str(t).strip() for t in tickers if str(t).strip()))
    if not clean:
        return {}
    chunks = [clean[i:i + chunk_size] for i in range(0, len(clean), chunk_size)]
    result: dict[str, pd.DataFrame] = {}
    if len(chunks) == 1:
        return _download_chunk(chunks[0], period, interval)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunks)))) as pool:
        jobs = {pool.submit(_download_chunk, part, period, interval): part for part in chunks}
        for job in as_completed(jobs):
            try:
                result.update(job.result())
            except Exception:
                # A bad Yahoo batch must not erase good symbols from the other
                # chunks. The dashboard reports coverage and remains usable.
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
    return (
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
        # Legacy bhavcopy uses FUTSTK. Newer files can call stock futures STF.
        allowed = tmp["_instrument"].str.contains(r"FUTSTK|STF", regex=True, na=False)
        if allowed.any():
            tmp = tmp.loc[allowed]
        else:
            tmp = tmp.iloc[0:0]
    tmp = tmp.dropna(subset=["_expiry", "_close"])
    tmp = tmp[(tmp["_symbol"] != "") & (tmp["_close"] > 0)]
    tmp["_expiry_date"] = tmp["_expiry"].dt.date
    # The official file may be one session old. Select contracts against today's
    # date, not the file's date, so expiry-day contracts are not shown as current
    # after they have expired.
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


def fetch_latest_fo_bhavcopy(*, max_calendar_days: int = 8,
                             timeout: float = 2.5) -> dict[str, Any]:
    """Find the newest accessible NSE FO bhavcopy and extract FUTSTK contracts."""
    session = _nse_session()
    today = datetime.now(IST).date()
    for offset in range(max(1, int(max_calendar_days))):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for url in _bhavcopy_urls(day):
            try:
                response = session.get(url, timeout=timeout)
                if response.status_code != 200 or len(response.content) < 100:
                    continue
                parsed = _read_bhavcopy(response.content)
                if parsed is None or parsed.empty:
                    continue
                contracts = _parse_fo_contracts(parsed, asof=day)
                if contracts:
                    return {
                        "contracts": contracts,
                        "symbols": sorted(contracts),
                        "asof": day,
                        "source": url,
                        "status": "ok",
                    }
            except requests.RequestException:
                continue
            except Exception:
                continue
    return {"contracts": {}, "symbols": [], "asof": None,
            "source": "", "status": "unavailable"}
