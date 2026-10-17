"""Offline complementary-side test. Read PASS2.md for predeclared scope."""
import collections, copy, json, pickle
import study as s
from strategy import Rule

RULES=[Rule('under_basic3',.10,.45,-100,3,10,.45,'basic',.03),
       Rule('under_basic5',.10,.45,-100,3,10,.45,'basic',.05),
       Rule('under_rich3',.10,.45,-100,3,10,.45,'rich',.03),
       Rule('under_rich5',.10,.45,-100,3,10,.45,'rich',.05),
       Rule('under_adaptive8',.10,.45,-100,3,10,.45,'adaptive',.08),
       Rule('under_official8',.10,.45,-100,3,10,.45,'official_average',.08)]

def complement(f):
    out=dict(f,side=-f['side'],bid=1-f['ask'],ask=1-f['bid'],z=-f['z'])
    for k in ('zofficial','zaverage'):
        if k in out:out[k]=-out[k]
    for k in ('pbasic','prich'):
        if k in out:out[k]=1-out[k]
    return out

if __name__=='__main__':
    markets=pickle.loads((s.P/'features.pickle').read_bytes())
    reversed_markets=[dict(m,features=[complement(f) for f in m['features']]) for m in markets]
    s.RULES=RULES
    books=s.old.load_books()
    for scenario,bb,delay in [('quote',None,0),('book',books,0),('book_delay10',books,10),('quote_delay60',None,60)]:
        base,_=s.old.replay(markets,None,bb,quantity=5,delay=delay,all_signals=True,rules=s.BASE_RULES)
        candidates,counts=s.candidate_replay(reversed_markets,bb,delay)
        s.summarize_scenario(markets,{**base,**candidates},'pass2_'+scenario)
        s.save('pass2_'+scenario+'_signal_counts.json',counts)
