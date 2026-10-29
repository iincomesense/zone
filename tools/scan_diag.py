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
BASEOFF = dict(minVolRatio=0.0, minRiskPct=0.0, coreMTF=M[0], filterAction=False, filterWholesale=False, minEdge=0, minScore=0,
               legInBodyVsBase=0.0, legOutBodyVsLegIn=0.0, legInMaxCloseWick=1.0, whiteWick=False)
ORIG = dict(BASEOFF, legInAtrMult=1.2, legOutAtrMult=1.2, boringAtrMult=1.0, legOutToBaseSizeMult=2.5, maxLegOut=3,
            allowTwoLegOut=True, maxBaseCountInput=3, powerMinRR=3.0, corePower=True, powerMode=A.POWER_MODES[1],
            useWhiteArea=True, useFormation=True, formationRange=False, formationTolAtr=0.10, useCBLI=True, cbliMaxPct=0.5,
            oppositeRR=5.0, legOutMinBody=0.65, legInMaxWick=0.20, gapTolAtr=0.10, legOutTooLong=4.0, coverPct=0.5)
VARS = {"DEFAULT (स्क्रीनशॉट)": {},
        "आपके candle नियम, सारी छन्नी बंद": BASEOFF,
        "+ Opposite बंद": dict(BASEOFF, coreOpposite=False),
        "+ Opposite बंद, Fresh बंद": dict(BASEOFF, coreOpposite=False, coreFresh=False),
        "शुरुआती मूल नियम (Pine v1 जैसे), छन्नी बंद": ORIG,
        "मूल नियम + Opposite बंद": dict(ORIG, coreOpposite=False)}
rows, rej_def = [], {}
ERRS = {}
BAND = {}
for name, ch in VARS.items():
    I = replace(D, **ch).sanitized()
    zones, per_tf, rej = 0, {}, {}
    fresh = 0
    for s, fr in frames.items():
        try:
            r = A.scan_symbol(s, fr, fr.get("1d"), I, 1e9, TFS, None, None)
        except Exception as e:
            import traceback
            k = type(e).__name__ + ": " + str(e)[:120] + " @ " + traceback.format_exc().strip().splitlines()[-3].strip()[:160]
            ERRS[k] = ERRS.get(k, 0) + 1
            continue
        bb = {t3: A.eod_band(fr.get(t3), t3, I.atrPeriod) for t3 in ("1d", "1w", "1M")}
        for z in r["rows"]:
            tg = "".join(A.BAND_LETTER[t3] for t3, b in bb.items() if b and b["low"] <= z["prox"] <= b["high"])
            for k in ("D", "W", "M"):
                if k in tg:
                    BAND.setdefault(name, {})[k] = BAND.setdefault(name, {}).get(k, 0) + 1
            if tg:
                BAND.setdefault(name, {})["कोई भी"] = BAND[name].get("कोई भी", 0) + 1
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
hist = {}
for nm in ("DEFAULT (स्क्रीनशॉट)", "आपके candle नियम, सारी छन्नी बंद", "शुरुआती मूल नियम (Pine v1 जैसे), छन्नी बंद"):
    I = replace(D, **VARS[nm]).sanitized()
    hh = {}
    for s_ in ["RELIANCE", "SBIN", "TCS", "INFY", "HDFCBANK"]:
        fr = frames.get(s_) or {}
        for tf in ("15m", "1h", "4h", "1d"):
            df = fr.get(tf)
            if df is None:
                continue
            zs = A.engine(df, I, tf, htf_daily=fr.get("1d"), return_all=True)
            for z in zs:
                hh[f"{tf}:{z['state']}"] = hh.get(f"{tf}:{z['state']}", 0) + 1
    hist[nm] = hh
out = ["# स्कैनर जाँच", "", f"स्टॉक {len(frames)} · TF {len(TFS)} · समय {round(time.time() - t0)}s", "",
       pd.DataFrame(rows).to_string(index=False), "", "## DEFAULT पर ज़ोन क्यों रुके (rej)",
       json.dumps(dict(sorted(rej_def.items(), key=lambda kv: -kv[1])), ensure_ascii=False, indent=1),
       "## Daily EOD दायरे में", json.dumps(BAND, ensure_ascii=False, indent=1),
       "## Errors", json.dumps(ERRS, ensure_ascii=False, indent=1),
       "## 5 स्टॉक, engine के सारे ज़ोन की स्थिति", json.dumps(hist, ensure_ascii=False, indent=1)]
open(os.path.join(ROOT, "shots", "scan_diag.md"), "w").write("\n".join(out))
