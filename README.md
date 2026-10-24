# NSE Futures Zone Scanner

एक **zone-only Streamlit dashboard**: NSE stock futures पर supply/demand zones, और अलग Global Instruments zone section. पुराने terminal के News, Options, FII/DII, AI score, alerts, order execution और बाकी market panels इस build में नहीं हैं।

## क्या शामिल है

- NSE के सभी उपलब्ध `FUTSTK` contracts का universe — latest NSE derivatives bhavcopy से; NSE archive न मिले तो साफ़ तौर पर बताई गई offline fallback list.
- Equity symbol और front-month continuous future के TradingView chart links.
- Equity LTP, current/front-month future LTP, future expiry, और **Equity LTP − Future LTP** का ₹ delta. Negative delta का अर्थ futures premium; positive delta का अर्थ futures discount.
- Timeframes: **3m, 5m, 10m, 15m, 30m, 75m, 1h, 2h, 4h, 6h, 1d, 1w, 1M**.
- हर symbol की आख़िरी पूर्ण EOD daily candle से range: `Low × (1 − range%)` से `High × (1 + range%)`. Default ±10%; dashboard slider से 0–30%.
- Zone का side, low/high, proximal/distal, Fresh/Tested, निकटता प्रतिशत और nearest-first / farthest-first sorting.
- Global instruments की अलग zone table: NIFTY 50, NIFTY BANK, USD/INR, Gold, Silver, WTI, Brent, S&P 500, NASDAQ 100 और Dollar Index.
- छोटी parallel Yahoo batches और Streamlit cache; manual refresh और optional 2-minute auto-refresh.
- CSV download, tests, GitHub Actions smoke workflow और Streamlit Cloud deploy instructions.

## Zone logic

1. 1–3 compact base candles पहचानें: छोटा candle range/body, अधिकतम wick %, और optional tight-base ATR cap.
2. Base के बाद अधिकतम 3 candles में ATR के चुने हुए multiplier से बड़ा close-through leg-out मिले तो Demand/Supply candidate बने.
3. Range filter के लिए केवल आख़िरी पूर्ण EOD day का High/Low इस्तेमाल होता है; zones को उसके ±range envelope से intersect करना ज़रूरी है.
4. Confirmation के बाद price वापस zone में आए तो `Tested`, नहीं आए तो `Fresh`; distal edge के पार close हो चुका zone हटता है.
5. एक ही side के भारी overlap वाले zones deduplicate होते हैं. Default में प्रति symbol/timeframe अधिकतम 3 rows.

महत्वपूर्ण: इस workspace में मूल app/source files उपलब्ध नहीं थे। इसलिए यह स्पष्ट, testable standalone reconstruction है—compact base + ATR leg-out, wick cap, Fresh/Tested और EOD-range filtering के documented नियमों के साथ। मूल app का exact hidden zone logic/parity दावा नहीं किया जा सकता; exact match चाहिए तो source app की zone function जोड़नी होगी।

## Data / quote honesty

- **Candles और spot LTP:** Yahoo Finance. Free Yahoo intraday history की अवधि/coverage सीमित है; `1m` layer अधिकतम लगभग 7 calendar days है. Quotes delayed हो सकते हैं.
- **Future LTP:** Dhan credentials होने पर Dhan marketfeed से live quote; अन्यथा NSE FO bhavcopy का official EOD close. Dashboard source column में यह स्पष्ट दिखता है. यदि NSE archive block हो और Dhan configured न हो, future LTP `—` रहेगा—कोई अनुमानित कीमत नहीं बनाई जाती.
- **Future symbol:** NSE bhavcopy से nearest unexpired `FUTSTK` expiry; chart link TradingView के `NSE:SYMBOL1!` continuous front-month chart पर खुलता है.
- NSE/Yahoo की coverage, throttling और data timing exchange/browser से अलग हो सकती है. यह scanner है, buy/sell recommendation नहीं.

## Local run

Python 3.10+ recommended:

```bash
git clone <your-github-repo-url>
cd nse-futures-zone-scanner
python -m venv .venv
# Windows: .venv\\Scripts\\activate
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Dhan live quotes चाहें तो `.streamlit/secrets.toml.example` को `.streamlit/secrets.toml` में copy करके अपने credentials रखें. Real secrets को GitHub पर commit न करें.

## GitHub repository बनाना

इस folder को नए GitHub repository में push करें:

```bash
git init
git add .
git commit -m "Build zone-only NSE futures Streamlit scanner"
git branch -M main
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

`.gitignore` `.streamlit/secrets.toml` को रोकता है. GitHub Actions push और pull request पर `pytest` चलाता है.

## Streamlit Community Cloud

1. GitHub पर repository push करें.
2. Streamlit Community Cloud में **New app** → repository/branch चुनें → main file `app.py`.
3. App settings → **Secrets** में जरूरत हो तो यह डालें:

```toml
[dhan]
client_id = "YOUR_DHAN_CLIENT_ID"
access_token = "YOUR_DHAN_ACCESS_TOKEN"
```

4. Deploy करें. Dhan न हो तब भी Yahoo candles + उपलब्ध NSE FO EOD contract data से scan चलता है.

## Tests

```bash
pytest -q
```
