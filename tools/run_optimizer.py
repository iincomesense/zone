"""GitHub Actions पर बिना Streamlit के Optimizer + baseline backtest चलाता है।
नतीजे results/ में: optimizer_all.csv, signals.csv.gz, summary.md
"""
import json
import os
import sys
import time
import types

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
cut = src.index("APP_BUILD = ")
mod = types.ModuleType("appcore")
mod.__file__ = os.path.join(ROOT, "app.py")
sys.modules["appcore"] = mod
exec(compile(src[:cut], "app_core", "exec"), mod.__dict__)
A = mod

OUT = os.path.join(ROOT, "results")
os.makedirs(OUT, exist_ok=True)
IST = A.IST
START = pd.Timestamp(os.environ.get("BT_START", "2024-01-01"), tz=IST)
END = pd.Timestamp(os.environ.get("BT_END", "2026-10-01"), tz=IST) + pd.Timedelta(hours=23, minutes=59)
SPLIT = pd.Timestamp(os.environ.get("BT_SPLIT", "2025-07-01"), tz=IST)
TFS = os.environ.get("BT_TFS", "1h,2h,4h,1d,1w").split(",")
_mode_f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MODE")
MODE_TXT = os.environ.get("RUN_MODE") or (open(_mode_f).read().strip() if os.path.exists(_mode_f) else "")
COLLECT = MODE_TXT.startswith("collect")
if COLLECT:                                  # हर पैरामीटर अलग से परखने के लिए: बहुत ढीले नियम, सिर्फ़ signals
    TFS = os.environ.get("BT_TFS_COLLECT", "1h,2h,4h,6h,1d").split(",")
    END = pd.Timestamp.now(tz=IST).normalize() + pd.Timedelta(hours=23, minutes=59)
    A.COLLECT_VARIANTS = [v for v in A.COLLECT_VARIANTS if v[0] in ("base", "rr2.5", "rr3")]
    os.environ["COLLECT_ALL_ENTRIES"] = "1"      # सामान्य limit order भी (सिर्फ़ gap-open नहीं)
if MODE_TXT.startswith("full"):
    TFS = list(A.ALL_TFS)
    END = pd.Timestamp.now(tz=IST).normalize() + pd.Timedelta(hours=23, minutes=59)   # आज तक (पिछले 60 दिन भी)
    if "small" in MODE_TXT:
        TFS = ["3m", "5m", "10m", "15m", "30m", "75m"]
CAP, RISK, ALLOC, KEEP = 25000.0, 0.5, 25.0, 80.0

t0 = time.time()
try:
    syms = A.live_fno_universe() or list(A.FALLBACK_FNO)
except Exception:  # noqa: BLE001
    syms = list(A.FALLBACK_FNO)
syms = list(dict.fromkeys(syms))
CHARTS = MODE_TXT.startswith("charts")
if CHARTS:                                   # चुने हुए ट्रेड के असली चार्ट (OHLC + ज़ोन) — JSON
    SEL = pd.read_csv(os.path.join(ROOT, "tools", "chart_trades.csv"))
    syms = sorted(SEL["symbol"].unique())
    TFS = sorted(SEL["tf"].unique())
