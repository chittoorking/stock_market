"""
RUNNER TEST: After hitting 1.75% target, don't close.
Move stop to 1.75% and let it run further.
How much more do we capture?
"""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

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
all_dates=sorted(date_bars.keys())
daily_ohlc=defaultdict(dict)
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],
            'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
prev_day={}; daily_trend={}
for sym in all_data:
    sd=sorted(daily_ohlc[sym].keys()); dc=[]
    for i,d in enumerate(sd):
        c=daily_ohlc[sym][d]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[sd[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50

def simulate(mode, trail_step=0.50):
    """
    mode='fixed': Close at target (current system)
    mode='runner': At target, move stop to target and let it run
    mode='stepped': Progressive trail after target (move stop up in steps)
    """
    trades=[]
    for date in all_dates:
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=SB+20: continue
            trend=daily_trend.get((date,sym))
            if trend not in ('DOWN','UP'): continue
            pd=prev_day.get((date,sym))
            if not pd: continue
            rng=pd['high']-pd['low']
            if rng<=0: continue

            if trend=='DOWN':
                sd2=sorted(daily_ohlc[sym].keys())
                di=sd2.index(date) if date in sd2 else -1
                if di<7: continue
                cd=0
                for back in range(1,20):
                    if di-back<1: break
                    if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']: cd+=1
                    else: break
                if cd>2: continue
                prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
                yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                if yr<2.0 or yb<0.2: continue
                level=pd['close']+rng*1.1/4
                triggered=False
                for j in range(1,SB+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if abs(db[j]['high']-level)<atr*0.3 and db[j]['close']<level:
                        triggered=True; break
                if not triggered: continue
                direction='SHORT'
            else:
                level=pd['close']-rng*1.1/4
                triggered=False
                for j in range(1,SB+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if abs(db[j]['low']-level)<atr*0.3 and db[j]['close']>level:
                        triggered=True; break
                if not triggered: continue
                direction='LONG'

            entry=db[SB]['close']
            if direction=='SHORT':
                tp=entry*(1-T/100);sp=entry*(1+S/100);lp=entry*(1-0.075/100)
            else:
                tp=entry*(1+T/100);sp=entry*(1-S/100);lp=entry*(1+0.075/100)

            ep=db[min(69,len(db)-1)]['close'];exit_r='eod';mfe=0;ta=False
            target_hit=False; current_stop=sp; best_level=0

            for k in range(SB+1,min(len(db),70)):
                if direction=='SHORT':
                    fav=(entry-db[k]['low'])/entry*100
                    mfe=max(mfe,fav)

                    if mode=='fixed':
                        # Current system: trail at 1.0, close at target
                        if mfe>=1.0: ta=True
                        if db[k]['low']<=tp: ep=tp;exit_r='target';break
                        if ta and db[k]['high']>=lp: ep=lp;exit_r='trail';break
                        if not ta and db[k]['high']>=sp: ep=sp;exit_r='stop';break

                    elif mode=='runner':
                        # At 1.0% trail activates
                        if mfe>=1.0: ta=True
                        # At target: don't close, move stop to target level
                        if mfe>=T:
                            target_hit=True
                            current_stop=tp  # Lock at target
                        # After target: progressive trail
                        if target_hit and mode=='runner':
                            # Move stop up with every new 0.25% gain
                            new_level=entry*(1-(mfe-trail_step)/100)
                            if new_level<current_stop: current_stop=new_level
                        # Check trail/stop
                        if target_hit and db[k]['high']>=current_stop:
                            ep=current_stop;exit_r='runner_stop';break
                        if ta and not target_hit and db[k]['high']>=lp:
                            ep=lp;exit_r='trail';break
                        if not ta and not target_hit and db[k]['high']>=sp:
                            ep=sp;exit_r='stop';break

                else:  # LONG
                    fav=(db[k]['high']-entry)/entry*100
                    mfe=max(mfe,fav)

                    if mode=='fixed':
                        if mfe>=1.0: ta=True
                        if db[k]['high']>=tp: ep=tp;exit_r='target';break
                        if ta and db[k]['low']<=lp: ep=lp;exit_r='trail';break
                        if not ta and db[k]['low']<=sp: ep=sp;exit_r='stop';break

                    elif mode=='runner':
                        if mfe>=1.0: ta=True
                        if mfe>=T:
                            target_hit=True
                            current_stop=tp
                        if target_hit:
                            new_level=entry*(1+(mfe-trail_step)/100)
                            if new_level>current_stop: current_stop=new_level
                        if target_hit and db[k]['low']<=current_stop:
                            ep=current_stop;exit_r='runner_stop';break
                        if ta and not target_hit and db[k]['low']<=lp:
                            ep=lp;exit_r='trail';break
                        if not ta and not target_hit and db[k]['low']<=sp:
                            ep=sp;exit_r='stop';break

            if direction=='SHORT': pnl_pct=(entry-ep)/entry*100
            else: pnl_pct=(ep-entry)/entry*100
            pnl=pnl_pct/100*POS-CHARGES
            trades.append({'pnl':pnl,'win':pnl>0,'date':date,'mfe':mfe,
                          'pnl_pct':pnl_pct,'exit':exit_r,'target_hit':target_hit,'dir':direction})
    return trades

# ═══ Compare ═══
print('='*80)
print('FIXED TARGET vs RUNNER')
print('='*80)

fixed=simulate('fixed')
fn=len(fixed);fw=sum(1 for t in fixed if t['win']);ft=sum(t['pnl'] for t in fixed)
print(f'\n  FIXED (close at 1.75%):')
print(f'    {fn} trades, WR={fw/fn*100:.1f}%, Rs {ft/fn:+,.0f}/trade, Total Rs {ft:+,.0f}')

# How many hit target and kept going?
target_hitters=[t for t in fixed if t['exit']=='target']
print(f'    Trades that hit target: {len(target_hitters)}')
extra_mfe=[t['mfe']-T for t in target_hitters]
print(f'    Of those, avg extra MFE beyond 1.75%: {sum(extra_mfe)/len(extra_mfe):.2f}%')
went_further=sum(1 for m in extra_mfe if m>0.25)
print(f'    Went 0.25%+ beyond target: {went_further}/{len(target_hitters)} ({went_further/len(target_hitters)*100:.0f}%)')

for trail_step in [0.25, 0.50, 0.75, 1.00, 1.25]:
    runner=simulate('runner', trail_step)
    rn=len(runner);rw=sum(1 for t in runner if t['win']);rt=sum(t['pnl'] for t in runner)
    diff=rt-ft
    # Walk forward
    tr_r=[t for t in runner if t['date']<'2025-01-01']
    te_r=[t for t in runner if t['date']>='2025-01-01']
    te_wr=sum(1 for t in te_r if t['win'])/len(te_r)*100
    te_per=sum(t['pnl'] for t in te_r)/len(te_r)
    marker=' <<<' if diff>0 else ''
    print(f'\n  RUNNER (trail step {trail_step}%):')
    print(f'    {rn} trades, WR={rw/rn*100:.1f}%, Rs {rt/rn:+,.0f}/trade, Total Rs {rt:+,.0f}')
    print(f'    vs Fixed: Rs {diff:+,.0f} ({diff/ft*100:+.1f}%){marker}')
    print(f'    Walk-fwd test: WR={te_wr:.1f}%, Rs {te_per:+,.0f}/trade')

    # Show what happened to target-hit trades
    runner_targets=[t for t in runner if t['target_hit']]
    if runner_targets:
        avg_runner_pnl=sum(t['pnl_pct'] for t in runner_targets)/len(runner_targets)
        print(f'    Target-hit trades ({len(runner_targets)}): avg final PnL={avg_runner_pnl:.2f}% (vs fixed 1.75%)')
