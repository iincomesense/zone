import os, sys, types, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
I = A.PineInputs().sanitized()
out = ["# TV ✓ test", ""]
syms = ["LTF", "ICICILIFE", "BANKBARODA", "SBILIFE", "RELIANCE", "COFORGE", "ONGC", "LICI"]
tfs = ["3m", "5m", "10m", "15m", "30m"]
fr = A.load_frames(syms, tfs + ["1d"])
t0 = time.time(); tot = ok = 0
for s in syms:
    r = A.scan_symbol(s, fr.get(s, {}), fr.get(s, {}).get("1d"), I, 1e9, tfs)
    for z in r["rows"]:
        tz = A.tv_cash_zones(s, z["tf"], I)
        m = "?" if tz is None else ("✓" if A.tv_match(z, tz) else "✗")
        tot += 1; ok += m == "✓"
        out.append(f"- {s} {z['tf']} {z['side']} {z['prox']:.2f} → {m}")
out.append(f"\n✓ {ok}/{tot} · time {time.time()-t0:.0f}s")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
