"""Pure protected-profit decision state. Does not place or simulate order fills."""
from dataclasses import dataclass
import math

@dataclass
class Protection:
    entry:float
    stop:float
    safe:float
    ideal:float
    buffer:float=0
    trailing:bool=False
    armed:int=0
    peak:float=0
    pending:str|None=None
    @classmethod
    def from_config(cls,entry,rem,c):
        scale=max(.7,min(1.4,math.sqrt(rem/7))) if c['mode']==3 else 1
        return cls(entry,c['stop'] if c['mode']==1 else entry-c['stop']*scale,entry+c['safe']*scale,entry+c['ideal']*scale,c['buffer']*scale,c['mode']==4,peak=entry)
    def observe(self,bid):
        if self.pending:return self.pending
        if bid is None or not math.isfinite(bid) or not 0<=bid<=1:return None
        protect=(self.ideal if self.armed==2 else self.safe)-self.buffer
        if self.trailing and self.armed==2:protect=max(protect,self.peak-.06)
        if bid<=self.stop+1e-8:self.pending='hard_stop'
        elif self.armed and bid<=protect+1e-8:self.pending='safe_reversal' if self.armed==1 else 'ideal_reversal'
        else:
            self.peak=max(self.peak,bid)
            if bid>=self.ideal-1e-8:self.armed=2
            elif bid>=self.safe-1e-8:self.armed=max(self.armed,1)
        return self.pending

