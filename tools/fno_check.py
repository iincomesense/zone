import os, io, requests, pandas as pd
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv", headers={"User-Agent": UA, "Accept": "*/*"}, timeout=30)
df = pd.read_csv(io.StringIO(r.text), dtype=str); df.columns = [c.strip() for c in df.columns]
df = df.apply(lambda c: c.str.strip())
m1 = df.columns[2]
out = [f"first month col {m1}; rows {len(df)}"]
hdr = df.index[df["SYMBOL"].str.upper() == "SYMBOL"]
cut = hdr[0] if len(hdr) else 0
idx = df.loc[:cut - 1]; stk = df.loc[cut + 1:]
out.append(f"index rows {len(idx)}: {idx['SYMBOL'].tolist()}")
out.append(f"stock rows {len(stk)}")
blank = stk[stk[m1].fillna("") == ""]
out.append(f"stocks with blank {m1}: {len(blank)} {blank['SYMBOL'].tolist()}")
for c in df.columns[3:5]:
    b = stk[stk[c].fillna("") == ""]
    out.append(f"stocks blank in {c}: {len(b)} {b['SYMBOL'].tolist()[:20]}")
os.makedirs("results", exist_ok=True)
open("results/tv_fut_test.md", "w").write("\n".join(out) + "\n")
print("\n".join(out))
