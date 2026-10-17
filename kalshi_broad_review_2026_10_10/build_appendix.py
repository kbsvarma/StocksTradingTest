from common import *
from pathlib import Path
replays=read(P/'replay_results.json');models=read(P/'weather_model_results.json');fx=read(P/'noncrypto_results.json');perps=read(P/'perps_results.json');cats=read(P/'results/category_screen.json');out=[]
out+=['KALSHI BROAD REVIEW — FULL RESULTS, October 10, 2026','Research simulations, not executed trades. See REPORT.txt for decisions and limitations.','All prediction tables use five contracts, modeled taker fees plus 1c/contract.','2c replaces 1c. Delay uses a later quote and can select DIFFERENT trades.','W/L denotes contract settlement; a winning contract can still lose money after costs.','Each strategy is a standalone risk simulation. Do not add alternative strategies.','', 'SPORTS / WEATHER PRICE-RULE REPLICATION, Sep26–Oct9 ET expiry dates','Family/profile | trades | W/L | net | DD | 2c net | delayed net | first-week net | second-week net']
for k,v in sorted(replays.items()):
 if k.startswith('ALL/') or not k.endswith('/fees1c'):continue
 prefix=k[:-len('fees1c')];a=v['full']['portfolio'];b=replays[prefix+'fees2c']['full']['portfolio'];c=replays[prefix+'delay1c']['full']['portfolio']
 out.append(f"{prefix[:-1]} | {a['trades']} | {a['wins']}/{a['losses']} | {a['net']:+.4f} | {a['drawdown']:.4f} | {b['net']:+.4f} | {c['net']:+.4f} | {v['first_week']['portfolio']['net']:+.4f} | {v['second_week']['portfolio']['net']:+.4f}")
out+=['','Common portfolio across ALL cities/leagues for each rule (not sum of rows):','Profile | trades | W/L | net | DD | 2c net | delayed net']
for k,v in sorted(replays.items()):
 if not k.startswith('ALL/') or not k.endswith('/fees1c'):continue
 prefix=k[:-len('fees1c')];a=v['full']['portfolio'];b=replays[prefix+'fees2c']['full']['portfolio'];c=replays[prefix+'delay1c']['full']['portfolio'];out.append(f"{prefix[:-1]} | {a['trades']} | {a['wins']}/{a['losses']} | {a['net']:+.4f} | {a['drawdown']:.4f} | {b['net']:+.4f} | {c['net']:+.4f}")
out+=['','WEATHER SOURCE MODELS, calibration Sep26–Oct2, evaluation Oct3–9 ET','Ten-minute source lag primary; six-minute lag sensitivity. City/model selection','after examining this table is exploratory, not a second independent holdout.','City/model/lag | trades | W/L | net | DD | 2c net | delayed trades/net | unc. trades/net']
for k,v in sorted(models.items()):
 if not k.endswith('/fees1c'):continue
 prefix=k[:-len('fees1c')];a=v['portfolio'];b=models[prefix+'fees2c']['portfolio'];c=models[prefix+'delay1c']['portfolio'];u=v['unconstrained'];out.append(f"{prefix[:-1]} | {a['trades']} | {a['wins']}/{a['losses']} | {a['net']:+.4f} | {a['drawdown']:.4f} | {b['net']:+.4f} | {c['trades']}/{c['net']:+.4f} | {u['trades']}/{u['net']:+.4f}")
out+=['','FX EXTENSION, available Oct7 17:30–Oct9 17:00 ET (182 windows each)','Asset/profile | trades | W/L | net | 2c net | cap net | delayed trades/net | newest-slice trades/net']
for k,v in sorted(fx.items()):
 a=v['plus1c']['periods']['full'];b=v['plus2c']['periods']['full'];c=v['cap_plus1c']['periods']['full'];d=v['delay60_plus1c']['periods']['full'];new=v['plus1c']['periods']['new_since_oct9_11ET'];out.append(f"{k} | {a['trades']} | {a['wins']}/{a['losses']} | {a['net']:+.4f} | {b['net']:+.4f} | {c['net']:+.4f} | {d['trades']}/{d['net']:+.4f} | {new['trades']}/{new['net']:+.4f}")
out+=['','PERPETUAL REPLICATION, entries Sep27 00ET–Oct10 00ET','Max $50 entry notional AND $1 modeled stop risk; whole contracts. These','normalization amounts do not constrain the user to a particular capital budget.','One-hour delayed execution, effective account taker fees, observed funding,','bid/ask crossing and 1bp/tick-floor stress per side. 5bp replaces 1bp.','Wins here are positive net trades. DD includes observed hourly marks.','Product/rule | trades | wins | net | 5bp net | early net | Oct3+ net | DD | net excluding best3']
for a in sorted(perps,key=lambda x:x['name']):out.append(f"{a['name']} | {a['trades']} | {a['wins']} | {a['net']:+.4f} | {a['stress5']:+.4f} | {a['early']:+.4f} | {a['recent']:+.4f} | {a['drawdown']:.4f} | {a['without_best3']:+.4f}")
out+=['','CATALOG COVERAGE','Category | definitions | open series | events | active markets | tight five-contract markets | reported 24h contracts']
for k,v in sorted(cats.items()):out.append(f"{k} | {v['catalog_series']} | {v['open_series']} | {v['events']} | {v['markets']} | {v['tight_five_contract_markets']} | {v['reported_open_24h_contracts']:.2f}")
(P/'APPENDIX.txt').write_text('\n'.join(out)+'\n')
