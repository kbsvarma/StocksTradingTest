"""Pure weather signal, reconstruction and paper execution calculations."""
import datetime as dt
import hashlib
import json
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo

CITIES = {'KXTEMPMIAH':'miami','KXTEMPCHIHS':'chicago',
          'KXTEMPLAXHS':'la-coastal','KXTEMPNYCHS':'nyc'}
SCENARIOS = ('early','delay60','canonical')
def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x): return hashlib.sha256(canonical(x).encode()).hexdigest()
def ts(x): return dt.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
def day(t): return dt.datetime.fromtimestamp(t,ZoneInfo('America/New_York')).date().isoformat()
def fee(price,qty=5,mult=1):
    p=Decimal(str(price))
    return float((Decimal('.07')*Decimal(str(qty))*Decimal(str(mult))*p*(1-p)).quantize(Decimal('.0001'),rounding=ROUND_CEILING))

def reconstruct(point,configs,now,check_qc=True):
    available=[c for c in configs if c['published_at_ms']/1000<=now and c['effective_at_ms']<=point['t']]
    if not available:return None,'no_known_configuration'
    cfg=max(available,key=lambda c:c['effective_at_ms'])
    roster={s['station_id']:s for s in cfg['stations']}
    stations={s['station_id']:s for s in point.get('stations',[])}
    if set(roster)!=set(stations):return None,'incomplete_roster'
    bad={'rejected','outlier','stale','invalid','hard_range','excluded','missing','spike','bad'}
    for s in stations.values():
        if s.get('source')!='hf_asos' or s.get('temp_f') is None or s.get('received_at_ms') is None:return None,'missing_primary'
        if not math.isfinite(float(s['temp_f'])) or not -40<=float(s['temp_f'])<=122:return None,'invalid_temperature'
        if s['received_at_ms']/1000>now+2:return None,'future_vendor_clock'
        if check_qc and (str(s.get('code','')).lower() in bad or str(s.get('primary_code','')).lower() in bad):return None,'explicit_qc_rejection'
    weight=sum(float(c['weight']) for c in roster.values())
    if weight<=0:return None,'invalid_weights'
    celsius=sum(float(c['weight'])*((float(stations[k]['temp_f'])-32)*5/9-c['offset_c']+cfg['city_reference_c']) for k,c in roster.items())/weight
    return {'forecast':round(celsius*9/5+32,2),'source_t':point['t']/1000,
            'vendor_received':max(s['received_at_ms'] for s in stations.values())/1000,
            'config_version':cfg['config_version'],'point_hash':digest(point),
            'raw_hash':digest(point.get('stations',[])),'pending':point.get('v') is None},None

def book(payload,side='yes',cap=1,qty=5):
    ob=payload.get('orderbook_fp')
    if not isinstance(ob,dict):return None,'missing_book'
    try:
        own=[(float(p),float(q)) for p,q in ob.get(side+'_dollars',[])]
        other='no' if side=='yes' else 'yes'
        asks=sorted((round(1-float(p),4),float(q)) for p,q in ob.get(other+'_dollars',[]) if float(q)>0)
        if any(not math.isfinite(p+q) or not 0<=p<=1 or q<0 for p,q in own+asks):return None,'invalid_book'
    except (TypeError,ValueError):return None,'invalid_book'
    bids=[p for p,q in own if q>0]
    if not bids or not asks:return None,'empty_book'
    bid=max(bids);ask=asks[0][0]
    if bid>ask+1e-9:return None,'crossed_book'
    remaining=qty;cost=0;depth=0;levels=[]
    for price,size in asks:
        if price>cap+1e-9:break
        depth+=size;take=min(size,remaining)
        if take>0:cost+=take*price;remaining-=take;levels.append([price,take])
    out={'bid':bid,'ask':ask,'depth':depth,'vwap':cost/qty if remaining<1e-8 else None,'levels':levels}
    return out, None if remaining<1e-8 else 'insufficient_depth_inside_cap'

def cap_price(m,ask,cfg):
    limit=min(ask+cfg['cap_increment'],cfg['max_cap'])
    if m.get('price_ranges'):
        valid=[]
        for r in m['price_ranges']:
            lo=Decimal(str(r['start']));hi=Decimal(str(r['end']));step=Decimal(str(r['step']))
            if step<=0:continue
            top=min(Decimal(str(limit)),hi)
            if top>=lo:valid.append(lo+((top-lo)/step).to_integral_value(rounding=ROUND_FLOOR)*step)
        return float(max(valid)) if valid else None
    if m.get('price_level_structure')=='linear_cent':return math.floor(limit*100+1e-8)/100
    return None

def choose(markets,books,source,training,now,volumes,cfg):
    candidates=[];counts={}
    def skip(r):counts[r]=counts.get(r,0)+1
    for m in markets:
        close=ts(m['close_time']);remaining=close-now
        if not cfg['min_remaining']<=remaining<=cfg['max_remaining']:skip('outside_window');continue
        if m.get('status')!='active' or m.get('strike_type')!='greater':skip('market_type');continue
        if m['ticker'] not in books:skip('missing_book');continue
        if now-source['source_t']>cfg['max_source_age']:skip('source_age');continue
        h=(close-source['source_t'])/60
        if abs(h-round(h))>1e-8:skip('nonminute_expiry');continue
        errors=training['errors'].get(m['event_ticker'].split('-')[0],{}).get(str(int(h)),[])
        if len(errors)<cfg['min_training']:skip('training_sample');continue
        if volumes.get(m['ticker'],0)<cfg['min_volume10']:skip('volume');continue
        q,_=book(books[m['ticker']],'yes',qty=cfg['contracts'])
        if not q:skip('invalid_book');continue
        py=(1+sum(source['forecast']+e>float(m['floor_strike']) for e in errors))/(len(errors)+2)
        blended=(1-cfg['market_weight'])*py+cfg['market_weight']*(q['bid']+q['ask'])/2
        for side,prob in [('yes',blended),('no',1-blended)]:
            sideq,_=book(books[m['ticker']],side,qty=cfg['contracts'])
            if not sideq or sideq['ask']-sideq['bid']>cfg['max_spread']+1e-8 or not cfg['min_ask']<=sideq['ask']<cfg['max_ask']:skip('price_spread');continue
            edge=prob-sideq['ask']-fee(sideq['ask'],cfg['contracts'])/cfg['contracts']-cfg['friction']['early']
            if edge+1e-10<cfg['edge']:skip('edge');continue
            cap=cap_price(m,sideq['ask'],cfg)
            if cap is None or cap<sideq['ask']:skip('tick_ladder');continue
            candidates.append({'event':m['event_ticker'],'series':m['event_ticker'].split('-')[0],
                'ticker':m['ticker'],'side':side,'ask':sideq['ask'],'bid':sideq['bid'],'cap':cap,
                'probability':prob,'raw_probability_yes':py,'edge':edge,'close':close,
                'horizon':int(h),'training_n':len(errors),'source':source,'strike':float(m['floor_strike'])})
    return (max(candidates,key=lambda r:(r['edge'],-r['ask'],r['ticker'],r['side'])) if candidates else None),counts

def payout(m,side):
    if not m.get('settlement_ts'):return None
    if m.get('result') in ('yes','no'):return float(m['result']==side)
    try:
        v=float(m['settlement_value_dollars'])
        return (v if side=='yes' else 1-v) if 0<=v<=1 else None
    except (KeyError,ValueError,TypeError):return None
