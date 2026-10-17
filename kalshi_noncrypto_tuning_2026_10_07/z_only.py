"""Predeclared one-factor z sweep: no winner replaced after confirmation."""
import study as s

def main():
    markets=s.pickle.loads((s.P/'features.pickle').read_bytes())
    out={a:{} for a in s.ASSETS}
    for name,mode,stress,precision in s.SCENARIOS:
        rows=s.pool(markets,mode,precision)
        for a in s.ASSETS:
            for z in s.ZLOW:
                c=dict(s.CONFIG[0],z2=z)
                out[a].setdefault(str(z),{})[name]={p:s.evaluate(rows,c,a,p,stress) for p in ('development','selection','confirmation','pooled')}
    s.save('z_only_sensitivity.json',out)
    for a in s.ASSETS:
        for z in ('1.5','1.75','2'):
            print('Z ONLY',a,z,[(k,v['pooled']['net'],v['confirmation']['net']) for k,v in out[a][z].items()],flush=True)

if __name__=='__main__':main()
