"""NSE F&O सूची (fo_mktlots.csv) → data/fno_list.json — GitHub Action रोज़ चलाता है।
ऐप पहले NSE से सीधे लेता है; NSE Streamlit Cloud को रोके तो यह फ़ाइल काम आती है।"""
import io, json, os, sys
from datetime import datetime, timezone
import pandas as pd
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
                 headers={"User-Agent": UA, "Accept": "*/*"}, timeout=40)
r.raise_for_status()
df = pd.read_csv(io.StringIO(r.text), dtype=str)
df.columns = [str(c).strip() for c in df.columns]
col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[1]
syms = sorted({x.strip().upper() for x in df[col].dropna() if x.strip() and x.strip().upper() != "SYMBOL" and x.isascii()})
if len(syms) < 100:
    print("too few symbols, not saving", len(syms)); sys.exit(1)
path = os.path.join(ROOT, "data", "fno_list.json")
old = {}
if os.path.exists(path):
    old = json.load(open(path, encoding="utf-8"))
if old.get("symbols") == syms:
    print("unchanged", len(syms)); sys.exit(0)
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(syms), "symbols": syms},
          open(path, "w", encoding="utf-8"), indent=0)
print("saved", len(syms))
