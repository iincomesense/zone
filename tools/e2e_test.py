import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
import pandas as pd
I = A.PineInputs().sanitized()
out = ["# BANKBARODA pivot/gap", ""]
for sym in ["BANKBARODA", "ONGC", "SBILIFE", "RELIANCE", "LUPIN", "NTPC"]:
    fr = A.load_frames([sym], ["3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M"]).get(sym, {})
    r = A.scan_symbol(sym, fr, fr.get("1d"), I, 1e9, ["3m", "5m", "10m", "15m", "30m"])
    areas = A.htf_areas(fr)
    for z in r["rows"]:
        out.append(f"## {sym} {z['tf']} {z['side']} prox {z['prox']:.2f} · boring wick {z['base_l']:.2f}–{z['base_h']:.2f} · body {z['body_l']:.2f}–{z['body_h']:.2f} · बना {pd.Timestamp(z['start_time'], unit='ms', tz='Asia/Kolkata')}")
        out.append(f"new Multi-TF: {z.get('htf_hits')}")
        zt = z["start_time"]
        near = [a for a in areas if a["tf"] in ("1h", "2h", "4h", "1d") and a["end"] <= zt and a["t"] >= zt - 5*86400000
                and a["lo"] <= z["base_h"] and a["hi"] >= z["base_l"]]
        for a in near[-6:]:
            out.append(f"  - {a['name']} {a['kind']} {pd.Timestamp(a['t'], unit='ms', tz='Asia/Kolkata')} area {a['lo']:.2f}–{a['hi']:.2f}")
    out.append("")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
