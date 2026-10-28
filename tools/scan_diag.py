"""स्कैनर जाँच: default इनपुट पर सारे F&O × 13 TF — कितने ज़ोन, और कौनसा नियम सब रोक रहा है।"""
import os, sys, time, types, json
from dataclasses import replace
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
t0 = time.time()
try:
    syms = A.live_fno_universe() or list(A.FALLBACK_FNO)
except Exception:
    syms = list(A.FALLBACK_FNO)
syms = list(dict.fromkeys(syms))
TFS = list(A.ALL_TFS)
frames = A.load_frames(syms, list(dict.fromkeys(TFS + ["75m", "1d", "1w", "1M"])))
print("frames", len(frames), round(time.time() - t0), flush=True)
D = A.PineInputs().sanitized()
M = A.MTF_CORE_MODES
VARS = {
    "DEFAULT (स्क्रीनशॉट)": {},
    "MTF: Off": dict(coreMTF=M[0]), "MTF: not against": dict(coreMTF=M[1]),
    "Action फ़िल्टर बंद": dict(filterAction=False), "Wholesale फ़िल्टर बंद": dict(filterWholesale=False),
    "Min score 0": dict(minScore=0), "Edge 0": dict(minEdge=0), "Volume 0": dict(minVolRatio=0.0),
    "SL दूरी 0": dict(minRiskPct=0.0), "Leg-in body vs boring 0": dict(legInBodyVsBase=0.0),
    "Leg-out vs leg-in 0": dict(legOutBodyVsLegIn=0.0), "Leg-out body 0.65": dict(legOutMinBody=0.65),
    "Leg-in wick 0.5": dict(legInMaxWick=0.5), "Closing wick 1": dict(legInMaxCloseWick=1.0),
    "Opposite 1:3": dict(oppositeRR=3.0), "Opposite बंद": dict(coreOpposite=False),
    "Maturity बंद": dict(useMaturity=False),
    "Action+Wholesale+MTF बंद": dict(filterAction=False, filterWholesale=False, coreMTF=M[0]),
    "ऊपर वाले + Edge 0 + score 0": dict(filterAction=False, filterWholesale=False, coreMTF=M[0], minEdge=0, minScore=0),
}
rows, rej_def = [], {}
for name, ch in VARS.items():
    I = replace(D, **ch).sanitized()
    zones, per_tf, rej = 0, {}, {}
    fresh = 0
    for s, fr in frames.items():
        try:
            r = A.scan_symbol(s, fr, fr.get("1d"), I, 1e9, TFS, None, None)
        except Exception as e:
            continue
        for z in r["rows"]:
            zones += 1
            per_tf[z["tf"]] = per_tf.get(z["tf"], 0) + 1
            fresh += int(z.get("state") == "Fresh")
        if name.startswith("DEFAULT"):
            for k, v in (r.get("rej") or {}).items():
                rej[k] = rej.get(k, 0) + int(v)
    if name.startswith("DEFAULT"):
        rej_def = rej
    rows.append(dict(सेटिंग=name, ज़ोन=zones, Fresh=fresh, TF=json.dumps(per_tf, ensure_ascii=False)))
    print(name, zones, round(time.time() - t0), flush=True)
os.makedirs(os.path.join(ROOT, "shots"), exist_ok=True)
out = ["# स्कैनर जाँच", "", f"स्टॉक {len(frames)} · TF {len(TFS)} · समय {round(time.time() - t0)}s", "",
       pd.DataFrame(rows).to_string(index=False), "", "## DEFAULT पर ज़ोन क्यों रुके (rej)",
       json.dumps(dict(sorted(rej_def.items(), key=lambda kv: -kv[1])), ensure_ascii=False, indent=1)]
open(os.path.join(ROOT, "shots", "scan_diag.md"), "w").write("\n".join(out))
