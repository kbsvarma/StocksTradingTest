from evaluate import *
R=read(P/'results.json');L=read(P/'ledgers.json.gz');C=read(P/'candidates.json.gz');CAL=read(P/'calibration.json')
primary='w7_lag10/base/cost1c/release0/lock1';names={'KXTEMPMIAH':'Miami','KXTEMPCHIHS':'Chicago','KXTEMPLAXHS':'LA Coastal'}
def brief(m):return {k:m.get(k) for k in ['trades','wins','losses','net','drawdown','win_rate','break_even','mean_model_probability','trades_per_day','worst_day','without_best_day','without_best3','day_bootstrap95','weeks']}
summary={'primary':{c:brief(m) for c,m in R[primary]['cities'].items()},'earlier':{},'selection_week':{},'same_three_weeks':{},'matched_14day':{},'scenarios':{}}
for c in CITIES:
    rs=[r for r in L[primary] if r['asset']==c]
    summary['earlier'][c]=desc([r for r in rs if r['t']<SELECTION],SELECTION)
    summary['selection_week'][c]=desc([r for r in rs if r['t']>=SELECTION])
    summary['same_three_weeks'][c]=desc([r for r in rs if r['t']>=ts('2026-09-19T04:00:00Z')])
    weeks={ts(key.rsplit('/',1)[1]+'T04:00:00Z') for key,info in CAL.items() if key.startswith(c+'/w14_lag10/') and all(v['eligible'] for v in info.values())}
    summary['matched_14day'][c]={'weeks':sorted(day(w) for w in weeks)}
    for variant in ('w7_lag10','w14_lag10'):
        rs=[r for r in C[variant+'/base'] if r['asset']==c and r['calibration_end'] in weeks]
        summary['matched_14day'][c][variant]=desc(port(rs)[0])
for key,value in R.items():summary['scenarios'][key]={c:{k:m.get(k) for k in ['trades','wins','losses','net','drawdown']} for c,m in value['cities'].items()}
save(P/'summary.json',summary)
lines=['WEATHER EXTENDED VALIDATION: ALL PREDECLARED SCENARIOS','Five contracts; USD net after fees and named extra costs.','Each row is a continuous common three-city portfolio; city attribution sums to total.','Decision weeks use only earlier settled labels; not prospective evidence.','']
for key,v in R.items():
    lines.append(key)
    for c,m in {'ALL':v['ALL'],**v['cities']}.items():lines.append(f"  {names.get(c,c):12} n={m['trades']:3} W/L={m['wins']:3}/{m['losses']:<3} net={m['net']:+10.4f} DD={m['drawdown']:9.4f}")
    lines.append('  portfolio '+str(v['state']));lines.append('')
lines+=['INDIVIDUAL CITY PORTFOLIOS: separate starting cash and risk budget per city','These totals need not add to the common portfolio because shared risk can block entries.','']
for key,v in R.items():
    lines.append(key)
    for c,m in v['standalone_cities'].items():lines.append(f"  {names[c]:12} n={m['trades']:3} W/L={m['wins']:3}/{m['losses']:<3} net={m['net']:+10.4f} DD={m['drawdown']:9.4f}")
    lines.append('')
lines+=['PRIMARY BY DECISION WEEK (settlement-based P&L assigned to its entry week)']
for c,m in R[primary]['cities'].items():
    lines.append(names[c])
    for w,stats in m.get('weeks',{}).items():lines.append(f"  {w}: {stats['trades']} trades {stats['wins']}W/{stats['losses']}L net {stats['net']:+.4f}")
lines+=['','MATCHED CALIBRATION LOOKBACK COMPARISON; dates matched, standalone city portfolios']
for c,v in summary['matched_14day'].items():
    lines.append(names[c]+' '+str(v['weeks']))
    for variant in ('w7_lag10','w14_lag10'):lines.append('  '+variant+' '+str(brief(v[variant])))
(P/'APPENDIX.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps({'primary':summary['primary'],'earlier':{c:brief(v) for c,v in summary['earlier'].items()},'matched_14day':{c:{k:brief(vv) if isinstance(vv,dict) else vv for k,vv in v.items()} for c,v in summary['matched_14day'].items()}},indent=2))