print("symbols", len(syms), flush=True)
need = list(dict.fromkeys(TFS + ["75m", "1d", "1w", "1M"]))
DHAN = MODE_TXT.startswith("dhan")
if DHAN:                                     # छोटे TF: NSE cash का लंबा इतिहास Dhan API से
    cid_, tok_ = A.dhan_creds()
    ids_ = A._dhan_eq_ids(syms)
    lines_ = [f"# Dhan run ({time.strftime('%Y-%m-%d %H:%M')} UTC)", "",
              f"secrets: {'हाँ' if cid_ and tok_ else 'नहीं'} · scrip master से cash id मिले: {len(ids_)}/{len(syms)} · "
              f"उदाहरण: {dict(list(ids_.items())[:5])} · स्थिति: {A._DHAN_STATUS.get('status')}"]
    if not (cid_ and tok_ and ids_):
        open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines_))
        pd.DataFrame().to_csv(os.path.join(OUT, "optimizer_all.csv"), index=False)
        print("\n".join(lines_), flush=True)
        sys.exit(0)
    TFS = ["3m", "5m", "10m", "15m", "30m", "75m"]
    START = pd.Timestamp.now(tz=IST).normalize() - pd.Timedelta(days=730)
    END = pd.Timestamp.now(tz=IST).normalize() + pd.Timedelta(hours=23, minutes=59)
    frames = A.load_dhan_equity_frames(syms, TFS, progress=lambda f, s: print(f"dhan {f:.0%} {s}", flush=True)
                                       if int(f * 100) % 5 == 0 else None)
    yf_ = A.load_frames(syms, ["1d", "1w", "1M"])
    for s_, fr_ in yf_.items():
        frames.setdefault(s_, {}).update(fr_)
    print("dhan frames", len(frames), "secs", round(time.time() - t0), flush=True)
    A.COLLECT_VARIANTS = [v for v in A.COLLECT_VARIANTS if v[0] in ("base", "rr2.5", "rr3")]
    os.environ["COLLECT_ALL_ENTRIES"] = "1"
    T = A.opt_collect(syms, frames, TFS, A.PineInputs().sanitized(), START, END, loose_all=True,
                      progress=lambda f, s: print(f"collect {f:.0%} {s}", flush=True) if int(f * 100) % 5 == 0 else None)
    for tf_, g_ in T.groupby("tf"):
        g_.to_csv(os.path.join(OUT, f"signals_{tf_}.csv.gz"), index=False, compression="gzip")
    for tf_ in TFS:
        a_, b_, n_ = None, None, 0
        for fr_ in frames.values():
            df_ = fr_.get(tf_)
            if df_ is not None and len(df_):
                a_ = min(a_, df_.index[0]) if a_ is not None else df_.index[0]
                b_ = max(b_, df_.index[-1]) if b_ is not None else df_.index[-1]
                n_ += 1
        lines_.append(f"- {tf_}: {a_} → {b_} ({n_} symbols) · rows {int((T['tf'] == tf_).sum()) if len(T) else 0}")
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines_))
    pd.DataFrame().to_csv(os.path.join(OUT, "optimizer_all.csv"), index=False)
    print("dhan done", len(T), flush=True)
    sys.exit(0)
frames = A.load_frames(syms, need)
print("frames", len(frames), "secs", round(time.time() - t0), flush=True)
cover = {}
for s, fr in frames.items():
    for tf, df in fr.items():
        if df is not None and len(df):
            a, b = pd.Timestamp(df.index[0]), pd.Timestamp(df.index[-1])
            c = cover.setdefault(tf, [a, b, 0])
            c[0], c[1], c[2] = min(c[0], a), max(c[1], b), c[2] + 1

