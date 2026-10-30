import os, sys, types, io
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
mod = types.ModuleType("appcore"); mod.__file__ = os.path.join(ROOT, "app.py"); sys.modules["appcore"] = mod
exec(compile(src[:src.index("APP_BUILD = ")], "app_core", "exec"), mod.__dict__)
A = mod
import requests, pandas as pd
out = ["# F&O check", ""]
r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv", headers={"User-Agent": A._UA, "Accept": "*/*"}, timeout=30)
out.append(f"status {r.status_code} len {len(r.content)}")
txt = r.text
out.append("head:\n```\n" + "\n".join(txt.splitlines()[:8]) + "\n```")
df = pd.read_csv(io.StringIO(txt)); df.columns = [str(c).strip() for c in df.columns]
out.append(f"columns {list(df.columns)[:6]} rows {len(df)}")
col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[1]
syms = df[col].astype(str).str.strip().str.upper().tolist()
out.append(f"symbol col values (first 12): {syms[:12]}")
u = A.live_fno_universe()
out.append(f"live_fno_universe: {len(u)}")
idx = [s for s in u if s in A.IDX_SYMBOLS]
out.append(f"indices in list: {idx}")
odd = [s for s in u if not s.replace('-', '').replace('&', '').isalnum()]
out.append(f"odd entries: {odd}")
out.append(f"all: {u}")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
open(os.path.join(ROOT, "results", "tv_fut_test.md"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
