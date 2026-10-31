import os, sys, types, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
I = A.PineInputs().sanitized()
out = ["# e2e ✓ check", ""]
syms = ["LTF", "ICICILIFE", "BANKBARODA", "SBILIFE", "RELIANCE", "ONGC"]
tfs = ["3m", "5m", "10m", "15m"]
t0 = time.time()
fr = A.load_frames(syms, list(dict.fromkeys(tfs + ["75m", "1h", "2h", "4h", "1d", "1w", "1M"])))
out.append(f"yahoo load {time.time()-t0:.1f}s")
rows = []
for s in syms:
    r = A.scan_symbol(s, fr.get(s, {}), fr.get(s, {}).get("1d"), I, 1e9, tfs)
    rows += r["rows"]
out.append(f"zones {len(rows)}")
pairs = sorted({(z["symbol"], z["tf"]) for z in rows})
t1 = time.time()
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(8) as ex:
    fc = {pq: ex.submit(A.tv_cash_zones, pq[0], pq[1], I) for pq in pairs}
    ff = {pq: ex.submit(A.tv_future_zones, pq[0], pq[1], I) for pq in pairs}
    res = {k: v.result() for k, v in fc.items()}
    fres = {k: v.result() for k, v in ff.items()}
out.append(f"TradingView cash+future check {len(pairs)} pairs: {time.time()-t1:.1f}s")
ok = 0
for z in rows:
    zz = res.get((z["symbol"], z["tf"]))
    m = "?" if zz is None else ("✓" if A.tv_match(z, zz) else "✗")
    ok += m == "✓"
    rel, fp = A.fut_rel_of(z["demand"], z["prox"], z["time"], fres.get((z["symbol"], z["tf"])) or [])
    out.append(f"- {z['symbol']} {z['tf']} {z['side']} {z['prox']:.2f} → {m} · future {rel or '—'}")
out.append(f"✓ {ok}/{len(rows)}")
t2 = time.time()
with ThreadPoolExecutor(8) as ex:
    list(ex.map(lambda pq: A.tv_cash_zones(pq[0], pq[1], I), pairs))
out.append(f"second pass (cache) {time.time()-t2:.2f}s")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
