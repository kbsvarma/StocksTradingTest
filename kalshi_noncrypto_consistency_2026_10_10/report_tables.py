from pathlib import Path
import json
P=Path(__file__).resolve().parent
lines=['NON-CRYPTO CONSISTENCY STUDY — ALL COMPARISONS','All dollar results: five contracts/entry, current taker fees plus1c per contract unless stated.','Historical candle execution proxies; not verified five-contract fills.','Standalone rows do not add to a shared portfolio.','Segments: financial Aug1–Sep4 / Sep5–Oct9; sports and teachers Aug15–Sep11 / Sep12–Oct9; extended sports July1–Aug14 / Aug15–Oct9. Extended segments have unequal durations.','']
for kind in ('financial','sports','extended_sports','teachers'):
 path=P/(kind+'_results.json')
 if not path.exists():continue
 rs=json.load(open(path));lines+=[kind.upper(),'Design | entries | W/L/scalar | net | drawdown | positive/active weeks | first half | second half | fee+2c net | delay60 net | cap net']
 for key,r in rs.items():
  if not key.endswith('/base'):continue
  pref=key[:-4];lines.append(f"{key[:-5]} | {r['trades']} | {r['wins']}/{r['losses']}/{r['scalar']} | {r['net']:+.4f} | {r['drawdown']:.4f} | {r['positive_weeks']}/{r['active_weeks']} | {r['first_half']['net']:+.4f} | {r['second_half']['net']:+.4f} | {rs[pref+'cost2c']['net']:+.4f} | {rs[pref+'delay60']['net']:+.4f} | {rs[pref+'cap']['net']:+.4f}")
 lines+=['','Weekly base results (entry-date attribution, actual settlement cash release):']
 for key,r in rs.items():
  if key.endswith('/base'):lines.append(key+' '+json.dumps(r['weekly']))
 lines+=['']
(P/'APPENDIX.txt').write_text('\n'.join(lines)+'\n')
