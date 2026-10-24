"""Zone-only NSE F&O + Global Instruments scanner.

Run: streamlit run app.py
"""
from __future__ import annotations

from datetime import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from scanner.dhan import fetch_dhan_quotes, load_instrument_master
from scanner.market_data import (
    download_yahoo_candles, fetch_latest_fo_bhavcopy, yahoo_ticker,
)
from scanner.scan import scan_equity_zones, scan_global_zones
from scanner.universe import FNO_FALLBACK, GLOBAL_INSTRUMENTS, TIMEFRAMES
from scanner.zones import IST
from scanner.zone_core import PINE_DEFAULTS

st.set_page_config(
    page_title="NSE Zone Scanner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      :root { color-scheme: dark; }
      .stApp { background: #0b1016; color: #e8edf3; }
      [data-testid="stAppViewContainer"] > .main .block-container {
        max-width: 98vw; padding-top: 1.05rem; padding-bottom: 2rem;
      }
      [data-testid="stHeader"] { background: rgba(11,16,22,.95); }
      h1, h2, h3 { letter-spacing: -.02em; }
      div[data-testid="stMetric"] {
        background: #111923; border: 1px solid #263445; padding: .6rem .8rem;
        border-radius: .65rem;
      }
      div[data-testid="stMetricLabel"] { color: #98a7b8; }
      .zone-note {
        background: #111923; border: 1px solid #263445; border-left: 3px solid #14b8a6;
        border-radius: 7px; padding: 9px 12px; color: #bdc9d5; font-size: 13px;
      }
      .source-note { color: #8c9aaa; font-size: 12px; }
      button[kind="primary"] { border: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=90, show_spinner=False)
def cached_yahoo_candles(tickers: tuple[str, ...], period: str, interval: str):
    return download_yahoo_candles(tickers, period, interval)


@st.cache_data(ttl=900, show_spinner=False)
def cached_fo_bhavcopy():
    return fetch_latest_fo_bhavcopy()


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def cached_dhan_master():
    return load_instrument_master()


def _secret(name: str, default: str = "") -> str:
    value = ""
    try:
        value = st.secrets.get(name, default)
        if not value and "dhan" in st.secrets:
            value = st.secrets.get("dhan", {}).get(name.lower(), default)
    except Exception:
        value = os.getenv(name, default)
    if not value:
        value = os.getenv(name, default)
    return str(value or "").strip()


def _dhan_credentials() -> tuple[str, str]:
    # Supports both Streamlit secrets [dhan] and environment variables.
    client_id, token = "", ""
    try:
        section = st.secrets.get("dhan", {})
        if hasattr(section, "get"):
            client_id = str(section.get("client_id", "") or "").strip()
            token = str(section.get("access_token", "") or "").strip()
    except Exception:
        pass
    client_id = client_id or os.getenv("DHAN_CLIENT_ID", "").strip()
    token = token or os.getenv("DHAN_ACCESS_TOKEN", "").strip()
    return client_id, token


def _assemble_market_bundle(symbols: list[str]):
    equity_tickers = [yahoo_ticker(symbol) for symbol in symbols]
    global_tickers = [item["yahoo"] for item in GLOBAL_INSTRUMENTS]
    tickers = tuple(dict.fromkeys(equity_tickers + global_tickers))
    layers = (("minute", "7d", "1m"), ("hour", "2y", "60m"),
              ("daily", "5y", "1d"))
    # Candle layers are independent. Parallel layer loads plus chunked batches
    # keep the cold scan short; cached calls are near-instant on reruns.
    loaded: dict[str, dict[str, pd.DataFrame]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {
            pool.submit(cached_yahoo_candles, tickers, period, interval): layer
            for layer, period, interval in layers
        }
        results = {}
        for job, layer in ((job, jobs[job]) for job in jobs):
            try:
                results[layer] = job.result()
            except Exception:
                results[layer] = {}
    for layer, _, _ in layers:
        source = results.get(layer, {})
        for ticker, frame in source.items():
            loaded.setdefault(ticker, {})[layer] = frame

    equity_data: dict[str, dict[str, pd.DataFrame]] = {}
    for symbol in symbols:
        ticker = yahoo_ticker(symbol)
        equity_data[symbol] = loaded.get(ticker, {})
    global_data: dict[str, dict[str, pd.DataFrame]] = {}
    for item in GLOBAL_INSTRUMENTS:
        global_data[item["key"]] = loaded.get(item["yahoo"], {})
    return equity_data, global_data


def _fmt_ts(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("Asia/Kolkata")
        else:
            stamp = stamp.tz_convert("Asia/Kolkata")
        return stamp.strftime("%d-%b %H:%M IST")
    except Exception:
        return str(value)


def _format_zone_table(frame: pd.DataFrame, *, global_table: bool = False):
    if frame.empty:
        st.info("इस फ़िल्टर में कोई वैध Fresh/Tested zone नहीं मिला।")
        return
    signal_columns = [
        "TF", "Zone", "Pattern", "Category", "Zone State", "Density Score",
        "Score / 10", "HQ", "Touch Count", "Base Count", "Zone Low", "Zone High",
        "Proximal", "Distal", "SL", "TP", "Nearest Gap %", "EOD Range Low",
        "EOD Range High", "EOD Candle", "Leg-in TR", "Leg-out TR",
        "Genuine Gap", "Overnight",
    ]
    if global_table:
        order = ["Global Instrument / TV Chart", "Instrument", "LTP", "LTP Source", *signal_columns]
        config = {
            "Global Instrument / TV Chart": st.column_config.LinkColumn(
                "Global / TradingView", display_text=r"symbol=([^&]+)"
            ),
        }
    else:
        order = [
            "Equity / TV Chart", "Equity LTP", "Current-Month Future / TV",
            "Future Symbol", "Future LTP", "Spot − Future Δ (₹)",
            "Premium / Discount", *signal_columns, "Future Expiry", "Future LTP Source",
            "Equity LTP Source",
        ]
        config = {
            "Equity / TV Chart": st.column_config.LinkColumn(
                "Equity / TradingView", display_text=r"symbol=NSE%3A([^&]+)"
            ),
            "Current-Month Future / TV": st.column_config.LinkColumn(
                "Current-month future / TradingView", display_text=r"symbol=NSE%3A([^&]+)"
            ),
        }
    visible = frame[[col for col in order if col in frame.columns]].copy()
    number_formats = {
        "Equity LTP": "₹{:.2f}", "Future LTP": "₹{:.2f}",
        "Spot − Future Δ (₹)": "₹{:+.2f}", "LTP": "{:.2f}",
        "Zone Low": "{:.2f}", "Zone High": "{:.2f}",
        "Proximal": "{:.2f}", "Distal": "{:.2f}", "SL": "{:.2f}",
        "TP": "{:.2f}", "EOD Range Low": "{:.2f}", "EOD Range High": "{:.2f}",
        "Nearest Gap %": "{:.2f}%", "Leg-in TR": "{:.2f}",
        "Leg-out TR": "{:.2f}", "Score / 10": "{:.1f}",
    }
    number_formats = {key: value for key, value in number_formats.items()
                      if key in visible.columns}
    styled = visible.style.format(number_formats, na_rep="—")
    if "Zone" in visible.columns:
        styled = styled.map(
            lambda value: "color:#14b8a6;font-weight:700;" if value == "Demand"
            else ("color:#fb7185;font-weight:700;" if value == "Supply" else ""),
            subset=["Zone"],
        )
    if "Zone State" in visible.columns:
        styled = styled.map(
            lambda value: "color:#34d399;font-weight:700;" if value == "Fresh"
            else ("color:#fbbf24;font-weight:700;" if value == "Tested" else ""),
            subset=["Zone State"],
        )
    height = max(260, min(720, 38 * (len(visible) + 1)))
    st.dataframe(styled, column_config=config, hide_index=True, height=height,
                 use_container_width=True)


def _zone_engine_controls() -> dict[str, object]:
    """Expose active supplied-core inputs; preserve explicitly inert inputs."""
    defaults = PINE_DEFAULTS
    params: dict[str, object] = {}

    def fnum(col, key, label, minimum, maximum, step, display_scale=1.0):
        return float(col.number_input(
            label, min_value=float(minimum), max_value=float(maximum),
            value=float(defaults[key]) * float(display_scale), step=float(step),
            key=f"zonecore_{key}",
        ))

    def inum(col, key, label, minimum, maximum, step=1):
        return int(col.number_input(
            label, min_value=int(minimum), max_value=int(maximum),
            value=int(defaults[key]), step=int(step), key=f"zonecore_{key}",
        ))

    with st.expander("Supplied zone_core.py · active inputs / parameters", expanded=False):
        st.caption("The EOD gate is fixed: candidate proximal must be inside that candle-day Low −10% to High +10%. All selected timeframes use the same engine.")
        st.markdown("**Leg-in / base / leg-out and ATR**")
        c = st.columns(4)
        params["accountCapital"] = fnum(c[0], "accountCapital", "Account capital", 1, 1_000_000_000, 1000)
        params["targetRR"] = fnum(c[1], "targetRR", "Target R:R", 0, 100, 0.25)
        params["slBufferAtr"] = fnum(c[2], "slBufferAtr", "SL buffer · ATR", 0, 10, 0.01)
        params["atrPeriod"] = inum(c[3], "atrPeriod", "ATR period", 2, 200)
        c = st.columns(4)
        params["volSmaPeriod"] = inum(c[0], "volSmaPeriod", "Volume SMA period", 2, 300)
        params["legOutTrMult"] = fnum(c[1], "legOutTrMult", "Leg-out TR × ATR", 0.1, 10, 0.1)
        params["legOutMinTrRatio"] = fnum(c[2], "legOutMinTrRatio", "Leg-out / leg-in TR min", 0, 10, 0.1)
        params["hqLegOutTrMult"] = fnum(c[3], "hqLegOutTrMult", "HQ leg-out × leg-in", 0, 10, 0.1)
        c = st.columns(4)
        params["hqLegInAtrMult"] = fnum(c[0], "hqLegInAtrMult", "HQ leg-in × ATR", 0, 10, 0.1)
        params["maxBaseAtrMult"] = fnum(c[1], "maxBaseAtrMult", "Max base TR × ATR", 0.1, 10, 0.1)
        params["maxWickPct"] = fnum(c[2], "maxWickPct", "Max leg-out wick · %", 0, 100, 1, display_scale=100) / 100.0
        params["minBaseCountInput"] = inum(c[3], "minBaseCountInput", "Min base candles", 1, 3)
        c = st.columns(4)
        params["maxBaseCountInput"] = inum(c[0], "maxBaseCountInput", "Max base candles", 1, 3)
        params["legInMinAtrMult"] = fnum(c[1], "legInMinAtrMult", "Min leg-in × ATR", 0, 10, 0.1)
        params["minClvPct"] = fnum(c[2], "minClvPct", "Min leg-in CLV · %", 0, 100, 1, display_scale=100) / 100.0
        params["legInToBaseSizeMult"] = fnum(c[3], "legInToBaseSizeMult", "Leg-in / base size ×", 0, 10, 0.1)
        c = st.columns(4)
        params["legInMinBodyPct"] = fnum(c[0], "legInMinBodyPct", "Min leg-in body · %", 0, 100, 1, display_scale=100) / 100.0
        params["legOutBodyHeavyPct"] = fnum(c[1], "legOutBodyHeavyPct", "Heavy leg-out body · %", 0, 100, 1, display_scale=100) / 100.0
        params["useImbalance"] = bool(c[2].toggle("Require imbalance", value=bool(defaults["useImbalance"]), key="zonecore_useImbalance"))
        params["genuineGapBonus"] = inum(c[3], "genuineGapBonus", "Genuine-gap score bonus", -100, 100)
        st.markdown("**Imbalance, classification and score**")
        c = st.columns(4)
        params["overnightGapBonus"] = inum(c[0], "overnightGapBonus", "Overnight-gap score bonus", -100, 100)
        params["rejectOppositeCoverPct"] = fnum(c[1], "rejectOppositeCoverPct", "Reject opposite-body cover · %", 0, 100, 1, display_scale=100) / 100.0
        params["minValidScore"] = inum(c[2], "minValidScore", "Minimum valid score", 0, 200)
        params["hqScoreThreshold"] = inum(c[3], "hqScoreThreshold", "HQ score threshold", 0, 200)
        c = st.columns(4)
        params["testedLegOutRetracePct"] = fnum(c[0], "testedLegOutRetracePct", "Leg-out retrace level ×", 0, 5, 0.05)
        params["useEodRange"] = True
        params["eodHighBufferPct"] = 10.0
        params["eodLowBufferPct"] = 10.0
        st.info("EOD filter fixed at Low −10% / High +10%; Fresh → Tested on proximal touch; Tested → Broken only on distal break.")
        inert_keys = [
            "riskPct", "maxImbalanceMult", "relaxGapCapOvernight", "maxTestedCount",
            "legOutToLegInBodyMult", "baseBoringMaxBodyPct", "scanAfterCandleComplete",
            "scanMonthlyOnce", "scanWeeklyOnce", "scanDailyOnce",
            "enableClosingWickCheck", "legInMinClosingWickPct",
            "enableLegOutCoverCheck", "legOutMaxCoverPct",
            "enableHQBaseColourCheck", "hqBaseColourProbabilityPct",
            "enableWhiteAreaCheck",
        ]
        st.markdown("**Pine parity fields retained but inert in supplied core**")
        st.json({key: defaults[key] for key in inert_keys})
    # settings() fills all retained defaults; the scanner overrides EOD back to ±10%.
    return params


st.title("🎯 NSE Futures · Pine Zone Core Scanner")
st.caption("केवल NSE Futures zones + अलग Global Instruments zones · 13 timeframes · TradingView links")
st.markdown(
    '<div class="zone-note"><b>Supplied zone_core.py engine:</b> leg-in → 1–3 base candles → '
    'leg-out, imbalance, engulf rejection, classification और score लागू हैं। '
    '<b>EOD gate fixed:</b> zone का Proximal उसी candle-day के Low −10% और High +10% के भीतर होना चाहिए. '
    '<b>Fresh → Tested:</b> reversal पर Proximal touch; <b>Tested → Broken:</b> केवल Distal break पर.</div>',
    unsafe_allow_html=True,
)

# Fetch the official derivative file once per cache window. If NSE blocks the
# archive, a broad fallback list is used and the missing EOD futures quotes are
# plainly marked rather than replaced with estimates.
with st.spinner("NSE F&O contract list जाँची जा रही है…"):
    try:
        fo_snapshot = cached_fo_bhavcopy()
    except Exception:
        fo_snapshot = {"contracts": {}, "symbols": [], "asof": None,
                       "source": "", "status": "unavailable"}

symbols = list(fo_snapshot.get("symbols") or FNO_FALLBACK)
contracts = fo_snapshot.get("contracts") or {}
using_fallback = not bool(fo_snapshot.get("symbols"))
fo_label = "static fallback" if using_fallback else f"NSE bhavcopy · {fo_snapshot.get('asof')}"

with st.container(border=True):
    top1, top2, top3, top4 = st.columns([1.1, 1.7, 1.35, 1.35])
    top1.metric("EOD Range", "Low −10% · High +10%")
    with top2:
        selected_tfs = st.multiselect("Timeframes", options=list(TIMEFRAMES),
                                      default=list(TIMEFRAMES))
    with top3:
        sort_mode = st.selectbox("Zone दूरी", ["Nearest · ascending", "Farthest · descending"],
                                 index=0)
    with top4:
        auto_refresh = st.toggle("Auto refresh · 2 min", value=False)

    adv1, adv2, adv3, adv4 = st.columns([1.2, 1.2, 1.4, 1.4])
    with adv1:
        selected_states = st.multiselect("Zone state", ["Fresh", "Tested"],
                                         default=["Fresh", "Tested"])
    with adv2:
        selected_sides = st.multiselect("Zone side", ["Demand", "Supply"],
                                        default=["Demand", "Supply"])
    with adv3:
        symbol_query = st.text_input("Symbol filter", placeholder="जैसे RELIANCE, SBIN")
    with adv4:
        st.caption(f"Universe: **{len(symbols)}** · {fo_label}")

    zone_params = _zone_engine_controls()
    btn_col, status_col = st.columns([1.0, 5.0])
    with btn_col:
        run_scan = st.button("🚀 Scan / Refresh", type="primary", use_container_width=True)
    with status_col:
        st.caption("डिफ़ॉल्ट में सभी 13 timeframes चुने हैं; NSE futures और global zones पर supplied zone_core.py rules चलेंगे।")

if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=120_000, key="zone_scan_autorefresh")
    except Exception:
        st.caption("Auto refresh package उपलब्ध नहीं; Scan / Refresh दबाएँ।")

signature = (
    tuple(selected_tfs), tuple(sorted(zone_params.items())), tuple(symbols),
)
now_epoch = time.time()
last_scan_epoch = float(st.session_state.get("last_scan_epoch", 0.0))
first_scan = "equity_zone_rows" not in st.session_state
signature_changed = st.session_state.get("scan_signature") != signature
scheduled = auto_refresh and (now_epoch - last_scan_epoch >= 115)
needs_scan = first_scan or run_scan or signature_changed or scheduled

if run_scan:
    # Explicit refresh bypasses the short Yahoo/NSE cache window.
    cached_yahoo_candles.clear()
    cached_fo_bhavcopy.clear()
    cached_dhan_master.clear()
    try:
        fo_snapshot = cached_fo_bhavcopy()
        if fo_snapshot.get("symbols"):
            symbols = list(fo_snapshot["symbols"])
            contracts = fo_snapshot.get("contracts") or {}
            using_fallback = False
    except Exception:
        pass
    signature = (
        tuple(selected_tfs), tuple(sorted(zone_params.items())), tuple(symbols),
    )

if needs_scan:
    if not selected_tfs:
        st.warning("कम-से-कम एक timeframe चुनें।")
    else:
        with st.spinner(f"Candles लोड + zone scan: {len(symbols)} NSE futures stocks × {len(selected_tfs)} TF …"):
            try:
                equity_data, global_data = _assemble_market_bundle(symbols)
                future_quotes = dict(contracts)
                equity_live_quotes: dict[str, float] = {}
                client_id, access_token = _dhan_credentials()
                dhan_status = "NSE/Yahoo fallback"
                if client_id and access_token:
                    try:
                        master = cached_dhan_master()
                        live = fetch_dhan_quotes(client_id, access_token, symbols, master)
                        equity_live_quotes = live.get("equity", {})
                        future_quotes.update(live.get("future", {}))
                        dhan_status = (f"Dhan live · {len(equity_live_quotes)} cash / "
                                       f"{len(live.get('future', {}))} futures quotes"
                                       if live.get("equity") or live.get("future")
                                       else "Dhan configured · no live quotes, EOD fallback")
                    except Exception as exc:
                        dhan_status = f"Dhan unavailable · {type(exc).__name__}"
                equity_rows, equity_coverage = scan_equity_zones(
                    equity_data, symbols, selected_tfs,
                    future_quotes=future_quotes,
                    equity_live_quotes=equity_live_quotes,
                    zone_params=zone_params,
                )
                global_rows, global_coverage = scan_global_zones(
                    global_data, GLOBAL_INSTRUMENTS, selected_tfs,
                    zone_params=zone_params,
                )
                st.session_state["equity_zone_rows"] = equity_rows
                st.session_state["global_zone_rows"] = global_rows
                st.session_state["equity_coverage"] = equity_coverage
                st.session_state["global_coverage"] = global_coverage
                st.session_state["future_quotes"] = future_quotes
                st.session_state["dhan_status"] = dhan_status
                st.session_state["scan_signature"] = signature
                st.session_state["last_scan_epoch"] = time.time()
                st.session_state["last_scan_text"] = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
            except Exception as exc:
                st.error(f"Scan पूरा नहीं हुआ: {type(exc).__name__}: {exc}")
                st.caption("Dependencies जाँचें; requirements.txt में Streamlit, yfinance, pandas और requests हैं।")

rows = st.session_state.get("equity_zone_rows", pd.DataFrame())
global_rows = st.session_state.get("global_zone_rows", pd.DataFrame())
if not isinstance(rows, pd.DataFrame):
    rows = pd.DataFrame(rows)
if not isinstance(global_rows, pd.DataFrame):
    global_rows = pd.DataFrame(global_rows)

last_scan_text = st.session_state.get("last_scan_text", "अभी scan नहीं हुआ")
coverage = st.session_state.get("equity_coverage", {})
with st.container(border=True):
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("NSE contracts", f"{len(symbols)}", "bhavcopy" if not using_fallback else "offline fallback")
    m2.metric("Zone rows", f"{len(rows):,}")
    m3.metric("Fresh", f"{int((rows['Zone State'] == 'Fresh').sum()) if not rows.empty else 0:,}")
    m4.metric("Tested", f"{int((rows['Zone State'] == 'Tested').sum()) if not rows.empty else 0:,}")
    m5.metric("Last scan", last_scan_text.split(" ")[1] if " " in last_scan_text else last_scan_text)
    st.caption(
        f"Coverage: {coverage.get('symbols_with_daily', 0)}/{coverage.get('symbols_requested', len(symbols))} "
        f"daily candles · {coverage.get('symbols_with_minute', 0)} minute feeds · "
        f"{st.session_state.get('dhan_status', 'NSE/Yahoo fallback')} · "
        f"Future LTP: Dhan live if configured, otherwise official NSE FO EOD close."
    )

st.subheader("📊 NSE All Futures · Zones")
st.caption(
    "EOD gate fixed: candidate candle-day Low −10% से High +10% के भीतर Proximal होना चाहिए · "
    "स्पॉट−फ्यूचर Δ = Equity LTP − Future LTP (negative Δ का अर्थ futures premium)। "
    "चार्ट लिंक पर क्लिक करके TradingView खोलें।"
)
view = rows.copy()
if not view.empty:
    if selected_tfs:
        view = view[view["TF"].isin(selected_tfs)]
    if selected_states:
        view = view[view["Zone State"].isin(selected_states)]
    else:
        view = view.iloc[0:0]
    if selected_sides:
        view = view[view["Zone"].isin(selected_sides)]
    else:
        view = view.iloc[0:0]
    if symbol_query.strip():
        view = view[view["Equity"].str.contains(symbol_query.strip().upper(), na=False)]
    view = view.sort_values("Nearest Gap %", ascending=sort_mode.startswith("Nearest"),
                            kind="stable")
    st.caption(f"दिखाए जा रहे **{len(view):,}** zones · scan complete: {last_scan_text}")
    _format_zone_table(view)
    csv = view.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ NSE zone table CSV", data=csv,
                       file_name="nse_futures_zones.csv", mime="text/csv")
else:
    st.info("अभी कोई zone row नहीं है। Scan / Refresh दबाएँ; यदि feed उपलब्ध है तो rows यहाँ आएँगी।")

st.divider()
st.subheader("🌍 Global Instruments · Zones only")
st.caption(
    "Indices, FX और commodities पर वही supplied zone_core.py rules, fixed EOD ±10% gate, "
    "Fresh/Tested state machine, nearest sorting और selected timeframes लागू हैं।"
)
gview = global_rows.copy()
if not gview.empty:
    if selected_tfs:
        gview = gview[gview["TF"].isin(selected_tfs)]
    if selected_states:
        gview = gview[gview["Zone State"].isin(selected_states)]
    else:
        gview = gview.iloc[0:0]
    if selected_sides:
        gview = gview[gview["Zone"].isin(selected_sides)]
    else:
        gview = gview.iloc[0:0]
    if symbol_query.strip():
        gview = gview[gview["Instrument"].str.contains(symbol_query.strip(), case=False, na=False)]
    gview = gview.sort_values("Nearest Gap %", ascending=sort_mode.startswith("Nearest"),
                              kind="stable")
    _format_zone_table(gview, global_table=True)
else:
    st.info("Global candle feed में अभी कोई matching zone नहीं मिला।")

st.markdown(
    '<p class="source-note">Data: Yahoo Finance candles (delayed/availability limits) + '
    'NSE derivatives bhavcopy EOD; optional Dhan marketfeed overrides LTPs. '
    'This dashboard is a scanner, not a trade recommendation or order terminal. '
    'No news, AI scoring, option chain, alerts, or auto-trading sections are included.</p>',
    unsafe_allow_html=True,
)
