"""Audit frozen NFL ledger and weekend opportunity counts; no parameter search."""
import json,gzip,datetime,math,statistics,collections
from decimal import Decimal,ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ROOT=P.parent;ET=ZoneInfo('America/New_York')
def read(p):
    return json.load(gzip.open(p,'rt')) if str(p).endswith('.gz') else json.loads(Path(p).read_text())
def dt(t):return datetime.datetime.fromtimestamp(t,ET)
def iso(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
def metrics(rr):
    nets=[r['net'] for r in sorted(rr,key=lambda r:(r['settled'],r['event']))];equity=peak=dd=0
    for v in nets:equity+=v;peak=max(equity,peak);dd=max(dd,peak-equity)
    return dict(trades=len(rr),wins=sum(r['won'] is True for r in rr),losses=sum(r['won'] is False for r in rr),net=round(sum(nets),4),drawdown=round(dd,4))
def main():
    ll=read(ROOT/'kalshi_new100_2026_10_16/nfl/ledgers.json.gz');rr=ll['spread2/base/combined']
    checks=0
    for r in rr:
        p=Decimal(str(round(r['entry'],8)));f=(Decimal('.07')*Decimal(str(r['fee_multiplier']))*5*p*(1-p)).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
        net=5*(Decimal(str(r['payout']))-p)-f-Decimal('.05')
        assert abs(float(net)-r['net'])<1e-7
        assert abs(float(f)-r['fee'])<1e-7
        assert .90-1e-8<=r['ask']<.95 and r['ask']-r['bid']<=.02000001
        assert r['entry']<=r['cap']+1e-8 and r['entry']>=r['ask']-1e-8
        assert r['period']==4 and 0<max(r['clock'],r['decision_clock'])<=300
        assert r['score_wall']<=r['entered']-r['source_delay'] and r['score_age']<=120
        assert r['entered']<r['game_end'] and r['entered']<r['settled']
        checks+=1
    assert len({r['game_owner'] for r in rr})==len(rr)
    this=[r for r in rr if r['schedule_start']>=iso('2026-09-01T00:00:00Z').timestamp()]
    cohorts={'all':rr,'previous_season':[r for r in rr if r not in this],'current_season':this,'fresh_extension':ll['spread2/base/fresh_extension']}
    out=dict(cohorts={k:metrics(v) for k,v in cohorts.items()},checked_trades=checks,
             stress={k.split('/')[1]:metrics(v) for k,v in ll.items() if k.startswith('spread2/') and k.endswith('/combined')},
             current_stress={k.split('/')[1]:metrics([r for r in v if r['schedule_start']>=iso('2026-09-01T00:00:00Z').timestamp()]) for k,v in ll.items() if k.startswith('spread2/') and k.endswith('/combined')})
    byday=collections.defaultdict(list)
    for r in this:byday[str(dt(r['schedule_start']).date())].append(r)
    out['current_by_game_date']={k:metrics(v) for k,v in sorted(byday.items())}
    out['current_sundays']={k:v for k,v in out['current_by_game_date'].items() if datetime.date.fromisoformat(k).weekday()==6}
    out['current_sunday_mean']=statistics.mean(v['trades'] for v in out['current_sundays'].values())
    base_cov=read(ROOT/'kalshi_nfl_nhl_frequency_2026_10_10/game_coverage.json')
    games={k:v for k,v in base_cov.items() if v['series']=='KXNFLGAME' and v['season_type']=='REG' and iso(v['start'])>=iso('2026-09-01T00:00:00Z')}
    for row in read(ROOT/'kalshi_new100_2026_10_16/nfl/coverage.json'):
        if row['status']=='ok':games[row['event']]=dict(start=dt(row['start']).isoformat(),season_type='REG',**row['coverage'])
    out['current_game_coverage']=dict(total=len(games),with_score_states=sum(bool(v.get('games_with_play_states')) for v in games.values()),filled_fraction=len(this)/len(games))
    current=read(P/'current.json') if (P/'current.json').exists() else None
    if current:
        out['upcoming_count']=len(current['games']);out['upcoming_expectation_if_same_rate']=len(current['games'])*len(this)/len(games)
    out['losses']=[]
    for r in rr:
        if r['won']:continue
        o=dict(r)
        if r['event']=='KXNFLGAME-26OCT11BALATL':
            x=read(ROOT/'kalshi_new100_2026_10_16/nfl/external/summary_401872993.json')['response']
            o['final_score']={c['team']['displayName']:c['score'] for c in x['header']['competitions'][0]['competitors']}
            plays=x.get('plays') or [p for d in x.get('drives',{}).get('previous',[]) for p in d.get('plays',[])]
            chosen=[]
            for p in plays:
                if (p.get('scoringPlay') and p.get('period',{}).get('number',0)>=4) or p.get('id')==r['source_play_id']:
                    chosen.append({k:p.get(k) for k in ('id','wallclock','modified','period','clock','homeScore','awayScore','text')})
            o['selected_plays']=chosen
        out['losses'].append(o)
    out['winning_mean']=statistics.mean(r['net'] for r in rr if r['won'])
    out['losing_mean']=statistics.mean(r['net'] for r in rr if not r['won'])
    out['mean_break_even']=statistics.mean(r['entry']+r['fee']/5+.01 for r in rr)
    n=len(rr);p=sum(r['won'] for r in rr)/n;z=1.96
    out['descriptive_wilson_lower']=(p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
    wins=sorted(r['net'] for r in rr if r['won']);out['five_bet_examples']=dict(all_win=5*statistics.mean(wins),four_win_one_loss=4*statistics.mean(wins)+out['losing_mean'])
    (P/'audit.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='losses'},indent=2))
if __name__=='__main__':main()
