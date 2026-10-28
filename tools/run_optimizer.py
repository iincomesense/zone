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
CAP, RISK, ALLOC, KEEP = 25000.0, 0.5, 25.0, 80.0

t0 = time.time()
try:
    syms = A.live_fno_universe() or list(A.FALLBACK_FNO)
except Exception:  # noqa: BLE001
    syms = list(A.FALLBACK_FNO)
syms = list(dict.fromkeys(syms))
print("symbols", len(syms), flush=True)
need = list(dict.fromkeys(TFS + ["75m", "1d", "1w", "1M"]))
frames = A.load_frames(syms, need)
print("frames", len(frames), "secs", round(time.time() - t0), flush=True)
cover = {}
for s, fr in frames.items():
    for tf, df in fr.items():
        if df is not None and len(df):
            a, b = pd.Timestamp(df.index[0]), pd.Timestamp(df.index[-1])
            c = cover.setdefault(tf, [a, b, 0])
            c[0], c[1], c[2] = min(c[0], a), max(c[1], b), c[2] + 1

I = A.PineInputs().sanitized()
T = A.opt_collect(syms, frames, TFS, I, START, END,
                  progress=lambda f, s: print(f"collect {f:.0%} {s}", flush=True) if int(f * 100) % 10 == 0 else None)
print("signals rows", len(T), "zones", T["zid"].nunique() if len(T) else 0, "secs", round(time.time() - t0), flush=True)
T.to_csv(os.path.join(OUT, "signals.csv.gz"), index=False, compression="gzip")
R, meta = A.opt_run(T, CAP, RISK, SPLIT, KEEP, max_alloc=ALLOC,
                    progress=lambda f: print(f"opt {f:.0%}", flush=True) if int(f * 1000) % 100 == 0 else None)
R.to_csv(os.path.join(OUT, "optimizer_all.csv"), index=False)
best = A.opt_pick(R)
def md(df):
    try:
        return df.to_markdown(index=False)
    except Exception:  # noqa: BLE001  (tabulate नहीं)
        return "```\n" + df.to_string(index=False) + "\n```"


keys = ["atr", "body", "li", "lo", "power", "maturity", "trend", "side", "be", "minrisk", "dmode", "loc", "htf", "rr"]
stats = ["IS_sig", "IS_winR", "IS_grossR", "IS_costR", "IS_expR", "IS_totR", "OOS_sig", "OOS_winR",
         "OOS_grossR", "OOS_costR", "OOS_expR", "OOS_totR", "IS_trades", "IS_skipped", "IS_net",
         "OOS_trades", "OOS_skipped", "OOS_net", "OOS_max_dd"]
lines = [f"# Optimizer result ({time.strftime('%Y-%m-%d %H:%M')} UTC)", "",
         f"symbols {len(syms)} · with data {len(frames)} · TFs {TFS} · signals {len(T)} · zones {T['zid'].nunique() if len(T) else 0}",
         f"in-sample {START.date()} → {SPLIT.date()} · out-of-sample {SPLIT.date()} → {END.date()}",
         f"min signals (IS) {meta.get('min_trades', 0):.0f}", "", "## data coverage"]
for tf, (a, b, n) in sorted(cover.items(), key=lambda kv: A.TF_SECONDS.get(kv[0], 0)):
    lines.append(f"- {tf}: {a:%Y-%m-%d} → {b:%Y-%m-%d} ({n} symbols)")
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
    for k in ("atr", "body", "li", "maturity", "score", "side", "be", "minrisk", "dmode", "loc", "rr"):
        q = q[q[k] == P[k]]
    q = q[q["lo"].isin([2.5, 3.0]) & q["power"].isin([2.0, 3.0]) & q["trend"].isin(["not_against", "in_favour"])]
    lines += ["", "## preset + neighbours (htf off/on)", q[keys + stats].round(3).pipe(md)]
lines += ["", f"runtime {round(time.time() - t0)} s"]
open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:40]), flush=True)
