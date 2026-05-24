"""Export ALL 4729 trades to CSV with every possible feature for analysis."""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50','NIFTY_BANK'): continue
    with open(f) as fh: rows=list(csv.DictReader(fh))
    bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
           'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym]=bars
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in by_d.items(): date_bars[d][sym]=bs

all_dates=sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
prev_day_bars={}; daily_ctx={}; prev_5d={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]; dh=[]; dl=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; h=max(b['high'] for b in db); l=min(b['low'] for b in db)
        dc.append(c); dh.append(h); dl.append(l)
        if i>0: prev_day_bars[(d,sym)]=date_bars[dfs[i-1]].get(sym,[])
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_ctx[(d,sym)]={
                'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE',
                'chg_5d': round((dc[-1]/dc[-5]-1)*100,2) if len(dc)>=5 else 0,
                'rsi': 50,
            }
            if len(dc)>=15:
                g=[max(0,dc[j]-dc[j-1]) for j in range(-14,0)]
                lo=[max(0,dc[j-1]-dc[j]) for j in range(-14,0)]
                ag=sum(g)/14; al=sum(lo)/14
                daily_ctx[(d,sym)]['rsi']=round(100-100/(1+ag/al) if al>0 else 50)
            # 52w
            lb=min(250,len(dc))
            daily_ctx[(d,sym)]['from_52h']=round((dc[-1]-max(dc[-lb:]))/max(dc[-lb:])*100,1)
            daily_ctx[(d,sym)]['from_52l']=round((dc[-1]-min(dc[-lb:]))/min(dc[-lb:])*100,1)
            # Higher lows
            if len(dl)>=5:
                daily_ctx[(d,sym)]['higher_lows']=sum(1 for j in range(1,len(dl[-5:])) if dl[-5:][j]>dl[-5:][j-1])
            # Prev day range
            if i>0:
                daily_ctx[(d,sym)]['prev_day_range']=round((dh[-2]-dl[-2])/dc[-2]*100,2) if len(dc)>=2 else 0
                daily_ctx[(d,sym)]['prev_day_dir']='UP' if dc[-2]>dc[-3] else 'DOWN' if len(dc)>=3 else '?'

print('Done. Generating trades...', flush=True)

scan_bar=10; TARGET=1.00; STOP=0.75
trades=[]

