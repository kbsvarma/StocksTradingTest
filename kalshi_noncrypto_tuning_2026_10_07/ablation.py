"""Post-selection attribution checks; do not replace frozen winners."""
import study as s

def main():
    markets=s.pickle.loads((s.P/'features.pickle').read_bytes())
    cases=[]
    for z2 in (1.5,2):
        for z3 in (2.5,3):
            for floor in (.80,.85):
                for lo in (1,3):cases.append(('COPPER',dict(s.CONFIG[0],z2=z2,z3=z3,floor2=floor,lo=lo)))
    for slope in (0,.5):
        for lo in (1,2,3):cases.append(('EURUSD',dict(s.CONFIG[0],z2=1.75,z3=2.75,slope=slope,lo=lo)))
    results=[dict(asset=a,config=c,scenarios={}) for a,c in cases]
    for name,mode,stress in [('opening1','opening',.01),('opening2','opening',.02),('cap1','cap_cost',.01)]:
        rows=s.pool(markets,mode)
        for x in results:
            x['scenarios'][name]={p:s.evaluate(rows,x['config'],x['asset'],p,stress) for p in ('development','selection','confirmation','pooled')}
    s.save('ablation.json',dict(note='Post-selection diagnostic for attribution/simplicity, not an independent confirmation or replacement winner.',results=results))
    print('Ablation cases',len(results),flush=True)

if __name__=='__main__':main()