def build_charts(SEL):
    from dataclasses import replace as _rep
    I = A.PineInputs().sanitized()
    loose = A.opt_loose_inputs(I, True)
    out, miss = [], 0
    for (sym, tf), g in SEL.groupby(["symbol", "tf"]):
        fr = frames.get(sym) or {}
        df = fr.get(tf)
        if df is None or len(df) < 40:
            miss += len(g)
            continue
        Is = _rep(loose, commodityMode=bool(loose.commodityMode and sym.upper() in A.COMMODITY_SYMBOLS))
        zs = A.engine(df, Is, tf, htf_daily=fr.get("1d"), return_all=True) or []
        P = A._bt_prep(df, tf)
        sims = []
        for z in zs:
            t = A._bt_sim_one(P, z, 5.0, 0, 0.0, False, START, END, long_mis=bool(P["intraday"] and z["demand"]))
            if t:
                sims.append((z, t))
        idx = P["idx"]
        for r in g.itertuples():
            et = pd.Timestamp(r.entry_time)
            cand = [(z, t) for z, t in sims if pd.Timestamp(t["entry_time"]) == et and bool(t["long"]) == bool(r.long)]
            if not cand:
                miss += 1
                continue
            z, t = min(cand, key=lambda zt: abs(zt[1]["entry"] - r.entry))
            li, bc, no, pb = int(z["left_bar"]), int(z["base_count"]), int(z["leg_outs"]), int(z["created_bar"])
            ej = int(t["ej"])
            xj = int(idx.get_indexer([pd.Timestamp(t["exit_time"])])[0])
            if xj < 0:
                xj = ej
            s1 = (max(0, li - 25), min(len(df), pb + 12))
            s2 = (max(0, ej - 20), min(len(df), xj + 8))
            segs = [(s1[0], s2[1])] if s2[0] <= s1[1] + 30 else [s1, s2]
            bars, pos = [], {}
            for a_, b_ in segs:
                if bars:
                    bars.append(None)                     # बीच का हिस्सा छोड़ा
                for k in range(a_, b_):
                    pos[k] = len(bars)
                    bars.append([int(idx[k].value // 10 ** 6), round(float(P["o"][k]), 2), round(float(P["h"][k]), 2),
                                 round(float(P["l"][k]), 2), round(float(P["c"][k]), 2)])
            dist = float(z["dist"]); prox = float(z["prox"]); dem = bool(z["demand"])
            out.append(dict(symbol=sym, tf=tf, long=dem, pattern=z.get("pattern", ""), bars=bars,
                            li=pos.get(li), boring=[pos.get(k) for k in range(li + 1, li + 1 + bc)],
                            legout=[pos.get(k) for k in range(li + 1 + bc, li + 1 + bc + no)],
                            entry_i=pos.get(ej), exit_i=pos.get(xj), prox=round(prox, 2), dist=round(dist, 2),
                            target=round(float(t["target"]), 2), entry=round(float(t["entry"]), 2), exit=round(float(t["exit"]), 2),
                            reason=t["reason"], entry_time=str(t["entry_time"]), exit_time=str(t["exit_time"]),
                            gap=bool(t["gap_entry"]), t1=round(prox + (2.5 if dem else -2.5) * abs(prox - dist), 2),
                            nR5=round(float(getattr(r, "nR5", r.nR)), 2),
                            nR=round(float(r.nR), 2), edge=int(getattr(r, "f_edge", 0)), volratio=round(float(getattr(r, "f_volratio", 0) or 0), 2),
                            width=round(float(r.f_zone_pct), 2), lo_ratio=round(float(r.m_lo_ratio), 2), power=round(float(r.pow_rr), 2),
                            li_atr=round(float(r.m_in_atr), 2), lo_atr=round(float(r.m_out_atr), 2), base_tr=round(float(r.m_base_tr), 2),
                            li_body=round(float(r.m_li_body), 2), lo_body=round(float(r.m_fo_body), 2)))
    return out, miss

if CHARTS:
    out, miss = build_charts(SEL)
    json.dump(dict(trades=out, missing=miss), open(os.path.join(OUT, "charts.json"), "w"), ensure_ascii=False)
    open(os.path.join(OUT, "summary.md"), "w").write(f"# charts run · trades {len(out)} · missing {miss}\n")
    print("charts", len(out), "missing", miss, flush=True)
    sys.exit(0)

FULL = MODE_TXT.startswith("full")
if FULL:                                     # सारे F&O स्टॉक · सारे 13 TF · v25 नियम → हर ट्रेड का चार्ट
    A.COLLECT_VARIANTS = [("base", 0.0, 0.0, 5.0, "rr"), ("rr2.5", 0.0, 0.0, 2.5, "rr")]
    os.environ["COLLECT_ALL_ENTRIES"] = "1"
    I = A.PineInputs().sanitized()
    T = A.opt_collect(syms, frames, TFS, I, START, END, loose_all=True,
                      progress=lambda f, s: print(f"collect {f:.0%} {s}", flush=True) if int(f * 100) % 10 == 0 else None)
    print("rows", len(T), flush=True)
    lg = T["long"].to_numpy(bool)
    en, ex, rp = T["entry"].to_numpy(), T["exit"].to_numpy(), T["risk_ps"].to_numpy()
    xt = pd.to_datetime(T["exit_time"]).map(lambda x: x.value).to_numpy()
    qn = np.maximum(np.floor(np.minimum(125.0 / np.maximum(rp, 1e-9), 25000 * 0.25 / np.maximum(en * A.MIS_MARGIN, 1e-9))), 1)
    buy = np.where(lg, qn * en, qn * ex); sell = np.where(lg, qn * ex, qn * en)
    T["nR"] = ((sell - buy) - A._charges_vec(np.zeros(len(T), bool), buy, sell, xt)) / 125.0
    notag = ~np.where(lg, T["act_short"], T["act_long"]).astype(bool)
    vr = T["f_volratio"].fillna(9.0)
    V = ((T.m_li_body >= .6) & (T.m_in_atr >= 1.0) & (T.m_li_closewick <= .3) & (T.m_cbli <= .5) & (T.m_base_tr <= .7)
         & (T.m_li_ratio >= 1.5) & (T.m_fo_body >= .6) & (T.m_out_atr >= 1.2) & (T.m_lo_minbody >= .5) & (T.m_lo_ratio >= 2.0)
         & (T.pow_rr >= 2.5) & (T.m_room >= 3) & T.m_white.astype(bool) & (T.f_zone_pct >= .4) & (vr >= 1.5) & (T.f_edge >= 4)
         & ~T.f_bz.astype(bool) & notag & (T.m_form_dev <= 1e-9) & (T.away >= 3)
         & (T.m_li_body_base >= 1.5) & (T.m_lo_li_body >= 1.0) & T.m_white_wick.astype(bool))
    # आधी 1:2.5, आधी 1:5
    _k = ["zid", "entry_time"]
    _r25 = T[T["var"] == "rr2.5"].set_index(_k)["nR"].rename("nR25")
    T = T.join(_r25, on=_k)
    T["nR5"] = T["nR"]
    T["nR"] = 0.5 * (T["nR5"] + T["nR25"])
    V = V & (T["var"] == "base") & T["nR25"].notna()
    if os.environ.get("FULL_NOFILTER"):
        V = T["reason"].notna()
    T.to_csv(os.path.join(OUT, "signals_full.csv.gz"), index=False, compression="gzip")
    SEL = T[V].copy()
    SEL.to_csv(os.path.join(OUT, "v25_trades.csv"), index=False)
    out, miss = build_charts(SEL)
    json.dump(dict(trades=out, missing=miss), open(os.path.join(OUT, "charts.json"), "w"), ensure_ascii=False)
    lines_ = [f"# full v25 run · TFs {TFS} · trades {len(SEL)} · charts {len(out)} · missing {miss}", ""]
    for tf_, g_ in SEL.groupby("tf"):
        lines_.append(f"- {tf_}: n {len(g_)} · target {(g_.reason == 'Target').sum()} · SL {(g_.reason == 'SL').sum()} · "
                      f"win {(g_.nR > 0).mean() * 100:.0f}% · R {g_.nR.sum():.1f}")
    for tf, (a, b, n) in sorted(cover.items(), key=lambda kv: A.TF_SECONDS.get(kv[0], 0)):
        lines_.append(f"- data {tf}: {a:%Y-%m-%d} → {b:%Y-%m-%d} ({n} symbols)")
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines_))
    print("full done", len(out), flush=True)
    sys.exit(0)


