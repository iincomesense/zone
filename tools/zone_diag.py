"""ज़ोन जाँच: app.py इंजन ने कोई ज़ोन किन candles से बनाया — leg-in, boring, leg-out और बाद की candles।
Usage: python tools/zone_diag.py  (CASES नीचे) → results/zone_diag.md
"""
import os
import sys
import types

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore")
mod.__file__ = os.path.join(ROOT, "app.py")
sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod

CASES = [("PGEL", "3m", 545.05), ("BANKNIFTY", "10m", 55793.35), ("POWERGRID", "15m", 255.40),
         ("BLUESTARCO", "30m", 1526.70), ("BHEL", "4h", 409.15), ("CIPLA", "15m", 1374.70)]
I = A.PineInputs().sanitized()
out = ["# Zone diag", ""]
syms = sorted({c[0] for c in CASES})
frames = A.load_frames(syms, ["3m", "10m", "15m", "30m", "4h", "1d"])
IST = A.IST


def ts(i, df):
    t = pd.Timestamp(df.index[i])
    t = t.tz_convert(IST) if t.tzinfo else t
    return t.strftime("%d-%b %H:%M")


for sym, tf, px in CASES:
    df = (frames.get(sym) or {}).get(tf)
    out.append(f"## {sym} {tf} (app prox {px})")
    if df is None or len(df) < 30:
        out.append("no data")
        continue
    out.append(f"bars {len(df)} · first {ts(0, df)} · last {ts(len(df) - 1, df)} · tz {getattr(df.index, 'tz', None)}")
    S = A._S(df)
    S.prepare(I, (frames.get(sym) or {}).get("1d"))
    zs = A.engine(df, I, tf, htf_daily=(frames.get(sym) or {}).get("1d"), return_all=True)
    near = sorted(zs, key=lambda z: abs(z["prox"] - px))[:2]
    for z in near:
        p = z["created_bar"]
        li = z["left_bar"]
        out.append(f"- zone {z['side']} {z['pattern']} prox {z['prox']:.2f} dist {z['dist']:.2f} sl {z['sl']:.2f} "
                   f"state {z['state']} bc {z['base_count']} nout {z['leg_outs']} xrr {z['xrr']:.2f} tags '{z['tags']}' "
                   f"created {ts(p, df)}")
        out.append("")
        out.append("| bar | time | role | O | H | L | C | V | TR/ATR |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        nout, bc = z["leg_outs"], z["base_count"]
        for i in range(max(0, li - 2), min(len(df), p + 12)):
            role = ("leg-in" if i == li else "boring" if li < i <= li + bc else
                    "leg-out" if li + bc < i <= p else "CBLI" if i == li - 1 else "after" if i > p else "")
            a = S.atr[i]
            out.append(f"| {i} | {ts(i, df)} | {role} | {S.o[i]:.2f} | {S.h[i]:.2f} | {S.l[i]:.2f} | {S.c[i]:.2f} | "
                       f"{S.v[i]:.0f} | {S.tr[i] / a:.2f} |" if np.isfinite(a) and a > 0 else
                       f"| {i} | {ts(i, df)} | {role} | {S.o[i]:.2f} | {S.h[i]:.2f} | {S.l[i]:.2f} | {S.c[i]:.2f} | {S.v[i]:.0f} | - |")
        out.append(f"ATR at creation {S.atr[p]:.3f}")
        out.append("")
    # duplicates / zero-volume / gaps in data
    zero_v = int((df["volume"] == 0).sum()) if "volume" in df.columns else -1
    dups = int(pd.Index(df.index).duplicated().sum())
    out.append(f"data check: zero-volume bars {zero_v} · duplicate timestamps {dups}")
    out.append("")

os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "zone_diag.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
