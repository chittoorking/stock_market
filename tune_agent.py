"""Grid search: find optimal score threshold, R:R, booking levels, exit time."""
import sys, io, csv, json
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
        db=date_bars[d].get(sym,[]);
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_close[(d,sym)]=pdb[-1]['close']
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            t='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
            rsi=50
            if len(dc)>=15:
                g=[max(0,dc[j]-dc[j-1]) for j in range(-14,0)]
                lo=[max(0,dc[j-1]-dc[j]) for j in range(-14,0)]
                ag=sum(g)/14;al=sum(lo)/14
                rsi=100-100/(1+ag/al) if al>0 else 50
            daily_ctx[(d,sym)]={'trend':t,'rsi':round(rsi)}
print('Done.\n', flush=True)

test_dates=all_dates[-200:]
scan_bar=10
print(f'GRID SEARCH on {len(test_dates)} days\n{"="*100}', flush=True)

best_net=-999; best_cfg=None; all_cfgs=[]

for min_score in [2,3,4,5,6,7]:
 for rr in [2.0,2.5,3.0,3.5]:
  for book_l1 in [0.4,0.5,0.7,1.0]:
   for be_at in [0.3,0.5,999]:
    for exit_bar in [48,60,69]:
      results=[]
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
            if pc is None or len(db)<=scan_bar+10 or not ctx: continue
            rh=db[0]['high'];rl=db[0]['low'];rs=rh-rl
            if rs>0:
                for j in range(1,scan_bar+1):
                    if db[j]['close']>rh and db[j]['volume']>db[0]['volume']:
                        signals.append({'sym':sym,'type':'ORB','dir':'LONG','stop':rl,'risk':round((db[j]['close']-rl)/db[j]['close']*100,2),'trend':ctx.get('trend','?'),'rsi':ctx.get('rsi',50)}); break
                    if db[j]['close']<rl and db[j]['volume']>db[0]['volume']:
                        signals.append({'sym':sym,'type':'ORB','dir':'SHORT','stop':rh,'risk':round((rh-db[j]['close'])/db[j]['close']*100,2),'trend':ctx.get('trend','?'),'rsi':ctx.get('rsi',50)}); break
            pd_dates=sorted(set(b['timestamp'][:10] for b in all_data[sym] if b['timestamp'][:10]<date))
            if pd_dates:
                lp=date_bars[pd_dates[-1]].get(sym,[])
                if lp:
                    ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng2=ph-pl
                    if rng2>0:
                        r3=pcc+rng2*1.1/4;r4=pcc+rng2*1.1/2
                        for j in range(1,scan_bar+1):
                            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                                signals.append({'sym':sym,'type':'CAM_R3','dir':'SHORT','stop':r4,'risk':round((r4-db[j]['close'])/db[j]['close']*100,2),'trend':ctx.get('trend','?'),'rsi':ctx.get('rsi',50)}); break

        seen=set();unique=[]
        for s in signals:
            if s['sym'] not in seen: seen.add(s['sym']);unique.append(s)

        best=None;bsc=-999
        for s in unique:
            sc=0
            if s['type']=='CAM_R3': sc+=3
            if s['dir']=='SHORT' and s['trend']=='DOWN': sc+=3
            elif s['dir']=='LONG' and s['trend']=='UP': sc+=3
            elif s['trend']=='SIDE': sc+=0
            else: sc-=2
            if s['dir']=='SHORT' and s['rsi']<40: sc+=2
            elif s['dir']=='LONG' and s['rsi']>60: sc+=2
            if s['risk']<1.0: sc+=1
            elif s['risk']>2.5: sc-=1
            if regime=='UP' and s['dir']=='LONG': sc+=1
            elif regime=='DOWN' and s['dir']=='SHORT': sc+=1
            elif regime!='CHOPPY' and ((regime=='UP' and s['dir']=='SHORT') or (regime=='DOWN' and s['dir']=='LONG')): sc-=1
            if sc>bsc: bsc=sc;best=s

        if not best or bsc<min_score: continue
        sym=best['sym'];direction=best['dir'];db=date_bars[date][sym]
        entry=db[scan_bar]['close'];stop=best['stop'];risk=abs(entry-stop)
        if risk==0: continue
        target=entry+risk*rr if direction=='LONG' else entry-risk*rr

        lots={'L1':0.30,'L2':0.25,'L3':0.20,'L4':0.15,'L5':0.10}
        booked=0;cur_stop=stop;mfe=0
        for j in range(scan_bar+1,min(len(db),exit_bar+1)):
            b=db[j];pnl_now=(b['close']-entry)/entry*100 if direction=='LONG' else (entry-b['close'])/entry*100
            fav=(b['high']-entry)/entry*100 if direction=='LONG' else (entry-b['low'])/entry*100
            mfe=max(mfe,fav);remaining=sum(lots.values())
            if remaining<=0: break
            if direction=='LONG' and b['low']<=cur_stop: booked+=((cur_stop-entry)/entry*100)*remaining;lots={};break
            if direction=='SHORT' and b['high']>=cur_stop: booked+=((entry-cur_stop)/entry*100)*remaining;lots={};break
            if direction=='LONG' and b['high']>=target: booked+=((target-entry)/entry*100)*remaining;lots={};break
            if direction=='SHORT' and b['low']<=target: booked+=((entry-target)/entry*100)*remaining;lots={};break
            if (j-scan_bar)%3!=0 or j==scan_bar+1: continue
            if pnl_now<book_l1*0.8: continue
            if be_at<100 and pnl_now>=be_at and ((direction=='LONG' and cur_stop<entry) or (direction=='SHORT' and cur_stop>entry)):
                cur_stop=entry
            if pnl_now>=book_l1 and 'L1' in lots:
                booked+=pnl_now*lots['L1'];del lots['L1']
                cur_stop=entry+risk*0.3 if direction=='LONG' else entry-risk*0.3
            if pnl_now>=1.0 and 'L2' in lots: booked+=pnl_now*lots['L2'];del lots['L2']
            if pnl_now>=1.5 and 'L3' in lots: booked+=pnl_now*lots['L3'];del lots['L3']
            if pnl_now>=2.0 and 'L4' in lots: booked+=pnl_now*lots['L4'];del lots['L4']

        if lots:
            ep=db[min(exit_bar,len(db)-1)]['close']
            eod_pnl=(ep-entry)/entry*100 if direction=='LONG' else (entry-ep)/entry*100
            booked+=eod_pnl*sum(lots.values())
        results.append({'pnl':booked,'win':booked>0})

      if not results or len(results)<10: continue
      w=sum(r['win'] for r in results);n=len(results)
      gross=sum(r['pnl'] for r in results);charges=0.0835*n;net=gross-charges;wr=w/n*100

      all_cfgs.append({'min_score':min_score,'rr':rr,'book_l1':book_l1,'be':be_at,'exit':exit_bar,
          'n':n,'wr':round(wr),'gross':round(gross,2),'net':round(net,2)})
      if net>best_net: best_net=net; best_cfg=all_cfgs[-1]

# Sort and show top 20
all_cfgs.sort(key=lambda x:-x['net'])
print(f'\nTOP 20 CONFIGS (out of {len(all_cfgs)} tested):')
print(f"{'sc':>3} {'RR':>4} {'bk@':>4} {'BE':>4} {'exit':>4} | {'N':>4} {'WR':>4} {'Gross':>7} {'Net':>7}")
print('-'*55)
for c in all_cfgs[:20]:
    be_str = f'{c["be"]:.1f}' if c['be']<100 else 'OFF'
    marker = ' <<<' if c['net']>5 else ''
    print(f'{c["min_score"]:>3} {c["rr"]:>4.1f} {c["book_l1"]:>4.1f} {be_str:>4} {c["exit"]:>4} | {c["n"]:>4} {c["wr"]:>3}% {c["gross"]:>+6.1f}% {c["net"]:>+6.1f}%{marker}')

print(f'\nBEST: {json.dumps(best_cfg)}')