I = A.PineInputs().sanitized()
T = A.opt_collect(syms, frames, TFS, I, START, END,
                  progress=lambda f, s: print(f"collect {f:.0%} {s}", flush=True) if int(f * 100) % 10 == 0 else None,
                  loose_all=COLLECT)
print("signals rows", len(T), "zones", T["zid"].nunique() if len(T) else 0, "secs", round(time.time() - t0), flush=True)
if COLLECT:
    for tf_, g_ in T.groupby("tf"):
        g_.to_csv(os.path.join(OUT, f"signals_{tf_}.csv.gz"), index=False, compression="gzip")
else:
    T.to_csv(os.path.join(OUT, "signals.csv.gz"), index=False, compression="gzip")
if COLLECT:
    lines = [f"# collect-only run ({time.strftime('%Y-%m-%d %H:%M')} UTC)", "",
             f"symbols {len(syms)} · with data {len(frames)} · TFs {TFS} · rows {len(T)} · zones {T['zid'].nunique() if len(T) else 0}", ""]
    for tf, (a, b, n) in sorted(cover.items(), key=lambda kv: A.TF_SECONDS.get(kv[0], 0)):
        lines.append(f"- {tf}: {a:%Y-%m-%d} → {b:%Y-%m-%d} ({n} symbols)")
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines))
    pd.DataFrame().to_csv(os.path.join(OUT, "optimizer_all.csv"), index=False)
    print("collect-only done", round(time.time() - t0), flush=True)
    sys.exit(0)
R, meta = A.opt_run(T, CAP, RISK, SPLIT, KEEP, max_alloc=ALLOC,
                    progress=lambda f: print(f"opt {f:.0%}", flush=True) if int(f * 1000) % 100 == 0 else None)
