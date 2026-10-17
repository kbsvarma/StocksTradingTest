"""Render the measured results, with selected failures retained."""
import datetime as dt
import json
from pathlib import Path
import math
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

P=Path(__file__).resolve().parent
load=lambda n:json.loads((P/n).read_text())
cfg=load('configurations.json');choice=load('choices_frozen.json')['choices']
results=load('finalist_results.json');zs=load('z_only_sensitivity.json')
neighbors=load('neighbors.json');robust=load('robustness.json');valid=load('validation.json')
interaction=load('crypto_interaction.json')['results'];preserve=load('preservation.json')
assets=('NATGAS','COPPER','WTI','EURUSD','GOLD')
money=lambda x:f'${x:+.2f}'
plain=lambda x:f'${x:.2f}'
def metric(a,i,scenario='opening1',period='pooled'):
    return results[a][str(i)]['scenarios'][scenario][period]
def describe(c):
    text=f"standard z={c['z2']}; premium z={c['z3']}; {c['lo']}–{c['hi']} min; floors {c['floor2']*100:g}/{c['floor3']*100:g}c"
    if c['slope']:text+=f"; add {c['slope']}*log2(minutes/5) to both z thresholds, minimum .5"
    return text

lines=[
'NON-CRYPTO 15-MINUTE PARAMETER RESEARCH',
'Research date: October 7, 2026 ET. Research only; no live changes.',
'',
'RECOMMENDATION',
'Advance tuned COPPER as the strongest parameter-improvement candidate. Its',
'frozen selection passes the three chronological partitions, 2c cost and cap-cost',
'checks. A simpler copper z1.5-only change also works and is an implementation',
'alternative. EURUSD is the second tuning lead, with a time-dependent z threshold;',
'its short sample and sensitivity to later execution require particular attention.',
'Keep NATGAS at the unchanged z2/z3 design. Do not promote tuned WTI or GOLD',
'from this study: their selected rules still lose in the October3–7 check.',
'',
'All selected variants were frozen before their October3–7 results were calculated.',
'The dates themselves were already used for the prior asset-level study. This is',
'chronological reuse with leakage protection inside this search, NOT a pristine',
'holdout and NOT proof of live profit. Do not substitute a later winning neighbor',
'for a failed frozen selection and call it independent confirmation.',
'',
'SIZING, DATES AND EXECUTION',
'Five contracts per entry, one entry per market, $50 simulated initial cash.',
'Both lanes share20 contracts/$19 exposure/$10 daily risk INCLUDING open risk.',
'Keep original caps93.9c/97.5c, ask+1c effective limit, spread<=2c, minimumcash$5.',
'Chronological entries and actual exchange settlement timestamps release cash/risk.',
'Actual market outcomes settle P&L. Full input replay, not filtered old fill ledgers.',
'Primary figures include modeled taker fees plus1c extra cost PER CONTRACT.',
'Commodities: Sep11–Oct7UTC,27 calendar days. EURUSD only Sep23–Oct7,15days.',
'The five-asset frozen archive contains8,415settled markets. Rebuilt115,415valid',
'decision-minute features from61consecutive completed prices,60logreturns, exact',
'pre-open reference and population volatility. All65,974old features match exactly.',
'',
'LOOK AT THE SELECTED VARIANTS, INCLUDING THEIR FAILURES',
'Asset | original trades/net | selected trades W/L/net | selected drawdown | Oct3–7 net',
]
for a in assets:
    b=metric(a,0);i=choice[a]['robust'];v=metric(a,i);late=metric(a,i,period='confirmation')
    lines.append(f"{a} | {b['trades']} / {money(b['net'])} | {v['trades']} {v['wins']}W/{v['losses']}L / {money(v['net'])} | {plain(v['settled_drawdown'])} | {money(late['net'])}")
