"""TradingView future candles + future ज़ोन की जाँच (GitHub Actions पर)।"""
import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
out = ["# TV future test", ""]
I = A.PineInputs().sanitized()
for sym, tf in [("JSWSTEEL", "3m"), ("POWERGRID", "15m"), ("BANKBARODA", "10m"), ("BANKNIFTY", "10m"), ("PFC", "1h"), ("RELIANCE", "1d")]:
    df = A.tv_future_frame(sym, tf)
    st = A._TV_STATUS.get("status")
    if df is None:
        out.append(f"- {sym} {tf}: None ({st})"); continue
    out.append(f"- {sym} {tf}: bars {len(df)} · {df.index[0]} → {df.index[-1]} · last close {df['close'].iloc[-1]}")
    fz = A.tv_future_zones(sym, tf, I)
    cash = A.load_frames([sym], [tf, "1d"]).get(sym, {})
    cz = A.engine(cash[tf], I, tf, htf_daily=cash.get("1d")) if tf in cash else []
    out.append(f"  future zones {len(fz)} · cash live zones {len(cz)}")
    for z in cz[-4:]:
        rel, fp = A.fut_rel_of(z["demand"], z["prox"], z["time"], fz)
        out.append(f"  cash {z['side']} {z['prox']:.2f} → future {fp} {rel}")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
