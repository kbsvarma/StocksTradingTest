from pathlib import Path
import pandas as pd,json,hashlib
P=Path(__file__).resolve().parent
old=pd.read_csv(P/'ai_mag7_exclusions.csv')
groups={
 'additional_chip_and_electronics_infrastructure':'ARM CRUS FN KOPN AVT ARW SNX BELFA BELFB',
 'additional_cloud_and_data_infrastructure':'IBM ORCL NTNX DOCN NET AKAM SNOW MDB CFLT ESTC DDOG DT GDS VNET BABA BIDU TCEHY',
 'additional_network_infrastructure':'UI ADTN CALX LUMN',
}
extra=pd.DataFrame([dict(symbol=n,reason=g) for g,ns in groups.items() for n in ns.split()]);new=pd.concat([old,extra],ignore_index=True).drop_duplicates('symbol');new.to_csv(P/'ai_mag7_exclusions_v2.csv',index=False)
(P/'EXCLUSION_REVIEW.md').write_text('''# Factual AI-infrastructure taxonomy review

The first exclusion file omitted several identifiable infrastructure suppliers, including ARM, IBM and Oracle. Correct those omissions consistently across the entire screened universe. Additional entries cover the same previously stated categories: semiconductors/electronics, cloud/data infrastructure, networking and direct infrastructure supply.

This is a factual business-classification correction after the raw and screened rankings, not a change to the portfolio rule or ranking score. The original file and its original hash remain intact for the raw-run audit. The final restricted list uses ai_mag7_exclusions_v2.csv. Both versions and this timing disclosure are retained. Check and report whether the resulting top ten changes rather than assume it does.

Primary sources for the main omissions: https://www.oracle.com/cloud/ai-infrastructure/ ; https://www.ibm.com/solutions/ai-infrastructure ; https://www.arm.com/markets/computing-infrastructure . Classification remains a conservative present-day thematic overlay, not a point-in-time industry database or a ban on all speculative themes.
''')
print('Added',len(new)-len(old),'factual exclusions')
