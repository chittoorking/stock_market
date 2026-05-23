"""Compare OLD agent vs NEW agent (with learned rules) on 100 days."""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
sys.path.insert(0,'.')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    with open(f) as fh: rows=list(csv.DictReader(fh))
    bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
           'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym]=bars
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in by_d.items(): date_bars[d][sym]=bs

all_dates=sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
prev_close={}; daily_ctx={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_close[(d,sym)]=pdb[-1]['close']
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            bouncing = len(dc)>=3 and dc[-1]>dc[-2] and (dc[-3]-dc[-2])/dc[-3]*100>1.5 if len(dc)>=3 else False
            daily_ctx[(d,sym)]={'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE', 'bouncing': bouncing}
print('Done.\n', flush=True)

import argparse
_p=argparse.ArgumentParser();_p.add_argument('--days',type=int,default=100);_a,_=_p.parse_known_args()
test_dates=all_dates[-_a.days:]
scan_bar=10

def simulate(date, signal, use_new_rules=False):
    sym=signal['sym']; d=signal['dir']
    db=date_bars[date][sym]; entry=db[scan_bar]['close']
    stop=signal['stop']; risk=abs(entry-stop)
    if risk<=0: return None
    target=entry+risk*2.5 if d=='LONG' else entry-risk*2.5
    lots={'L1':0.30,'L2':0.25,'L3':0.20,'L4':0.15,'L5':0.10}
    booked=0; cur_stop=stop; mfe=0
    for j in range(scan_bar+1, min(len(db),70)):
        b=db[j]
        pnl_now=(b['close']-entry)/entry*100 if d=='LONG' else (entry-b['close'])/entry*100
        fav=(b['high']-entry)/entry*100 if d=='LONG' else (entry-b['low'])/entry*100
        mfe=max(mfe,fav); rem=sum(lots.values())
        if rem<=0: break
        if d=='LONG' and b['low']<=cur_stop: booked+=((cur_stop-entry)/entry*100)*rem; lots={}; break
        if d=='SHORT' and b['high']>=cur_stop: booked+=((entry-cur_stop)/entry*100)*rem; lots={}; break
        if d=='LONG' and b['high']>=target: booked+=((target-entry)/entry*100)*rem; lots={}; break
        if d=='SHORT' and b['low']<=target: booked+=((entry-target)/entry*100)*rem; lots={}; break
        if (j-scan_bar)%3!=0 or j==scan_bar+1: continue

        if use_new_rules and signal['type']=='CAM_R3':
            # NEW: lower booking, NO breakeven stop
            if pnl_now>=0.3 and 'L1' in lots: booked+=pnl_now*lots['L1']; del lots['L1']
            if pnl_now>=0.6 and 'L2' in lots: booked+=pnl_now*lots['L2']; del lots['L2']
            if pnl_now>=1.0 and 'L3' in lots: booked+=pnl_now*lots['L3']; del lots['L3']
            if pnl_now>=1.5 and 'L4' in lots: booked+=pnl_now*lots['L4']; del lots['L4']
            # After 2PM trail
            bars_held = j - scan_bar
            if bars_held >= 40 and pnl_now > 0:
                trail = entry - pnl_now/100*entry*0.5 if d=='SHORT' else entry + pnl_now/100*entry*0.5
                if d=='LONG' and trail > cur_stop: cur_stop = trail
                elif d=='SHORT' and trail < cur_stop: cur_stop = trail
        else:
            # OLD rules
            if pnl_now>=0.3 and ((d=='LONG' and cur_stop<entry) or (d=='SHORT' and cur_stop>entry)):
                cur_stop=entry
            if pnl_now>=0.5 and 'L1' in lots: booked+=pnl_now*lots['L1']; del lots['L1']

            if pnl_now>=1.0 and 'L2' in lots: booked+=pnl_now*lots['L2']; del lots['L2']
            if pnl_now>=1.5 and 'L3' in lots: booked+=pnl_now*lots['L3']; del lots['L3']
    if lots:
        ep=db[min(69,len(db)-1)]['close']
        eod_pnl=(ep-entry)/entry*100 if d=='LONG' else (entry-ep)/entry*100
        booked+=eod_pnl*sum(lots.values())
    return booked

def score_signal(s, regime, use_new_rules=False):
    sc=0
    if s['type']=='CAM_R3': sc+=3
    if s['dir']=='SHORT' and s.get('trend')=='DOWN': sc+=3
    elif s['dir']=='LONG' and s.get('trend')=='UP': sc+=3
    elif s.get('trend')=='SIDE': sc+=0
    else: sc-=2
    if s.get('risk',1)<1.0: sc+=1
    elif s.get('risk',1)>2.5: sc-=1
    if regime=='UP' and s['dir']=='LONG': sc+=1
    elif regime=='DOWN' and s['dir']=='SHORT': sc+=1
    elif regime!='CHOPPY' and ((regime=='UP' and s['dir']=='SHORT') or (regime=='DOWN' and s['dir']=='LONG')): sc-=1

    if use_new_rules:
        # Rule 3: No CAM_R3 SHORT when bar0 GREEN body > 70%
        if s['type']=='CAM_R3' and s['dir']=='SHORT' and s.get('b0_green') and s.get('b0_body',0)>70: sc-=5
        # Rule 4: No CAM_R3 LONG when bar0 RED body > 70%
        if s['type']=='CAM_R3' and s['dir']=='LONG' and not s.get('b0_green') and s.get('b0_body',0)>70: sc-=5
        # Rule 6: Bounce detection
        if s['dir']=='SHORT' and s.get('bouncing'): sc-=3
        # Rule 7: Volume filter
        if s.get('vol',99999)<20000: sc-=3
        # Rule 8: Prefer high volume
        if s.get('vol',0)>100000: sc+=1
        # Rule 11: Morning momentum for CAM_R3 SHORT
        if s['type']=='CAM_R3' and s['dir']=='SHORT' and s.get('morning',0)>0.5: sc-=2
    return sc

results_old=[]; results_new=[]

for date in test_dates:
    up=dn=tot=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=scan_bar: continue
        tot+=1; mv=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        if mv>0.15: up+=1
        elif mv<-0.15: dn+=1
    if tot==0: continue
    regime='UP' if up/tot>0.6 else 'DOWN' if dn/tot>0.6 else 'CHOPPY'

    signals=[]
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close.get((date,sym)); ctx=daily_ctx.get((date,sym),{})
        if pc is None or len(db)<=scan_bar+10: continue
        b0_body=abs(db[0]['close']-db[0]['open'])/(db[0]['high']-db[0]['low'])*100 if db[0]['high']!=db[0]['low'] else 0
        b0_green=db[0]['close']>db[0]['open']
        vol=sum(b['volume'] for b in db[:11])/11

        morning=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100 if db[0]['open']>0 else 0
        rh=db[0]['high'];rl=db[0]['low'];rs=rh-rl
        if rs>0:
            for j in range(1,scan_bar+1):
                if db[j]['close']>rh and db[j]['volume']>db[0]['volume']:
                    signals.append({'sym':sym,'type':'ORB','dir':'LONG','stop':rl,'risk':round((db[j]['close']-rl)/db[j]['close']*100,2),'trend':ctx.get('trend','?'),'bouncing':ctx.get('bouncing',False),'b0_body':b0_body,'b0_green':b0_green,'vol':vol,'morning':morning}); break
                if db[j]['close']<rl and db[j]['volume']>db[0]['volume']:
                    signals.append({'sym':sym,'type':'ORB','dir':'SHORT','stop':rh,'risk':round((rh-db[j]['close'])/db[j]['close']*100,2),'trend':ctx.get('trend','?'),'bouncing':ctx.get('bouncing',False),'b0_body':b0_body,'b0_green':b0_green,'vol':vol,'morning':morning}); break

        pd_dates=sorted(set(b['timestamp'][:10] for b in all_data[sym] if b['timestamp'][:10]<date))
        if pd_dates:
            lp=date_bars[pd_dates[-1]].get(sym,[])
            if lp:
                ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
                if rng>0:
                    r3=pcc+rng*1.1/4;r4=pcc+rng*1.1/2
                    for j in range(1,scan_bar+1):
                        atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                        if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                            signals.append({'sym':sym,'type':'CAM_R3','dir':'SHORT','stop':r4,'risk':round((r4-db[j]['close'])/db[j]['close']*100,2),'trend':ctx.get('trend','?'),'bouncing':ctx.get('bouncing',False),'b0_body':b0_body,'b0_green':b0_green,'vol':vol,'morning':morning}); break

    seen=set();unique=[]
    for s in signals:
        if s['sym'] not in seen: seen.add(s['sym']);unique.append(s)

    # OLD
    best_old=None;bsc_old=-999
    for s in unique:
        sc=score_signal(s, regime, False)
        if sc>bsc_old: bsc_old=sc; best_old=s
    if best_old and bsc_old>=2:
        r=simulate(date, best_old, False)
        if r is not None: results_old.append(r)

    # NEW
    best_new=None;bsc_new=-999
    for s in unique:
        sc=score_signal(s, regime, True)
        if sc>bsc_new: bsc_new=sc; best_new=s
    if best_new and bsc_new>=2:
        r=simulate(date, best_new, True)
        if r is not None: results_new.append(r)

print('='*80)
print('100-DAY COMPARISON')
print('='*80)

wo=sum(1 for r in results_old if r>0);no=len(results_old)
go=sum(results_old);co=0.0835*no;neto=go-co
wn=sum(1 for r in results_new if r>0);nn=len(results_new)
gn=sum(results_new);cn=0.0835*nn;netn=gn-cn

print(f'OLD: {no} trades, WR={wo}/{no}={wo/no*100:.0f}%, gross={go:+.2f}%, charges={co:.2f}%, net={neto:+.2f}%')
print(f'NEW: {nn} trades, WR={wn}/{nn}={wn/nn*100:.0f}%, gross={gn:+.2f}%, charges={cn:.2f}%, net={netn:+.2f}%')
print(f'IMPROVEMENT: {netn-neto:+.2f}% net')

bal_old=100000;bal_new=100000
for r in results_old: bal_old*=(1+(r-0.0835)/100)
for r in results_new: bal_new*=(1+(r-0.0835)/100)
print(f'OLD: Rs {bal_old:,.0f} | NEW: Rs {bal_new:,.0f} | Diff: Rs {bal_new-bal_old:+,.0f}')

# Breakeven count
be_old=sum(1 for r in results_old if abs(r)<0.01)
be_new=sum(1 for r in results_new if abs(r)<0.01)
print(f'Breakeven trades — OLD: {be_old} | NEW: {be_new}')
print(f'Charges saved from fewer BEs: Rs {(be_old-be_new)*84:+,.0f}')
