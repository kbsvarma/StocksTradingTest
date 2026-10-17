"""Scan universe (a query list, not a selection — names with no data drop out).
Dow 30 (2026 membership approx incl. recent adds) + large-cap S&P names with
liquid options + BRK.B + major ETFs."""

DOW30 = ["AAPL","AMGN","AMZN","AXP","BA","CAT","CRM","CSCO","CVX","DIS","GS",
         "HD","HON","IBM","JNJ","JPM","KO","MCD","MMM","MRK","MSFT","NKE",
         "NVDA","PG","SHW","TRV","UNH","V","VZ","WMT"]

LARGE = ["ABBV","ABT","ACN","ADBE","ADI","ADP","AMAT","AMD","ANET","APH","AVGO",
         "BAC","BK","BKNG","BLK","BMY","BRK.B","BSX","C","CB","CI","CL","CMCSA",
         "CME","COF","COP","COST","CVS","DE","DHR","DUK","ELV","EMR","EOG","ETN",
         "F","FDX","GD","GE","GILD","GM","GOOGL","HCA","INTC","INTU","ISRG","ITW",
         "KLAC","KMI","LIN","LLY","LMT","LOW","LRCX","MA","MDLZ","MDT","MET","META",
         "MMC","MO","MPC","MS","MU","NEE","NFLX","NOC","NOW","ORCL","OXY","PANW",
         "PEP","PFE","PGR","PLD","PM","PSX","QCOM","REGN","RTX","SBUX","SCHW","SLB",
         "SO","SPG","SYK","T","TGT","TJX","TMO","TMUS","TSLA","TXN","UNP","UPS",
         "USB","VLO","VRTX","WFC","XOM","ZTS","PLTR","APP","SNOW","UBER","ABNB",
         "SHOP","SQ","PYPL","CRWD","DDOG","NET","ZS","MRVL","SMCI","ARM","DELL",
         "HPE","ORLY","AZO","CMG","DPZ","YUM","KHC","GIS","K","HSY","STZ","KDP",
         "MNST","CCL","RCL","DAL","UAL","LUV","AAL","MAR","HLT","WYNN","LVS","MGM"]

ETFS = ["SPY","QQQ","DIA","IWM","XLF","XLE","XLK","XLV","XLP","XLU","XLI","XLY",
        "XLB","GLD","SLV","TLT","HYG","EEM","EFA","SMH","KRE","XBI"]

UNIVERSE = sorted(set(DOW30 + LARGE + ETFS))

if __name__ == "__main__":
    print(len(UNIVERSE), "tickers")
