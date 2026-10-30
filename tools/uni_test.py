import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
out = ["# universe test", ""]
u = A.live_fno_universe()
stocks = [x for x in u if x not in A.IDX_SYMBOLS and not x.startswith("NIFTY")]
out.append(f"fno list {len(u)} · stocks {len(stocks)}")
I = A.PineInputs().sanitized()
for name, syms in [("Index", A.INDEX_UNIVERSE), ("Global", A.GLOBAL_SYMBOLS)]:
    fr = A.load_frames(syms, ["15m", "1h", "1d"])
    out.append(f"## {name}: data {len(fr)}/{len(syms)} · missing {[s for s in syms if s not in fr]}")
    for s in syms:
        f = fr.get(s, {})
        d = f.get("15m")
        out.append(f"- {s}: 15m bars {0 if d is None else len(d)}" + ("" if d is None else f" · last {d.index[-1]} close {d['close'].iloc[-1]}"))
    r = A.scan_symbol(syms[0], fr.get(syms[0], {}), fr.get(syms[0], {}).get("1d"), I, 1e9, ["15m", "1h"])
    out.append(f"scan {syms[0]} ok · rows {len(r['rows'])}")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