lines+=['','Selected rules:']
for a in assets:lines.append(f"{a}: rule{choice[a]['robust']}; {describe(cfg[choice[a]['robust']])}")
lines += [
'',
'COPPER: TWO CONCRETE OPTIONS',
'Simple option: original lane z>=1.5 instead of2; premium stays z>=3.',
'Everything else retains original85/93c floors and3–10min timing.',
'273trades,265W8L,+$55.1577 versus82/80W2L/+$12.0502; +191trades.',
'Drawdown increases$8.8948->$16.9309. October3–7:28wins/+$8.6087.',
'Full-period2c cost+$41.5077; cap-cost+$46.5807; delayed diagnostic+$34.7052.',
'It ranked second on the earlier selection score, before confirmation. Discussing',
'it as the simpler implementation choice is a post-result recommendation, not a',
'change to the frozen primary winner.',
'',
'Full selected option: standard z>=1.5,80–<95c; premium z>=2.5,93–<98c;',
'both1–10minutes; the same93.9/97.5c caps.350trades,340W10L,+$64.1603.',
'This adds268markets (+327%) and$52.1101 versus the unchanged copper replay.',
'Drawdown$14.8447; 2c-cost net+$44.6440; cap-cost+$53.6549;',
'whole-cent fee rounding+$62.22. October3–7:32trades31W1L,+$4.3433;',
'that last block remains+$2.7433 at2c and+$3.4030 at cap-cost.',
'Broader parameters are not free:10losses versus2, and a larger dollar drawdown.',
'One additional five-contract loss replacing a win would turn that32-trade later',
'block from+$4.34 to-$0.66. Full-period sample would remain positive.',
'',
'EURUSD: TIME-DEPENDENT THRESHOLD',
'Standard minimum z=max(.5,1.75+.5*log2(minutes_remaining/5)); premium minimum',
'z=max(.5,2.75+.5*log2(minutes_remaining/5));1–10min, unchanged price bands/caps.',
'At10min:2.25/3.25;5min:1.75/2.75;3min:1.38/2.38;1min:.59/1.59.',
'117trades115W2L,+$24.7231 versus16wins/+$4.7208, with$4.6758drawdown.',
'Three chronological blocks:+$5.1260,+$12.1516,+$7.4455.',
'2c-cost full+$18.8731/later+$5.2455; cap-cost full+$20.9593/later+$6.0344.',
'A simple fixed standardz1.75/premiumz3,3–10min gives32wins/+$10.1914,',
'but only6early-development trades and thus fails the declared15-trade screen.',
'Do not interpret32wins as evidence of a zero-loss strategy.',
'',
'Execution sensitivity matters: the60s diagnostic leaves only20entries/+$0.8838',
'and loses$2.1609 in the middle partition. It is OUTSIDE the35s live freshness',
'limit and is not a permissible execution schedule. The exact loss of profit at',
'a practical1–25s delay cannot be recovered from minute candles. The late rule',
'needs timely source publication and five-contract book evidence before relying',
'on the larger quote-based numbers.',
'',
'WHY THE OTHERS ARE NOT PROMOTED',
'NATGAS: simply lowering standardz2 to1.5 increases164->390trades but reduces',
'net$26.7370->$14.3005 and increases drawdown$8.0205->$34.3188. The robust',
'early-selected premium-only z2/7–12min rule gives$18.8709 total and loses',
'$0.8396 later. The higher-net unselected wider80c/1–14min rule gives$31.9896',
'but loses in the middle block and trails baseline under delayed execution.',
'The existing z2/z3 design remains the better-supported natural-gas choice.',
'',
'WTI: selected z1.5/z2.5, premiumfloor95c,3–10min increases103->362trades',
'and net$14.4247->$38.7212. Later62trades57W5L lose$0.6136, or$3.7136',
'at2c. Standardz1.75 alone gives191trades/+$23.2722 and later+$4.4665,',
'but loses in the middle block and in later delayed execution. It is not an',
'independently confirmed substitute for the failed selected rule.',
'',
'GOLD: adaptive selected rule improves net$5.7527->$18.8642 but still loses',
'$0.7132 later. Highest-frequency standardz1 gives927trades/+$56.3103 with',
'$30.5029drawdown;2c costs shrink full net to$1.5908 and raise drawdown to',
'$45.0391. This is the clearest example of why the largest headline profit is',
'not the preferred design. It remains the lowest-priority target here.',
'',
'ONE-FACTOR STANDARD-z SWEEP (premiumz3 and all other original rules unchanged)',
'Net afterfees+1c. These are full chronological replays, including portfolio caps.',
'z | NATGAS | COPPER | WTI | EURUSD | GOLD',
]
for z in ('0.75','1','1.25','1.5','1.75','2','2.25','2.5','3'):
    lines.append(z+' | '+' | '.join(money(zs[a][z]['opening1']['pooled']['net']) for a in assets))
lines+=['','FROZEN SELECTED RULES: ALL COST CHECKS','Asset | fees+1c | fees+2c | cap+1c | whole-cent fees+1c | 60s diagnostic']
for a in assets:
    i=choice[a]['robust'];lines.append(a+' | '+' | '.join(money(metric(a,i,sc)['net']) for sc in ('opening1','opening2','cap1','cent1','delay60')))
