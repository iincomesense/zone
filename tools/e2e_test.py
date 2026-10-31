import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
import pandas as pd
I = A.PineInputs().sanitized()
out = ["# DABUR pivot check", ""]
sym = "DABUR"
fr = A.load_frames([sym], ["3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M"]).get(sym, {})
r = A.scan_symbol(sym, fr, fr.get("1d"), I, 1e9, ["3m", "5m", "10m", "15m", "30m"])
areas = A.htf_areas(fr)
ts = lambda x: pd.Timestamp(x, unit="ms", tz="Asia/Kolkata").strftime("%d-%b %H:%M")
for z in r["rows"]:
    zt = z["start_time"]
    out.append(f"## {z['tf']} {z['side']} prox {z['prox']:.2f} · boring body {z['body_l']:.2f}–{z['body_h']:.2f} · leg-in {ts(zt)} · created {ts(z['time'])}")
    out.append(f"Multi-TF: {z.get('htf_hits')}")
    for a in areas:
        if a["end"] <= zt and (a.get("till") is None or zt <= a["till"]) and a["lo"] <= z["body_l"] and z["body_h"] <= a["hi"] \
                and A.TF_SECONDS[a["tf"]] > A.TF_SECONDS[z["tf"]]:
            days = next(d for t_, _, d in A.HTF_AREA_TFS if t_ == a["tf"])
            if a["t"] >= zt - days * 86400000:
                out.append(f"  - match {a['name']} {a['kind']} 2nd candle {ts(a['t'])} area {a['lo']:.2f}–{a['hi']:.2f} till {ts(a['till']) if a.get('till') else '—'}")
h = fr["1h"]
out.append("\n## 1h candles 27–30 Oct")
out.append("| time | O | H | L | C |")
for t0, row in h.loc["2026-10-27":"2026-10-30"].iterrows():
    out.append(f"| {t0.strftime('%d %H:%M')} | {row.open:.2f} | {row.high:.2f} | {row.low:.2f} | {row.close:.2f} |")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
