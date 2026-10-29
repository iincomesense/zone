"""ऐप के ज़ोन की जाँच: कौन-सी candles से ज़ोन बना (Yahoo डेटा, वही इंजन जो app.py में है)।
python tools/zone_debug.py "PGEL:3m,BANKNIFTY:10m,POWERGRID:15m,BLUESTARCO:30m"
"""
import os
import sys
import types
from datetime import timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd

st = MagicMock()
st.__version__ = "debug"
st.cache_data = lambda *a, **k: (lambda f: f)
st.cache_resource = lambda *a, **k: (lambda f: f)
sys.modules["streamlit"] = st
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
src = src[:src.rstrip().rfind("main()")]
mod = types.ModuleType("appdbg")
sys.modules["appdbg"] = mod
mod.__dict__["__file__"] = os.path.join(ROOT, "app.py")
exec(compile(src, "app.py", "exec"), mod.__dict__)
A = mod.__dict__
IST = timezone(timedelta(hours=5, minutes=30))

pairs = [x.split(":") for x in (sys.argv[1] if len(sys.argv) > 1 else "PGEL:3m").split(",")]
I = A["PineInputs"]().sanitized()
out = []
for sym, tf in pairs:
    frames = A["load_frames"]([sym], [tf, "1d"])
    df = frames.get(sym, {}).get(tf)
    if df is None:
        out.append(f"## {sym} {tf}: डेटा नहीं")
        continue
    zs = A["engine"](df, I, tf, htf_daily=frames[sym].get("1d"), return_all=True)
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_convert(IST) if idx.tz is not None else idx
    out.append(f"## {sym} {tf}: bars={len(df)} first={idx[0]} last={idx[-1]} zones={len(zs)}")
    for z in zs:
        p, li = z["created_bar"], z["left_bar"]
        out.append(f"### {z['side']} {z['pattern']} prox={z['prox']:.2f} dist={z['dist']:.2f} sl={z['sl']:.2f} "
                   f"state={z['state']} bc={z['base_count']} nout={z['leg_outs']} xrr={z['xrr']:.2f} "
                   f"created={idx[p]} tags={z.get('tags')}")
        for k in range(max(0, li - 2), min(len(df), p + 3)):
            r = df.iloc[k]
            role = ("LEG-IN" if k == li else "LEG-OUT" if k > p - z["leg_outs"] and k <= p
                    else "BORING" if li < k <= p - z["leg_outs"] else "")
            out.append(f"    {idx[k]}  O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f} "
                       f"V={r.get('volume', 0):.0f}  {role}")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "zone_debug.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
