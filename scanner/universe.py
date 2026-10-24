"""Instrument universe and timeframe definitions.

The current NSE stock-futures universe is taken from the latest FO bhavcopy
when available.  The list below is only an offline fallback; it is deliberately
not used to claim that every fallback symbol still has a listed future.
"""
from __future__ import annotations

TIMEFRAMES: tuple[str, ...] = (
    "3m", "5m", "10m", "15m", "30m", "75m",
    "1h", "2h", "4h", "6h", "1d", "1w", "1M",
)

# Broad offline fallback. The latest NSE derivative bhavcopy overrides this.
FNO_FALLBACK: tuple[str, ...] = tuple(sorted(set("""
AARTIIND ABB ABBOTINDIA ABCAPITAL ABFRL ACC ADANIENSOL ADANIENT ADANIGREEN
ADANIPORTS ALKEM AMBUJACEM ANGELONE APOLLOHOSP APOLLOTYRE ASHOKLEY ASIANPAINT
ASTRAL ATUL AUBANK AUROPHARMA AXISBANK BAJAJ-AUTO BAJAJFINSV BAJFINANCE
BALKRISIND BANDHANBNK BANKBARODA BANKINDIA BATAINDIA BEL BERGEPAINT BHARATFORG
BHARTIARTL BHEL BIOCON BOSCHLTD BPCL BRITANNIA BSE CANBK CANFINHOME
CHAMBLFERT CHOLAFIN CIPLA COALINDIA COFORGE COLPAL CONCOR CROMPTON CUB
CUMMINSIND CYIENT DABUR DALBHARAT DEEPAKNTR DELHIVERY DIVISLAB DIXON DLF
DMART DRREDDY EICHERMOT ESCORTS EXIDEIND FEDERALBNK FORTIS GAIL GLENMARK
GMRAIRPORT GODREJCP GODREJPROP GRANULES GRASIM GUJGASLTD HAL HAVELLS HCLTECH
HDFCAMC HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDCOPPER HINDPETRO HINDUNILVR
HUDCO ICICIBANK ICICIGI ICICIPRULI IDEA IDFCFIRSTB IEX INDHOTEL INDIAMART
INDIGO INDUSINDBK INDUSTOWER INFY IOC IONEXCH IRB IRCTC IRFC ITC JINDALSTEL
JIOFIN JSWENERGY JSWSTEEL JUBLFOOD KALYANKJIL KANSAINER KEI KOTAKBANK
KPITTECH LALPATHLAB LAURUSLABS LT LTF LICI LTIM LTTS LUPIN M&M M&MFIN
MANAPPURAM MARICO MARUTI MCDOWELL-N MCX METROPOLIS MFSL MGL MOTHERSON
MPHASIS MUTHOOTFIN NATIONALUM NAUKRI NBCC NCC NESTLEIND NHPC NMDC NTPC
OBEROIRLTY OFSS OIL ONGC PAGEIND PERSISTENT PETRONET PFC PGEL PIIND PIDILITIND
PNB POLYCAB POWERGRID POWERINDIA PRESTIGE PVRINOX RAMCOCEM RBLBANK RECLTD
RELIANCE SAIL SBICARD SBILIFE SBIN SHREECEM SHRIRAMFIN SIEMENS SONACOMS
SRF SUNPHARMA SUPREMEIND SYNGENE TATACHEM TATACOMM TATACONSUM TATAELXSI
TATAMOTORS TATAPOWER TATASTEEL TCS TECHM TITAGARH TITAN TORNTPHARM TRENT
TVSMOTOR UBL ULTRACEMCO UNIONBANK UNITDSPR UPL VBL VEDL VOLTAS
WIPRO YESBANK ZYDUSLIFE
""".split())))

# These are zone-only instruments: each Yahoo ticker is paired with its
# TradingView symbol and a human-readable label. Global rows do not need to
# have an NSE futures contract.
GLOBAL_INSTRUMENTS: tuple[dict[str, str], ...] = (
    {"key": "NIFTY 50", "yahoo": "^NSEI", "tv": "NSE:NIFTY", "label": "NIFTY 50"},
    {"key": "NIFTY BANK", "yahoo": "^NSEBANK", "tv": "NSE:BANKNIFTY", "label": "NIFTY BANK"},
    {"key": "USD/INR", "yahoo": "INR=X", "tv": "FX_IDC:USDINR", "label": "USD/INR"},
    {"key": "GOLD", "yahoo": "GC=F", "tv": "COMEX:GC1!", "label": "Gold Futures"},
    {"key": "SILVER", "yahoo": "SI=F", "tv": "COMEX:SI1!", "label": "Silver Futures"},
    {"key": "WTI CRUDE", "yahoo": "CL=F", "tv": "NYMEX:CL1!", "label": "WTI Crude"},
    {"key": "BRENT", "yahoo": "BZ=F", "tv": "NYMEX:BZ1!", "label": "Brent Crude"},
    {"key": "S&P 500", "yahoo": "^GSPC", "tv": "SP:SPX", "label": "S&P 500"},
    {"key": "NASDAQ 100", "yahoo": "^NDX", "tv": "NASDAQ:NDX", "label": "NASDAQ 100"},
    {"key": "DOLLAR INDEX", "yahoo": "DX-Y.NYB", "tv": "TVC:DXY", "label": "US Dollar Index"},
)

# Yahoo interval/period pairs used as compact source layers. Non-native periods
# (3m, 10m, 75m, 2h, 4h, 6h, weekly and monthly) are built from these candles.
YAHOO_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("minute", "1m", "7d"),
    ("hour", "60m", "2y"),
    ("daily", "1d", "5y"),
)
