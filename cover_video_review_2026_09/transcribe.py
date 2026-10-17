from pathlib import Path
import json,time
from faster_whisper import WhisperModel
P=Path(__file__).resolve().parent
video='/Users/varmakammili/Downloads/Stanford professor took two average stocks.mp4'
start=time.time()
model=WhisperModel('base.en',device='cpu',compute_type='int8',cpu_threads=6,num_workers=1)
print('MODEL READY',time.time()-start,flush=True)
segments,info=model.transcribe(video,beam_size=3,vad_filter=True,condition_on_previous_text=True)
(P/'metadata.json').write_text(json.dumps({'duration':info.duration,'language':info.language,'model':'base.en','source':video},indent=2))
with (P/'transcript.jsonl').open('w') as j,(P/'transcript.txt').open('w') as t:
 for s in segments:
  row={'start':s.start,'end':s.end,'text':s.text,'avg_logprob':s.avg_logprob,'no_speech_prob':s.no_speech_prob}
  j.write(json.dumps(row)+'\n');j.flush()
  t.write(f'[{int(s.start)//60:02d}:{int(s.start)%60:02d}–{int(s.end)//60:02d}:{int(s.end)%60:02d}] {s.text.strip()}\n');t.flush()
  print(f'{s.end:.1f}s/{info.duration:.1f}s {s.text}',flush=True)
print('DONE',time.time()-start,flush=True)
