from pathlib import Path
import json,pandas as pd
P=Path(__file__).resolve().parent
GROUPS={
'user_holdings_and_mag7':'META FB GOOG GOOGL MSFT NVDA AMD AVGO AMZN MU DELL INTC TSM TSMC AAPL TSLA',
'semiconductors_equipment_design':'AAOI ACLS ACMR ADI AEHR AEIS AIP ALAB ALGM AMAT AMBA AMKR AOSL ASML ASX ATOM AVNW AXTI AZTA CAMT CDNS CEVA COHU CRDO CREE DIOD DSP EMKR ENTG FORM GFS HIMX ICHR IIVI IMOS INDI IPGP KLAC KLIC LASR LRCX LSCC MCHP MKSI MPWR MRAM MRVL MX MXL NVEC NVTS NXPI OLED ON ONTO OSIS PDFS PI PLAB POET POWI QCOM QRVO RMBS SGH SIMO SITM SLAB SMTC SNPS SOI SWKS SYNA TER TSEM TXN UCTT VECO VICR VSH WOLF XLNX XPER',
'servers_networking_storage_optics':'ANET CIEN CLS CMBM COHR COMM CSCO ERIC FLEX HPE HPQ INFN JBL JNPR LITE NETGEAR NOK NTAP NTGR PENG PLXS PSTG RDWR SANM SMCI SNDK STX WDC',
'datacenter_real_estate_power_cooling_construction':'AAON AMT APH CARR CEG COR DLR DFT DRE EME EQIX ETN FIX GEV HUBB IRM JCI MOD NRG NVT PNR PWR QTS SBAC STRL TEL TLN TT VRT VST WCC',
'ai_compute_cloud_and_mining_conversion':'APLD BITF BTDR CIFR CORZ CRWV HUT IREN NBIS WULF',
'ai_software_or_quantum_hype_overlay':'AI BBAI BIGC C3AI DXYZ IONQ PLTR QBTS QMCO RGTI SOUN TEM UPST VERI',
}
# Conservative present-day business interpretation; applies identically to all pairs.
rows=[]
for group,symbols in GROUPS.items():
 for n in symbols.split():rows.append(dict(symbol=n,reason=group))
f=pd.DataFrame(rows).drop_duplicates('symbol');f.to_csv(P/'ai_mag7_exclusions.csv',index=False)
