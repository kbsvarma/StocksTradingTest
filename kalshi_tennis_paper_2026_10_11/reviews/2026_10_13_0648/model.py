"""Frozen scoring model, extracted unchanged from the research checkpoint."""
from functools import lru_cache

class Model:
 def __init__(self,pa,pb):self.pa=pa;self.pb=pb
 @lru_cache(None)
 def game(self,a,b,s):
  q=self.pa if s==0 else 1-self.pb
  if max(a,b)>=4 and abs(a-b)>=2:return float(a>b)
  if min(a,b)>=3:
   d=q*q/(q*q+(1-q)**2)
   return d if a==b else q+(1-q)*d if a>b else q*d
  return q*self.game(a+1,b,s)+(1-q)*self.game(a,b+1,s)
 @lru_cache(None)
 def tb(self,a,b,first):
  if max(a,b)>=7 and abs(a-b)>=2:return float(a>b)
  n=a+b;s=first if n==0 or ((n-1)//2)%2==1 else 1-first
  q=self.pa if s==0 else 1-self.pb
  if min(a,b)>=6:
   x=self.pa*(1-self.pb);y=(1-self.pa)*self.pb;d=x/(x+y)
   return d if a==b else q+(1-q)*d if a>b else q*d
  return q*self.tb(a+1,b,first)+(1-q)*self.tb(a,b+1,first)
 @lru_cache(None)
 def setdist(self,a,b,s):
  if max(a,b)>=6 and abs(a-b)>=2:
   out=[0.]*4;out[s if a>b else 2+s]=1.;return tuple(out)
  if a==6 and b==6:
   p=self.tb(0,0,s);out=[0.]*4;out[1-s]=p;out[3-s]=1-p;return tuple(out)
  q=self.game(0,0,s);x=self.setdist(a+1,b,1-s);y=self.setdist(a,b+1,1-s)
  return tuple(q*u+(1-q)*v for u,v in zip(x,y))
 @lru_cache(None)
 def match(self,a,b,s):
  if a>=2:return 1.
  if b>=2:return 0.
  d=self.setdist(0,0,s)
  return sum(d[i]*self.match(a+1,b,i) for i in (0,1))+sum(d[2+i]*self.match(a,b+1,i) for i in (0,1))
 def current(self,sa,sb,ga,gb,pa,pb,s):
  if sa>=2:return 1.
  if sb>=2:return 0.
  q=self.game(pa,pb,s);x=self.setdist(ga+1,gb,1-s);y=self.setdist(ga,gb+1,1-s);d=[q*u+(1-q)*v for u,v in zip(x,y)]
  return sum(d[i]*self.match(sa+1,sb,i) for i in (0,1))+sum(d[2+i]*self.match(sa,sb+1,i) for i in (0,1))
 def clear(self):
  # Method caches include self; clear between fitted models to bound memory.
  for f in (self.game,self.tb,self.setdist,self.match):f.cache_clear()

def fit(base,target):
 lo,hi=-.12,.12
 for _ in range(15):
  d=(lo+hi)/2;m=Model(base+d,base-d);p=(m.match(0,0,0)+m.match(0,0,1))/2;m.clear()
  if p<target:lo=d
  else:hi=d
 d=(lo+hi)/2;return Model(base+d,base-d)
