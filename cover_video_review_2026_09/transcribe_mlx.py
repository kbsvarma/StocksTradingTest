from pathlib import Path
import json,time
import mlx_whisper
from faster_whisper.audio import decode_audio
P=Path(__file__).resolve().parent
start=time.time();audio=decode_audio('/Users/varmakammili/Downloads/Stanford professor took two average stocks.mp4')
print('AUDIO READY',len(audio)/16000,flush=True)
with (P/'transcript_mlx.jsonl').open('w') as j,(P/'transcript_mlx.txt').open('w') as t:
 for offset in range(0,len(audio),300*16000):
  # Chunks cover all audio; timestamps shifted back to original timeline.
  result=mlx_whisper.transcribe(audio[offset:offset+300*16000],path_or_hf_repo='mlx-community/whisper-small.en-mlx',language='en',verbose=False,condition_on_previous_text=True)
  for s in result['segments']:
   s['start']+=offset/16000;s['end']+=offset/16000
   j.write(json.dumps(s)+'\n');j.flush()
   t.write(f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}–{int(s['end'])//60:02d}:{int(s['end'])%60:02d}] {s['text'].strip()}\n");t.flush()
  print('PROGRESS',min((offset+300*16000)/16000,len(audio)/16000),'elapsed',time.time()-start,flush=True)
print('DONE',time.time()-start,flush=True)
