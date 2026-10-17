"""Read only archived CF index frames; no live state or API access."""
from pathlib import Path
import gzip,json,hashlib
P=Path(__file__).resolve().parent
manifest=json.loads(Path('/home/varma/kalshi_band_ride_2026_10_03/inputs/stream_top/manifest.json').read_text())['manifest']
records=[];sources=[]
for item in manifest:
    path=Path(item['source']);n=0
    with gzip.open(path,'rt') as f:
        for line in f:
            if '"type":"cfbenchmarks_value"' not in line:continue
            r=json.loads(line);msg=r['msg'];v=json.loads(msg['data']);ts=int(v['time'])
            if ts%60000!=59000:continue
            records.append([msg['index_id'],ts,r['client_received_at_ms'],float(v['value'])]);n+=1
    sources.append(dict(path=str(path),source_sha256=item['source_sha256'],minute_end_ticks=n))
with gzip.open(P/'inputs/cf_receipts.json.gz','wt') as f:json.dump(dict(sources=sources,rows=records),f,separators=(',',':'))
print('receipt rows',len(records),'files',len(sources),flush=True)