for date in all_dates:
    # Market breadth
    up_cnt=dn_cnt=tot_cnt=0
    for s2 in date_bars[date]:
        d2=date_bars[date][s2]
        if len(d2)<=scan_bar: continue
        tot_cnt+=1; mv=(d2[scan_bar]['close']-d2[0]['open'])/d2[0]['open']*100
        if mv>0.15: up_cnt+=1
        elif mv<-0.15: dn_cnt+=1
    if tot_cnt==0: continue
    breadth_up=round(up_cnt/tot_cnt*100,1)
    breadth_dn=round(dn_cnt/tot_cnt*100,1)
    regime='UP' if up_cnt/tot_cnt>0.6 else 'DOWN' if dn_cnt/tot_cnt>0.6 else 'CHOPPY'

    try: dow=datetime.strptime(date,'%Y-%m-%d').weekday()
    except: dow=-1
    dow_name=['Mon','Tue','Wed','Thu','Fri'][dow] if 0<=dow<=4 else '?'

    for sym in date_bars[date]:
        db=date_bars[date][sym]; ctx=daily_ctx.get((date,sym),{})
        if len(db)<=scan_bar+20 or ctx.get('trend')!='DOWN': continue
        lp=prev_day_bars.get((date,sym),[])
        if not lp: continue
        ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
        if rng<=0: continue
        r3=pcc+rng*1.1/4; r4=pcc+rng*1.1/2; s3=pcc-rng*1.1/4

        cam_r3=False; trigger_bar=0
        for j in range(1,scan_bar+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                cam_r3=True; trigger_bar=j; break
        if not cam_r3: continue

        entry=db[scan_bar]['close']
        tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)

        # Simulate
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'; mfe=0; mae=0; exit_bar_num=69
        for k in range(scan_bar+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100; mfe=max(mfe,fav)
            adv=(db[k]['high']-entry)/entry*100; mae=max(mae,adv)
            if db[k]['low']<=tp: ep=tp; exit_r='target'; exit_bar_num=k; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; exit_bar_num=k; break
        pnl=(entry-ep)/entry*100

        # All features
        morning=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        gap=(db[0]['open']-pcc)/pcc*100
        b0_body=abs(db[0]['close']-db[0]['open'])/(db[0]['high']-db[0]['low'])*100 if db[0]['high']!=db[0]['low'] else 0
        b0_green=db[0]['close']>db[0]['open']
        vol_avg=sum(b['volume'] for b in db[:scan_bar+1])/(scan_bar+1)

        # Bar at entry
        eb=db[scan_bar]
        eb_body=abs(eb['close']-eb['open'])/(eb['high']-eb['low'])*100 if eb['high']!=eb['low'] else 0
        eb_green=eb['close']>eb['open']

        # Consecutive bars
        consec_red=0
        for k in range(scan_bar,max(scan_bar-5,0),-1):
            if db[k]['close']<db[k]['open']: consec_red+=1
            else: break
        consec_green=0
        for k in range(scan_bar,max(scan_bar-5,0),-1):
            if db[k]['close']>db[k]['open']: consec_green+=1
            else: break

        # VWAP
        tp_vol=sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in db[:scan_bar+1])
        cum_vol=sum(b['volume'] for b in db[:scan_bar+1])
        vwap=tp_vol/cum_vol if cum_vol>0 else entry
        vwap_dist=(entry-vwap)/vwap*100

        # ORB
        orb_high=db[0]['high']; orb_low=db[0]['low']
        orb_range=(orb_high-orb_low)/entry*100
        above_orb=entry>orb_high
        below_orb=entry<orb_low

        # Distance from R3
        r3_dist=(entry-r3)/entry*100

        # ATR at entry
        atr_at_entry=sum(db[k]['high']-db[k]['low'] for k in range(max(0,scan_bar-5),scan_bar+1))/min(6,scan_bar+1)
        atr_pct=atr_at_entry/entry*100

        # Time to exit
        bars_held=exit_bar_num-scan_bar

        trades.append({
            'date':date, 'day_of_week':dow_name, 'symbol':sym,
            'entry_price':round(entry,2), 'r3_level':round(r3,2), 'r4_level':round(r4,2),
            'prev_high':round(ph,2), 'prev_low':round(pl,2), 'prev_close':round(pcc,2),
            'prev_range_pct':round(rng/pcc*100,2),
            'target_price':round(tp,2), 'stop_price':round(sp,2),
            'exit_price':round(ep,2), 'exit_reason':exit_r,
            'pnl_pct':round(pnl,4), 'win':1 if pnl>0 else 0,
            'mfe_pct':round(mfe,3), 'mae_pct':round(mae,3),
            'bars_held':bars_held,
            'trigger_bar':trigger_bar,
            'morning_move_pct':round(morning,2),
            'gap_pct':round(gap,2),
            'bar0_body_pct':round(b0_body,0),
            'bar0_green':1 if b0_green else 0,
            'entry_bar_body_pct':round(eb_body,0),
            'entry_bar_green':1 if eb_green else 0,
            'volume_avg':round(vol_avg,0),
            'consec_red':consec_red,
            'consec_green':consec_green,
            'vwap_distance_pct':round(vwap_dist,3),
            'orb_range_pct':round(orb_range,3),
            'above_orb':1 if above_orb else 0,
            'below_orb':1 if below_orb else 0,
            'r3_distance_pct':round(r3_dist,3),
            'atr_pct':round(atr_pct,3),
            'market_regime':regime,
            'breadth_up_pct':breadth_up,
            'breadth_down_pct':breadth_dn,
            'daily_trend':ctx.get('trend','?'),
            'daily_rsi':ctx.get('rsi',50),
            'daily_5d_change':ctx.get('chg_5d',0),
            'from_52w_high':ctx.get('from_52h',0),
            'from_52w_low':ctx.get('from_52l',0),
            'higher_lows_5d':ctx.get('higher_lows',0),
            'prev_day_range_pct':ctx.get('prev_day_range',0),
            'prev_day_dir':ctx.get('prev_day_dir','?'),
        })

# Write CSV
out_file=Path('data/all_signals_analysis.csv')
fields=list(trades[0].keys())
with open(out_file,'w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields)
    w.writeheader()
    w.writerows(trades)

print(f'\nExported {len(trades)} trades to {out_file}')
print(f'Columns: {len(fields)}')
print(f'Fields: {", ".join(fields)}')

# Quick summary
wins=sum(t['win'] for t in trades)
print(f'\nWR: {wins}/{len(trades)} = {wins/len(trades)*100:.0f}%')
print(f'Gross: {sum(t["pnl_pct"] for t in trades):+.1f}%')
