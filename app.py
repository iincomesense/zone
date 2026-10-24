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
    if global_table:
        order = [
            "Global Instrument / TV Chart", "LTP", "LTP Source", "TF", "Zone",
            "Zone Low", "Zone High", "Proximal", "Distal", "Zone State",
            "Nearest Gap %", "Strength (ATR)", "EOD Candle",
        ]
        config = {
            "Global Instrument / TV Chart": st.column_config.LinkColumn(
                "Global / TradingView", display_text=r"symbol=([^&]+)"
            ),
        }
    else:
        order = [
            "Equity / TV Chart", "Equity LTP", "Current-Month Future / TV",
            "Future Symbol", "Future LTP", "Spot − Future Δ (₹)",
            "Premium / Discount", "TF", "Zone", "Zone Low", "Zone High",
            "Proximal", "Distal", "Zone State", "Nearest Gap %", "EOD Range Low",
            "EOD Range High", "EOD Candle", "Future Expiry", "Future LTP Source",
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
        "Proximal": "{:.2f}", "Distal": "{:.2f}",
        "Nearest Gap %": "{:.2f}%", "Strength (ATR)": "{:.2f}",
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


st.title("🎯 NSE Futures · Supply / Demand Zone Scanner")
st.caption("सिर्फ़ ज़ोन स्कैन + Global Instruments ज़ोन · NSE F&O स्टॉक्स · TradingView chart links")
st.markdown(
    '<div class="zone-note"><b>रेंज नियम:</b> हर symbol की आख़िरी पूर्ण EOD day candle लें → '
    'Low से चुना हुआ % घटाएँ और High में वही % जोड़ें। केवल उस दायरे को काटने वाले '
    'zones दिखेंगे। <b>Fresh</b> = confirmation के बाद retest नहीं; '
    '<b>Tested</b> = zone में लौट चुका, distal close से टूटा नहीं।</div>',
    unsafe_allow_html=True,
)
st.caption("मूल source app files workspace में नहीं थे; zone engine यहाँ documented compact-base/ATR-leg-out rules की standalone reconstruction है—exact hidden-logic parity का दावा नहीं।")

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
    with top1:
        range_pct = st.slider("EOD रेंज ± %", min_value=0, max_value=30,
                              value=10, step=1,
                              help="EOD Low × (1 − %) और EOD High × (1 + %)")
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

    with st.expander("Zone engine parameters (base / leg-out / wick rules)"):
        p1, p2, p3 = st.columns(3)
        legout_mult = p1.slider("Leg-out TR ×", 1.0, 3.0, 1.5, 0.1)
        max_wick_pct = p2.slider("Max base wick %", 10, 80, 50, 5)
        tight_base = p3.toggle("Tight base filter", value=True)

    btn_col, status_col = st.columns([1.0, 5.0])
    with btn_col:
        run_scan = st.button("🚀 Scan / Refresh", type="primary", use_container_width=True)
    with status_col:
        st.caption("पहला scan सभी NSE stock futures और चुने गए timeframes पर चलेगा; अगली बार candles cache से मिलेंगी।")

if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=120_000, key="zone_scan_autorefresh")
    except Exception:
        st.caption("Auto refresh package उपलब्ध नहीं; Scan / Refresh दबाएँ।")

signature = (
    tuple(selected_tfs), int(range_pct), float(legout_mult), int(max_wick_pct),
    bool(tight_base), tuple(symbols),
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
        tuple(selected_tfs), int(range_pct), float(legout_mult), int(max_wick_pct),
        bool(tight_base), tuple(symbols),
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
                    equity_data, symbols, selected_tfs, range_pct,
                    future_quotes=future_quotes,
                    equity_live_quotes=equity_live_quotes,
                    legout_mult=legout_mult, max_wick_pct=max_wick_pct,
                    tight_base=tight_base, max_zones_per_tf=3,
                )
                global_rows, global_coverage = scan_global_zones(
                    global_data, GLOBAL_INSTRUMENTS, selected_tfs, range_pct,
                    legout_mult=legout_mult, max_wick_pct=max_wick_pct,
                    tight_base=tight_base, max_zones_per_tf=2,
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
    f"स्कैन रेंज प्रति symbol: पिछले पूर्ण EOD Low − {range_pct}% से EOD High + {range_pct}% · "
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
    f"Indices, FX और commodities पर वही EOD range, Fresh/Tested, nearest sorting और "
    f"timeframes लागू हैं · range ±{range_pct}%."
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
