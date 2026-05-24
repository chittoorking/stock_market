"""What happens to trades AFTER reaching 1.25% MFE?"""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
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
        daily_ohlc[sym][d]={'close':bs[-1]['close']}

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

# Simulate bar by bar, track what happens after 1.25%
results_no_trail=[]
results_with_trail=[]

for date in all_dates:
    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        if daily_trend.get((date,sym))!='DOWN': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        r3=pd['close']+rng*1.1/4
        triggered=False
        for j in range(1,SB+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                triggered=True; break
        if not triggered: continue

        sd2=sorted(daily_ohlc[sym].keys())
        di=sd2.index(date) if date in sd2 else -1
        if di<7: continue
        cd=0
        for back in range(1,20):
            if di-back<1: break
            if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']:
                cd+=1
            else: break
        if cd>2: continue

        entry=db[SB]['close']
        tp=entry*(1-T/100)
        sp=entry*(1+S/100)

        # === NO TRAIL ===
        ep1=db[min(69,len(db)-1)]['close']; ex1='eod'; mfe1=0
        for k in range(SB+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100
            mfe1=max(mfe1,fav)
            if db[k]['low']<=tp: ep1=tp; ex1='target'; break
            if db[k]['high']>=sp: ep1=sp; ex1='stop'; break
        pnl1=(entry-ep1)/entry*100/100*POS-CHARGES

        # === WITH TRAIL: at 1.25% lock 0.50% ===
        ep2=db[min(69,len(db)-1)]['close']; ex2='eod'; mfe2=0
        trail_active=False
        lock_price=entry*(1-0.50/100)  # Lock 0.50% profit
        for k in range(SB+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100
            mfe2=max(mfe2,fav)
            if mfe2>=1.25: trail_active=True

            if db[k]['low']<=tp: ep2=tp; ex2='target'; break
            if trail_active and db[k]['high']>=lock_price:
                ep2=lock_price; ex2='trail'; break
            if not trail_active and db[k]['high']>=sp:
                ep2=sp; ex2='stop'; break
        pnl2=(entry-ep2)/entry*100/100*POS-CHARGES

        results_no_trail.append({'pnl':pnl1,'exit':ex1,'mfe':mfe1,'date':date,'sym':sym})
        results_with_trail.append({'pnl':pnl2,'exit':ex2,'mfe':mfe2,'date':date,'sym':sym})

n=len(results_no_trail)

# Compare
print('='*80)
print('TRADES THAT REACH 1.25%: What happens next?')
print('='*80)

reached=[i for i in range(n) if results_no_trail[i]['mfe']>=1.25]
not_reached=[i for i in range(n) if results_no_trail[i]['mfe']<1.25]

print(f'\nTotal: {n} trades')
print(f'Reached 1.25%: {len(reached)} ({len(reached)/n*100:.0f}%)')
print(f'Never reached: {len(not_reached)} ({len(not_reached)/n*100:.0f}%)')

# What happened to the ones that reached 1.25%?
print(f'\nOf {len(reached)} that reached 1.25%:')
outcomes = defaultdict(list)
for i in reached:
    t=results_no_trail[i]
    if t['exit']=='target': outcomes['Hit 1.75% target'].append(t)
    elif t['exit']=='stop': outcomes['Reversed to stop (-1.5%)'].append(t)
    elif t['pnl']>0: outcomes['EOD in profit (didnt reach target)'].append(t)
    else: outcomes['EOD in LOSS (gave it ALL back)'].append(t)

for outcome, tl in sorted(outcomes.items(), key=lambda x: -len(x[1])):
    avg_pnl=sum(t['pnl'] for t in tl)/len(tl)
    avg_mfe=sum(t['mfe'] for t in tl)/len(tl)
    print(f'  {outcome}: {len(tl)} trades, avg Rs {avg_pnl:+,.0f}, avg MFE {avg_mfe:.2f}%')

# The ones that gave it ALL back
gave_back=[i for i in reached if results_no_trail[i]['pnl']<=0]
print(f'\n  ** {len(gave_back)} trades reached 1.25%+ and STILL LOST ** ')
print(f'  These are the ones we want to save.')

# Now compare: with trail at 1.25% -> lock 0.50%
print(f'\n{"="*80}')
print(f'WITH TRAIL: At 1.25% lock in 0.50%')
print(f'{"="*80}')

# For NOT reached trades: trail doesn't activate, should be identical
changed_good=0; changed_bad=0; unchanged=0
total_diff=0

for i in range(n):
    diff=results_with_trail[i]['pnl']-results_no_trail[i]['pnl']
    if abs(diff)<1: unchanged+=1
    elif diff>0: changed_good+=1; total_diff+=diff
    else: changed_bad+=1; total_diff+=diff

w1=sum(1 for t in results_no_trail if t['pnl']>0)
w2=sum(1 for t in results_with_trail if t['pnl']>0)
t1=sum(t['pnl'] for t in results_no_trail)
t2=sum(t['pnl'] for t in results_with_trail)

print(f'\n  No trail:   WR={w1/n*100:.0f}%, Rs {t1/n:+,.0f}/trade, Total Rs {t1:+,.0f}')
print(f'  With trail: WR={w2/n*100:.0f}%, Rs {t2/n:+,.0f}/trade, Total Rs {t2:+,.0f}')
print(f'\n  Trades improved: {changed_good}')
print(f'  Trades hurt:     {changed_bad}')
print(f'  Trades unchanged: {unchanged}')
print(f'  Net effect: Rs {total_diff:+,.0f}')

# Show every changed trade
print(f'\n  IMPROVED trades (trail saved them):')
for i in range(n):
    diff=results_with_trail[i]['pnl']-results_no_trail[i]['pnl']
    if diff>100:
        t1t=results_no_trail[i]; t2t=results_with_trail[i]
        print(f'    {t1t["date"]} {t1t["sym"]:>12}: MFE={t1t["mfe"]:.2f}% | No trail: Rs {t1t["pnl"]:+,.0f} ({t1t["exit"]}) -> Trail: Rs {t2t["pnl"]:+,.0f} ({t2t["exit"]}) | SAVED Rs {diff:+,.0f}')

print(f'\n  HURT trades (trail cut them short):')
hurt_count=0
for i in range(n):
    diff=results_with_trail[i]['pnl']-results_no_trail[i]['pnl']
    if diff<-100:
        t1t=results_no_trail[i]; t2t=results_with_trail[i]
        hurt_count+=1
        if hurt_count<=15:
            print(f'    {t1t["date"]} {t1t["sym"]:>12}: MFE={t1t["mfe"]:.2f}% | No trail: Rs {t1t["pnl"]:+,.0f} ({t1t["exit"]}) -> Trail: Rs {t2t["pnl"]:+,.0f} ({t2t["exit"]}) | LOST Rs {diff:+,.0f}')
if hurt_count>15: print(f'    ... and {hurt_count-15} more')

# Test different lock levels at 1.25
print(f'\n{"="*80}')
print(f'DIFFERENT LOCK LEVELS AT 1.25% ACTIVATION')
print(f'{"="*80}')

for lock_pct in [0.00, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.75]:
    trades_t=[]
    for date in all_dates:
        for sym in date_bars[date]:
            if sym in ('NIFTY_50','NIFTY_BANK'): continue
            db=date_bars[date][sym]
            if len(db)<=SB+20: continue
            if daily_trend.get((date,sym))!='DOWN': continue
            pd2=prev_day.get((date,sym))
            if not pd2: continue
            rng2=pd2['high']-pd2['low']
            if rng2<=0: continue
            r32=pd2['close']+rng2*1.1/4
            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r32)<atr*0.3 and db[j]['close']<r32:
                    triggered=True; break
            if not triggered: continue
            sd3=sorted(daily_ohlc[sym].keys())
            di2=sd3.index(date) if date in sd3 else -1
            if di2<7: continue
            cd2=0
            for back in range(1,20):
                if di2-back<1: break
                if daily_ohlc[sym][sd3[di2-back]]['close']<daily_ohlc[sym][sd3[di2-back-1]]['close']:
                    cd2+=1
                else: break
            if cd2>2: continue

            entry=db[SB]['close']
            tp2=entry*(1-T/100); sp2=entry*(1+S/100)
            lp=entry*(1-lock_pct/100)
            ep=db[min(69,len(db)-1)]['close']; ex='eod'; mfe=0; ta=False
            for k in range(SB+1,min(len(db),70)):
                fav=(entry-db[k]['low'])/entry*100
                mfe=max(mfe,fav)
                if mfe>=1.25: ta=True
                if db[k]['low']<=tp2: ep=tp2; ex='target'; break
                if ta and lock_pct>0 and db[k]['high']>=lp: ep=lp; ex='trail'; break
                if ta and lock_pct==0 and db[k]['high']>=entry: ep=entry; ex='trail'; break
                if not ta and db[k]['high']>=sp2: ep=sp2; ex='stop'; break
            pnl=(entry-ep)/entry*100/100*POS-CHARGES
            trades_t.append(pnl)

    nt=len(trades_t); wt=sum(1 for p in trades_t if p>0); tt=sum(trades_t)
    diff_total=tt-t1
    label=f'breakeven' if lock_pct==0 else f'+{lock_pct}%'
    marker=' <<<' if diff_total>0 else ''
    print(f'  Lock {label:>10} at 1.25%: WR={wt/nt*100:.0f}%, Rs {tt/nt:+,.0f}/trade, Total Rs {tt:+,.0f} (diff Rs {diff_total:+,.0f}){marker}')