lines += [
'',
'SHARED CRYPTO INTERACTION: MATCHED Sep11–Oct2 ONLY',
'Crypto archive does not cover the Oct3–7 test. Never sum standalone profits.',
'Unchanged crypto alone334trades/+$69.5823/drawdown$4.6711.',
'Crypto+unchanged copper406/+$79.1770/drawdown$7.0425.',
'Crypto+tuned copper637/+$123.8525/drawdown$9.9052.',
'Tuning copper adds231nettrades/+$44.6755 versus adding unchanged copper;',
'versus crypto alone it adds303nettrades/+$54.2702.15old crypto markets are',
'displaced, so these results include competition for shared capacity.',
'The tuning increment remains+$32.6783 at2c and+$37.2978 atcap-cost.',
'These dates overlap rule selection; this is portfolio integration arithmetic,',
'not fresh confirmation. Onlyroute2crypto/commodities are combined. EURUSD',
'usesroute0 and is evaluated standalone; no cross-route cash pool is assumed.',
'Tie priority is standard lane first, then crypto assets before commodities',
'within each lane. No independent extra daily budget is granted to the add-on.',
'',
'SEARCH AND ROBUSTNESS',
'1,290predeclared configurations perasset,6,450development asset/rule screens.',
'Rules include both lanes, each lane alone,11time windows, z.75–5, adaptive',
'thresholds and80/90cstandard or95cpremium floor changes.1290 includes rules',
'that yield identical trades; it is not1290independent statistical experiments.',
'Commodities developmentSep11–24; selectionSep25–Oct2; checkOct3–7.',
'EURUSD developmentSep23–27; selectionSep28–Oct2; same final check.',
'Require15development and5selection trades; both early blocks positive under',
'2c/cap costs; rank by weakest stressed daily profit with drawdown penalty.',
'The minimum count is a search gate, not statistical sufficiency.',
'',
'Nearby copper z/time changes generally preserve full-period profitability;',
'lowering the selected standardz1.5 further to1.25 loses in the later block.',
'All evaluated neighboring EURUSD rules have positive full and later base net;',
'this does not resolve its execution-latency limitation.22post-selection',
'component/ablation cases are preserved as diagnostics, not new frozen winners.',
'',
'100paired random25%-unavailable-market simulations: copper improves on baseline',
'in100/100, incremental5th/median/95th percentiles+$24.77/+$36.77/+$50.01.',
'EURUSD100/100,+$10.16/+$13.80/+$19.51. Failure independence is an assumption;',
'adverse-selection-correlated failed IOC fills could be materially worse.',
'Unadjusted day-bootstrap intervals are descriptive only; they do not correct',
'for the6,450screens, prior asset selection, reused history or unobserved tails.',
'',
'VALIDATION AND LIMITS',
f"Five unchanged baseline P&L/trade/drawdown controls exactly reproduced. {valid['independent_z_checks']:,}independent NumPy z calculations match, maxerror {valid['max_difference']:.3g}.",
f"{valid['tick_cap_parities']:,}production tick-cap comparisons match. Future/forming prices excluded; missing minute rejects.",
'Original production entry_guard matches every baseline/selected trade identity',
'and P&L for all five assets, and the tuned three-commodity+crypto integration.',
'This validates arithmetic/risk, not an activated market adapter or FX routing.',
'Changed z/time/price gates are research rules; the production scanner is untouched.',
'',
'Historical opening bid/ask candles are price proxies, not five-contract depth',
'or guaranteed fills. Original publication lag and subsequent feed revision are',
'not fully reconstructed. Costs are P&L stresses, not permission to exceed caps.',
'The60s close-quote case cannot satisfy current freshness. No fresh live-execution',
'test was run for the tuned rules. Existing fixed-design live probe had zero',
'qualifying opportunities and cannot be repurposed as tuned-fill proof.',
'Dollar drawdown can exceed$10 over multiple days; the$10limit resets daily.',
'Continuous full-period paths differ from sums of separately restarted partitions',
'because cash, open risk and ET settlement-day P&L carry across UTC boundaries.',
'',
'PRESERVATION / REPRODUCTION',
'No orders, live configuration, funding, services, arm files or ledgers changed.',
f"Fiveproduction source hashes unchanged;{preserve['baseline_hashes_verified']}immutable checkpoint hashes verified.",
f"Live process remained PID3535704; dashboardHTTP{preserve['dashboard_http']}; LIVE/armed, no halt or error at verification.",
'Run study.py build, train, select, confirm, diagnostics in that order; preserve',
'the frozen-choice artifacts. Then validate.py, interaction.py, z_only.py and',
'ablation.py. Build refers to the immutable previous study raw-input bundle.',
'All code/results under /home/varma/kalshi_noncrypto_tuning_2026_10_07 on',
'192.168.3.136, compact copies in the same local study directory. Large input',
'features and full per-trade ledgers remain on the new server.',
'',
'Official documentation rechecked:',
'https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks',
'https://docs.kalshi.com/getting_started/fee_rounding',
]
prose='\n'.join(lines)+'\n'
for old,new in [('standardz','standard z'),('premiumz','premium z'),('floor80','floor 80'),
                ('minimumcash','minimum cash'),('Full-period2c','Full-period 2c'),
                ('old features','old features'),('Fiveproduction','Five production')]:
    prose=prose.replace(old,new)
