from pathlib import Path
import time,subprocess,sys
P=Path(__file__).resolve().parent
while not (P/'long_all_fetch.log').read_text().rstrip().endswith('DONE 1917'):time.sleep(20)
for script in ['prepare_long.py','search_long_universe.py','rank_long_universe.py']:
 subprocess.run([sys.executable,str(P/script)],check=True)
print('DONE long universe pipeline',flush=True)