R.to_csv(os.path.join(OUT, "optimizer_all.csv"), index=False)
best = A.opt_pick(R)
def md(df):
    try:
        return df.to_markdown(index=False)
    except Exception:  # noqa: BLE001  (tabulate नहीं)
        return "```\n" + df.to_string(index=False) + "\n```"


keys = ["atr", "body", "li", "lo", "power", "maturity", "trend", "side", "be", "minrisk", "dmode", "loc", "htf", "approach", "rr", "volratio", "edge"]
stats = ["IS_sig", "IS_winR", "IS_grossR", "IS_costR", "IS_expR", "IS_totR", "OOS_sig", "OOS_winR",
         "OOS_grossR", "OOS_costR", "OOS_expR", "OOS_totR", "IS_trades", "IS_skipped", "IS_net",
         "OOS_trades", "OOS_skipped", "OOS_net", "OOS_max_dd"]
lines = [f"# Optimizer result ({time.strftime('%Y-%m-%d %H:%M')} UTC)", "",
         f"symbols {len(syms)} · with data {len(frames)} · TFs {TFS} · signals {len(T)} · zones {T['zid'].nunique() if len(T) else 0}",
         f"in-sample {START.date()} → {SPLIT.date()} · out-of-sample {SPLIT.date()} → {END.date()}",
         f"min signals (IS) {meta.get('min_trades', 0):.0f}", "", "## data coverage"]
for tf, (a, b, n) in sorted(cover.items(), key=lambda kv: A.TF_SECONDS.get(kv[0], 0)):
    lines.append(f"- {tf}: {a:%Y-%m-%d} → {b:%Y-%m-%d} ({n} symbols)")
lines += ["", f"## diag: parent-TF zones per TF {getattr(A, 'OPT_DIAG', {})} · htf_par share {T['htf_par'].mean() if 'htf_par' in T else 'na'}"]
lines += ["", "## baseline (current rules)"]
for rr, b in meta.get("base", {}).items():
    lines.append("- " + json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in b.items() if k in keys + stats}, ensure_ascii=False, default=str))
lines += ["", "## best", "- " + json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in (best or {}).items() if k in keys + stats}, ensure_ascii=False, default=str)]
lines += ["", "## top 25 feasible by IS totR", R[R["feasible"]].head(25)[keys + stats].round(3).pipe(md)]
pos = R[R["feasible"] & (R["IS_expR"] > 0) & (R["OOS_expR"] > 0)]
lines += ["", f"## positive in BOTH periods: {len(pos)} combos", pos.sort_values("OOS_totR", ascending=False).head(25)[keys + stats].round(3).pipe(md) if len(pos) else "none"]
# backtested preset and its HTF / neighbour variants
P = A.BACKTESTED_PRESET if hasattr(A, "BACKTESTED_PRESET") else None
if P is not None:
    q = R.copy()
    for k in ("atr", "body", "li", "maturity", "score", "side", "be", "minrisk", "dmode", "loc", "rr", "volratio", "edge", "lo", "power"):
        q = q[q[k] == P[k]]
    q = q[q["trend"].isin(["not_against", "in_favour", "off"])]
    q = q.sort_values(["approach", "htf", "trend"])
    lines += ["", "## preset + neighbours (htf off/on)", q[keys + stats].round(3).pipe(md)]
lines += ["", "## marginal effect of each option (mean over combos with IS_sig>=30)"]
for k in ["approach", "volratio", "edge", "htf", "trend", "side", "dmode", "loc", "minrisk", "maturity", "rr", "lo", "power", "atr"]:
    g = R[R["IS_sig"] >= 30].groupby(k)[["IS_sig", "IS_grossR", "IS_expR", "OOS_sig", "OOS_grossR", "OOS_expR"]].mean().round(3).reset_index()
    lines += ["", f"### {k}", md(g)]
pos2 = R[(R["IS_expR"] > 0) & (R["OOS_expR"] > 0) & (R["IS_sig"] >= 60) & (R["OOS_sig"] >= 40)]
lines += ["", f"## positive in BOTH periods with >=60 IS and >=40 OOS signals: {len(pos2)}",
          md(pos2.sort_values("OOS_totR", ascending=False).head(30)[keys + stats].round(3)) if len(pos2) else "none"]
lines += ["", f"runtime {round(time.time() - t0)} s"]
open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:40]), flush=True)
