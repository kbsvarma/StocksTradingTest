from weather_model import *
from pathlib import Path
import hashlib
spec={'name':'weather_index_persistence_v1_research','mode':'RESEARCH_ONLY','live_orders_enabled':False,'training_start':START,'training_end':SPLIT,'evaluation_start':SPLIT,'evaluation_end':END,'models_considered':['persistence','trend'],'selected_after_comparison':'persistence','selection_is_exploratory':True,'source_lag_minutes':10,'required_consecutive_minutes':61,'latest_source_status':'normal','horizons_minutes':[30,15,5],'ask_min':.60,'ask_max_exclusive':.95,'max_spread':.02,'recent_10m_min_volume':5,'quantity':5,'max_buy':.95,'signal_price_increment_cap':.01,'extra_cost_per_contract':.01,'min_edge_after_cost':.02,'probability_pseudocount':1,'selection':'Earliest qualifying horizon, then highest model edge; one city position and one entry per expiry','exit':'Hold to official settlement','capital_normalization':100,'max_contracts':25,'max_reserved_exposure':25,'daily_loss_budget_including_open':20,'calibrations':{}}
summary=read(P/'weather_model_calibration.json')
for city in CITIES:
 d=P/'replays'/city;bars={}
 for path in sorted(d.glob('*_source.json.gz')):
  for p in read(path)['live_data']['details']['timeseries']:
   if p['v'].get('temp_f') is not None:bars[int(p['t'])//1000]=(float(p['v']['temp_f']),p['v']['status'])
 byevent={m['event_ticker']:m for m in read(d/'markets.json')};one={}
 for h in (30,15,5):
  errs=[];ids=[]
  for e,m in sorted(byevent.items()):
   cl=ts(m['close_time'])
   if not START<=cl<SPLIT or ts(m['settlement_ts'])>=SPLIT:continue
   pred=forecast(bars,cl-h*60,cl,'persistence',10)
   if pred is not None:errs.append(float(m['expiration_value'])-pred);ids.append(e)
  expected=summary[f'{city}/persistence/lag10'][str(h)];assert len(errs)==expected['n'] and abs(statistics.mean(errs)-expected['mean_error'])<1e-10
  one[str(h)]={'errors_f':errs,'calibration_events':ids,'n':len(errs)}
 spec['calibrations'][city]=one
save(P/'weather_paper_spec.json',spec)
print('Frozen primary spec:',hashlib.sha256((P/'weather_paper_spec.json').read_bytes()).hexdigest())