# Keep data/code URLs and paths literal, while giving the narrative readable units.
prose='\n'.join(line if line.startswith(('https://','192.168.')) or '/home/' in line
    else re.sub(r'(?<=[0-9])(?=[A-Za-z])|(?<=[A-Za-z])(?=[0-9])',' ',line)
    for line in prose.splitlines())+'\n'
prose=prose.replace('log 2','log2').replace('premiumfloor','premium floor').replace('Standardz','Standard z')
prose=re.sub(r'\$([+-])([0-9])',r'\1$\2',prose)
prose=re.sub(r'(?<=[A-Za-z])(?=\$|[+-]\$)',' ',prose)
(P/'REPORT.txt').write_text(prose)

spec=dict(status='RESEARCH_ONLY_NOT_ACTIVATED',date_et='2026-10-07',
    first_tuning_candidate={'asset':'COPPER','configuration':cfg[1239]},
    simpler_copper_alternative={'configuration':cfg[560],'selection_note':'Second earlier ranking; simplicity recommendation discussed after later diagnostics.'},
    second_tuning_candidate={'asset':'EURUSD','configuration':cfg[1183],'limitation':'Short history; late execution-price sensitivity unresolved.'},
    natural_gas={'configuration':cfg[0],'decision':'Retain unchanged; selected tuned variant failed later check.'},
    not_promoted=['WTI','GOLD'],size=5,shared_max_contracts=20,shared_max_risk=19,shared_daily_risk=10,
    no_budget_or_live_changes=True,confirmation_note='Previously observed asset history; no untouched holdout claim.')
(P/'candidate_spec.json').write_text(json.dumps(spec,indent=2)+'\n')

plt.rcParams.update({'font.family':'DejaVu Sans','axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(2,3,figsize=(13,7.5),layout='constrained')
for ax,a in zip(axes.flat,assets):
    values=[float(k) for k in zs[a]]
    base=[zs[a][str(z) if z%1 else str(int(z))]['opening1']['pooled']['net'] for z in values]
    cost=[zs[a][str(z) if z%1 else str(int(z))]['opening2']['pooled']['net'] for z in values]
    ax.plot(values,base,'o-',color='#196b65',label='Fees + 1c cost')
    ax.plot(values,cost,'s--',color='#ac633c',label='Fees + 2c cost')
    ax.axhline(0,color='#999',lw=.8);ax.axvline(2,color='#aaa',ls=':',lw=1)
    ax.set_title(a,fontweight='bold');ax.set_xlabel('Standard-lane minimum z');ax.set_ylabel('Modeled net ($)')
    ax.grid(alpha=.15);ax.set_xticks([.75,1.5,2,2.5,3,4])
axes.flat[-1].axis('off')
axes.flat[-1].text(.02,.85,'What changes here',size=15,weight='bold')
axes.flat[-1].text(.02,.70,'Only the standard-lane z threshold.\nPremium stays z ≥ 3; both stay 3–10 min.\nFive contracts, same prices and risk caps.\nSep 11–Oct 7; EUR/USD Sep 23–Oct 7.\n\nCopper benefits near z = 1.5.\nNatural gas peaks around the original z = 2.\nLower z does not improve every market.\n\nQuote-based historical simulations;\nfive-contract fills are not verified.',size=11,va='top',linespacing=1.5)
handles,labels=axes.flat[0].get_legend_handles_labels();axes.flat[0].legend(handles,labels,fontsize=9)
fig.suptitle('Changing z: more trades can mean less profit',fontsize=18,fontweight='bold')
fig.savefig(P/'z_sensitivity.png',dpi=170,facecolor='white')
fig.savefig(P/'z_sensitivity.pdf',facecolor='white');plt.close(fig)
print('Wrote REPORT.txt, candidate_spec.json, z_sensitivity.png/pdf')
