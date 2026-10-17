"""Offline BTC entry/exit arithmetic. No trading or network dependencies."""
import math
import numpy as np
from numba import njit

@njit(cache=True)
def cash(price,buy,precision=.0001):
    fee=math.ceil(.07*price*(1-price)*1e6-1e-8)/1e6
    return math.floor(((-price if buy else price)-fee)/precision+1e-8)*precision

@njit(cache=True)
def direction(row,policy):
    b,a=row[0],row[1];fav=1 if b+a>1 else -1;fallback=0
    if policy==0:return fav,0
    if policy==1:return -fav,0
    if policy==2:return 1,0
    if policy==3:return -1,0
    idx=6 if policy in (4,5) else 7 if policy==6 else 10 if policy in (7,8) else 13 if policy==10 else 8 if policy==9 else 11 if policy==11 else 12
    value=row[idx]
    if math.isnan(value):return fav,1
    if policy in (4,5,6,7,8,10):
        side=1 if value>0 else -1
        if policy in (5,8):side=-side
        return side,0
    ey=value+cash(a,True);en=1-value+cash(1-b,True)
    return (1 if ey>=en else -1),0

@njit(cache=True)
def entry(x,policy,rem,band,delay=0):
    begin=0 if band else 14-rem
    for j in range(begin,14-delay):
        b,a=x[j,0],x[j,1]
        if math.isnan(b+a) or not 0<=b<=a<=1 or a-b>.05000001:continue
        side,fallback=direction(x[j],policy);ask=a if side==1 else 1-b
        if band and 14-j>rem and not .55-1e-8<=ask<=.65+1e-8:continue
        off=4 if delay else 2;eb,ea=x[j,off],x[j,off+1]
        if math.isnan(eb+ea) or not 0<=eb<=ea<=1 or ea-eb>.05000001:continue
        price=ea if side==1 else 1-eb
        if not 0<price<1:continue
        return j,side,price,fallback
    return -1,0,0.,0

@njit(cache=True)
def breach(mode,amount,activate,arm,model_col,margin,remaining,entry_rem,price,entry_price,peak,armed,feature,side):
    if remaining>activate:return False
    if mode==1:return price<=entry_price-amount+1e-8
    if mode==2:return entry_price>amount and price<=amount+1e-8
    if mode==3:return price<=entry_price-amount*math.sqrt(remaining/entry_rem)+1e-8
    if mode==4:return armed and price<=peak-amount+1e-8
    if mode==5:return price<entry_price and not math.isnan(feature[6]) and side*feature[6]<=-amount
    if mode==6:
        p=feature[model_col]
        if math.isnan(p):return False
        if side==-1:p=1-p
        return price<entry_price and p+margin<=cash(price,False)-.0025+1e-12
    return False

@njit(cache=True)
def exit_trade(x,result,ent,ex,delay=0):
    j,side,price,fallback=ent;j=int(j);side=int(side)
    mode,amount,activate,confirm,arm,model_col,margin=ex;model_col=int(model_col)
    if j<0:return 0.,14,False,False,-1
    if mode==0:return (1. if result==side else 0.),14,False,False,-1
    pending=False;consecutive=0;peak=price;armed=False;trigger=-1
    for k in range(j+1+delay,14):
        b,a=x[k,0],x[k,1]
        if not pending:
            if math.isnan(b+a) or not 0<=b<=a<=1:consecutive=0;continue
            bid=b if side==1 else 1-a;peak=max(peak,bid)
            if bid>=price+arm-1e-8:armed=True
            hit=breach(int(mode),amount,activate,arm,model_col,margin,14-k,14-j,bid,price,peak,armed,x[k],side)
            consecutive=consecutive+1 if hit else 0
            if hit and consecutive>=confirm:pending=True;trigger=k
        if pending:
            if k+delay>=14:break
            off=4 if delay else 2;eb,ea=x[k,off],x[k,off+1]
            if math.isnan(eb+ea) or not 0<=eb<=ea<=1:continue
            out=eb if side==1 else 1-ea
            if 0<out<1:return out,k,True,armed,trigger
    return (1. if result==side else 0.),14,False,armed,trigger

@njit(cache=True)
def screen(x,y,co,entries,exits,delay=0,max_co=1):
    out=np.zeros((len(entries)*len(exits),5,14))
    for ei,ec in enumerate(entries):
        for i in range(len(x)):
            cc=int(co[i])
            if cc>max_co:continue
            ent=entry(x[i],int(ec[0]),int(ec[1]),int(ec[2]),delay)
            if ent[0]<0:continue
            debit=cash(ent[2],True);won=int(y[i]==ent[1]);hold=debit+won-.0025
            for xi,ex in enumerate(exits):
                price,k,sold,armed,trigger=exit_trade(x[i],y[i],ent,ex,delay)
                proceeds=cash(price,False) if sold else price;net=debit+proceeds-.0025*(1+int(sold));ix=ei*len(exits)+xi
                o=out[ix,cc];o[0]+=1;o[1]+=net;o[2]+=net*net;o[3]+=int(net>1e-10);o[4]+=int(sold);o[5]+=won;o[6]+=hold
                if sold and won==0:o[7]+=proceeds-.0025;o[10]+=1
                if sold and won==1:o[8]+=1-proceeds+.0025;o[9]+=1
                o[11]+=ent[2];o[12]+=net if net>0 else 0;o[13]+=net if net<0 else 0
    return out
