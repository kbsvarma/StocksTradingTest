"""Offline sample reads and resource-limit verification on the migrated host."""
from pathlib import Path
import json, os, subprocess
import pandas as pd
import pyarrow.parquet as pq
import databento as db
C=Path('/home/varma/.data_migration_control_20260926')
A=Path('/home/varma/old_server_archive_2026_09_26')
samples=json.loads((C/'research_samples.json').read_text())
parquet=A/'home/varmak'/samples['parquet']['path']
dbn=A/'home/varmak'/samples['dbn']['path']
table=pq.read_table(parquet)
store=db.DBNStore.from_file(dbn)
frame=store.to_df()
limits={}
for line in Path('/proc/self/cgroup').read_text().splitlines():
 if line.startswith('0::'):
  group=Path('/sys/fs/cgroup')/line[3:].lstrip('/')
  for name in ['cpu.max','memory.max','io.weight']:
   limits[name]=(group/name).read_text().strip() if (group/name).exists() else 'not delegated to user scopes'
assert limits['cpu.max'].split()[0]!='max',limits
quota,period=map(int,limits['cpu.max'].split());assert quota/period==2,limits
assert int(limits['memory.max'])==12*1024**3,limits
limits['nice']=os.getpriority(os.PRIO_PROCESS,0)
limits['ionice']=subprocess.check_output(['ionice','-p',str(os.getpid())],text=True).strip()
assert limits['nice']==15 and 'prio 7' in limits['ionice'],limits
assert table.num_rows>0 and len(frame)>0
r={'parquet_rows':table.num_rows,'parquet_columns':table.num_columns,'dbn_rows':len(frame),'resource_limits':limits,'working_directory':os.getcwd(),'python':os.sys.executable}
(A/'audit/research_smoke.json').write_text(json.dumps(r,indent=2))
print(json.dumps(r,indent=2))
