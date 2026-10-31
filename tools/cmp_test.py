import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
import pandas as pd, numpy as np
I = A.PineInputs().sanitized()
out = ["# Yahoo vs TradingView", ""]
def tvframe(sym, tf):
    base, rule = A.TF_FETCH[tf]
    df = A.tv_candles("NSE:" + sym, base, n_bars=5000)
    return None if df is None else (A._resample_session(df, rule) if rule else df)
def why(S, p):
    res = []
    for nout in range(1, I.maxLegOut + 1):
        for bc in range(I.minBaseCount, I.maxBaseCount + 1):
            c = A._candidate(p, bc, nout, S, I)
            res.append(f"n{nout}b{bc}:" + (c[1] if isinstance(c, tuple) else f"OK {c['pattern']} prox {c['prox']:.2f}"))
    return res
for sym, tf, px in [("LTF", "15m", 251.05), ("ICICILIFE", "3m", 412.35)]:
    out.append(f"## {sym} {tf} (app prox {px})")
    yf_ = A.load_frames([sym], [tf, "1d"]).get(sym, {})
    ydf = yf_.get(tf); tdf = tvframe(sym, tf)
    if ydf is None or tdf is None:
        out.append(f"missing data y={ydf is None} tv={tdf is None} {A._TV_STATUS}"); continue
    yz = [z for z in A.engine(ydf, I, tf, htf_daily=yf_.get("1d"), return_all=True) if abs(z["prox"] - px) < 0.02]
    if not yz:
        out.append("yahoo zone not found now"); continue
    z = yz[-1]; t = pd.Timestamp(ydf.index[z["created_bar"]])
    out.append(f"Yahoo zone {z['pattern']} created {t} li {ydf.index[z['left_bar']]} bc {z['base_count']} nout {z['leg_outs']} state {z['state']}")
    tz = [q for q in A.engine(tdf, I, tf, return_all=True) if abs(q["prox"] - px) < 0.5 * float(A._atr_last(tdf))]
    out.append(f"TV zones near: {[(q['pattern'], round(q['prox'],2), str(tdf.index[q['created_bar']]), q['state']) for q in tz]}")
    li = ydf.index[z["left_bar"]]
    w0, w1 = li - pd.Timedelta(minutes=A.TF_SECONDS[tf]//60*2), t + pd.Timedelta(minutes=A.TF_SECONDS[tf]//60*2)
    out.append("| time | Y O | Y H | Y L | Y C | TV O | TV H | TV L | TV C |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for ts in ydf.loc[w0:w1].index:
        y = ydf.loc[ts]; v = tdf.loc[ts] if ts in tdf.index else None
        out.append(f"| {ts.strftime('%d %H:%M')} | {y.open:.2f} | {y.high:.2f} | {y.low:.2f} | {y.close:.2f} | " +
                   (" | ".join(f"{v[c]:.2f}" for c in ["open","high","low","close"]) if v is not None else "- | - | - | -") + " |")
    if t in tdf.index:
        S = A._S(tdf); S.prepare(I, None)
        p = tdf.index.get_loc(t)
        out.append("TV candidate reasons at same bar: " + " ; ".join(why(S, p)))
        a = S.atr[p]; out.append(f"TV ATR {a:.3f} vs Yahoo ATR {A._S(ydf).__class__ and 0}")
    out.append("")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
