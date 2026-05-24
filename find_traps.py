"""
FIND THE TRAPS — Not broad filters. Only EXTREME signals that scream "DON'T TRADE".
Skip 5-10% of trades, keep 90-95%. Remove only the obvious losers.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt

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

all_dates=sorted(date_bars.keys())
daily_ohlc=defaultdict(dict)
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={'open':bs[0]['open'],'high':max(b['high'] for b in bs),
            'low':min(b['low'] for b in bs),'close':bs[-1]['close'],
            'volume':sum(b['volume'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}

prev_day={}; daily_trend={}
for sym in all_data:
    sym_dates=sorted(daily_ohlc[sym].keys()); dc=[]
    for i,d in enumerate(sym_dates):
        c=daily_ohlc[sym][d]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[sym_dates[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'

vix_data={}
vf=Path('data/vix_daily.csv')
if vf.exists():
    with open(vf) as f:
        for r in csv.DictReader(f): vix_data[r['date']]=float(r['close'])

POS=1000000; CHARGES=386; SCAN_BAR=10; TARGET=1.75; STOP=1.50
print('Loaded.\n')

trades=[]
for date in all_dates:
    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db=date_bars[date][sym]
        if len(db)<=SCAN_BAR+20: continue
        if daily_trend.get((date,sym))!='DOWN': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        r3=pd['close']+rng*1.1/4
        triggered=False
        for j in range(1,SCAN_BAR+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                triggered=True; break
        if not triggered: continue

        entry=db[SCAN_BAR]['close']
        tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
        mfe=0; mae=0
        for k in range(SCAN_BAR+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100; adv=(db[k]['high']-entry)/entry*100
            mfe=max(mfe,fav); mae=max(mae,adv)
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
        pnl_rs=(entry-ep)/entry*100/100*POS-CHARGES

        sym_dates=sorted(daily_ohlc[sym].keys())
        di=sym_dates.index(date) if date in sym_dates else -1
        if di<7: continue

        # Consecutive down days
        consec_down=0
        for back in range(1,20):
            if di-back<1: break
            if daily_ohlc[sym][sym_dates[di-back]]['close']<daily_ohlc[sym][sym_dates[di-back-1]]['close']:
                consec_down+=1
            else: break

        # Fall in last 5 days
        c5=daily_ohlc[sym][sym_dates[di-5]]['close']
        fall_5d=(c5-pd['close'])/c5*100 if c5>0 else 0

        # Fall in last 3 days
        c3=daily_ohlc[sym][sym_dates[di-3]]['close']
        fall_3d=(c3-pd['close'])/c3*100 if c3>0 else 0

        # Previous day: hammer?
        prev_d=sym_dates[di-1]; pc=daily_ohlc[sym][prev_d]
        prev_body=abs(pc['close']-pc['open']); prev_range=pc['range']
        lower_wick=min(pc['open'],pc['close'])-pc['low']
        is_hammer=lower_wick>prev_body*2 and lower_wick>prev_range*0.4 if prev_range>0 else False

        # Previous day closed in upper half of range?
        close_pos=(pd['close']-pd['low'])/rng if rng>0 else 0.5

        # Range expansion: today's first hour range vs yesterday
        first_bars=db[:SCAN_BAR+1]
        fh_range=(max(b['high'] for b in first_bars)-min(b['low'] for b in first_bars))/entry*100
        yd_range_pct=rng/entry*100
        range_ratio=fh_range/yd_range_pct if yd_range_pct>0 else 1

        # First hour already moved UP?
        first_hr_up=(db[SCAN_BAR]['close']-db[0]['open'])/db[0]['open']*100

        # Gap up from prev close
        gap=(db[0]['open']-pd['close'])/pd['close']*100

        # VIX
        vix=vix_data.get(date,0)

        # Today's open vs R3 (opened above R3 = dangerous)
        open_vs_r3=(db[0]['open']-r3)/entry*100

        trades.append({
            'pnl':pnl_rs,'win':pnl_rs>0,'date':date,'sym':sym,
            'consec_down':consec_down,'fall_5d':round(fall_5d,2),'fall_3d':round(fall_3d,2),
            'is_hammer':is_hammer,'close_pos':round(close_pos,2),
            'range_ratio':round(range_ratio,2),'first_hr_up':round(first_hr_up,3),
            'gap':round(gap,2),'vix':round(vix,1),'open_vs_r3':round(open_vs_r3,3),
        })

def avg(lst): return sum(lst)/len(lst) if lst else 0

total_n=len(trades); total_pnl=sum(t['pnl'] for t in trades)
baseline_per=total_pnl/total_n
baseline_wr=sum(1 for t in trades if t['win'])/total_n*100
print(f'Total: {total_n} trades, WR={baseline_wr:.0f}%, Rs {baseline_per:+,.0f}/trade\n')

# ═══════════════════════════════════════════════════════════════
print('='*90)
print('FIND THE TRAPS — Extreme values where WR drops badly')
print('Look at each feature: what extreme value = guaranteed loss?')
print('='*90)

def test_bucket(trades_sub, label):
    if len(trades_sub)<20: return
    n=len(trades_sub); w=sum(1 for t in trades_sub if t['win']); wr=w/n*100
    total=sum(t['pnl'] for t in trades_sub); per=total/n
    pct_of_all=n/total_n*100
    marker=' ← TRAP!' if wr<55 else ' ← WEAK' if wr<62 else ''
    print(f'    {label:>35}: {n:>4} trades ({pct_of_all:>4.1f}%), WR={wr:.0f}%, Rs {per:>+7,.0f}/trade{marker}')

print(f'\n  CONSECUTIVE DOWN DAYS:')
for cd in range(0,8):
    test_bucket([t for t in trades if t['consec_down']==cd], f'Exactly {cd} days down')

print(f'\n  FALL IN LAST 5 DAYS:')
for lo,hi in [(0,1),(1,2),(2,3),(3,4),(4,5),(5,7),(7,10),(10,99)]:
    test_bucket([t for t in trades if lo<=t['fall_5d']<hi], f'Fall {lo}-{hi}%')

print(f'\n  FALL IN LAST 3 DAYS:')
for lo,hi in [(0,1),(1,2),(2,3),(3,4),(4,6),(6,99)]:
    test_bucket([t for t in trades if lo<=t['fall_3d']<hi], f'Fall {lo}-{hi}%')

print(f'\n  FIRST HOUR DIRECTION:')
for lo,hi in [(-99,-0.5),(-0.5,-0.2),(-0.2,0),(0,0.2),(0.2,0.5),(0.5,1.0),(1.0,99)]:
    test_bucket([t for t in trades if lo<=t['first_hr_up']<hi], f'FirstHr {lo:+.1f} to {hi:+.1f}%')

print(f'\n  GAP FROM PREV CLOSE:')
for lo,hi in [(-99,-1),(-1,-0.5),(-0.5,0),(0,0.5),(0.5,1),(1,2),(2,99)]:
    test_bucket([t for t in trades if lo<=t['gap']<hi], f'Gap {lo:+.1f} to {hi:+.1f}%')

print(f'\n  PREV CLOSE POSITION IN RANGE:')
for lo,hi in [(0,0.2),(0.2,0.3),(0.3,0.4),(0.4,0.5),(0.5,0.6),(0.6,0.8),(0.8,1.0)]:
    test_bucket([t for t in trades if lo<=t['close_pos']<hi], f'ClosePos {lo:.1f}-{hi:.1f}')

print(f'\n  RANGE RATIO (today first hr / yesterday):')
for lo,hi in [(0,0.3),(0.3,0.5),(0.5,0.7),(0.7,1.0),(1.0,1.5),(1.5,99)]:
    test_bucket([t for t in trades if lo<=t['range_ratio']<hi], f'RangeRatio {lo:.1f}-{hi:.1f}')

print(f'\n  OPEN vs R3:')
for lo,hi in [(-99,-0.5),(-0.5,-0.2),(-0.2,0),(0,0.2),(0.2,0.5),(0.5,99)]:
    test_bucket([t for t in trades if lo<=t['open_vs_r3']<hi], f'OpenVsR3 {lo:+.1f} to {hi:+.1f}%')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TRAP RULES — Skip ONLY the extreme losers (keep 90%+ of trades)')
print('='*90)

traps=[
    ('ConsecDown >= 5 (exhausted)', lambda t: t['consec_down']>=5),
    ('ConsecDown >= 6', lambda t: t['consec_down']>=6),
    ('Fall5d >= 7% (crashed)', lambda t: t['fall_5d']>=7),
    ('Fall5d >= 8%', lambda t: t['fall_5d']>=8),
    ('Fall3d >= 5%', lambda t: t['fall_3d']>=5),
    ('Fall3d >= 4%', lambda t: t['fall_3d']>=4),
    ('FirstHr > +0.8% (rallying already)', lambda t: t['first_hr_up']>0.8),
    ('FirstHr > +0.5%', lambda t: t['first_hr_up']>0.5),
    ('Gap up > +1.5% (gap reversal)', lambda t: t['gap']>1.5),
    ('Gap up > +1.0%', lambda t: t['gap']>1.0),
    ('ClosePos > 0.7 (closed near high)', lambda t: t['close_pos']>0.7),
    ('ClosePos > 0.6', lambda t: t['close_pos']>0.6),
    ('RangeRatio > 1.5 (already exploded)', lambda t: t['range_ratio']>1.5),
    ('OpenVsR3 > +0.3% (opened above R3)', lambda t: t['open_vs_r3']>0.3),
    # Combos
    ('ConsecDown>=5 OR Fall5d>=7%', lambda t: t['consec_down']>=5 or t['fall_5d']>=7),
    ('ConsecDown>=5 OR Fall3d>=5%', lambda t: t['consec_down']>=5 or t['fall_3d']>=5),
    ('ConsecDown>=5 OR FirstHr>+0.8%', lambda t: t['consec_down']>=5 or t['first_hr_up']>0.8),
    ('ConsecDown>=5 OR Gap>+1.5%', lambda t: t['consec_down']>=5 or t['gap']>1.5),
    ('ConsecDown>=5 OR Fall5d>=7% OR Gap>1.5%', lambda t: t['consec_down']>=5 or t['fall_5d']>=7 or t['gap']>1.5),
    ('ConsecDown>=5 OR Fall3d>=5% OR FirstHr>0.8%', lambda t: t['consec_down']>=5 or t['fall_3d']>=5 or t['first_hr_up']>0.8),
    ('ConsecDown>=6 OR Fall5d>=8% OR Gap>1.5%', lambda t: t['consec_down']>=6 or t['fall_5d']>=8 or t['gap']>1.5),
]

print(f'\n  {"Trap rule (SKIP if true)":>55} {"Skip":>5} {"Skip%":>5} {"Remain":>6} {"WR":>4} {"/trade":>8} {"SavedLoss":>9} {"LostWins":>8}')
print(f'  {"-"*110}')
for name, is_trap in traps:
    trapped=[t for t in trades if is_trap(t)]
    remain=[t for t in trades if not is_trap(t)]
    if len(remain)<100 or len(trapped)<10: continue

    skip_n=len(trapped); skip_pct=skip_n/total_n*100
    r_n=len(remain); r_w=sum(1 for t in remain if t['win']); r_wr=r_w/r_n*100
    r_total=sum(t['pnl'] for t in remain); r_per=r_total/r_n
    saved_losses=sum(1 for t in trapped if not t['win'])
    lost_wins=sum(1 for t in trapped if t['win'])
    trap_wr=sum(1 for t in trapped if t['win'])/len(trapped)*100
    imp=(r_per-baseline_per)/baseline_per*100
    marker=' <<<' if imp>2 and skip_pct<15 and lost_wins<saved_losses else ''
    print(f'  {name:>55} {skip_n:>5} {skip_pct:>4.1f}% {r_n:>6} {r_wr:>3.0f}% Rs{r_per:>+7,.0f} {saved_losses:>9}L {lost_wins:>8}W (trap WR={trap_wr:.0f}%){marker}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('WALK-FORWARD VERIFY — Do traps hold on unseen data?')
print('='*90)

train=[t for t in trades if t['date']<'2025-01-01']
test=[t for t in trades if t['date']>='2025-01-01']

best_traps=[
    ('NO FILTER', lambda t: False),
    ('ConsecDown>=5', lambda t: t['consec_down']>=5),
    ('ConsecDown>=5 OR Fall5d>=7%', lambda t: t['consec_down']>=5 or t['fall_5d']>=7),
    ('ConsecDown>=5 OR Fall3d>=5%', lambda t: t['consec_down']>=5 or t['fall_3d']>=5),
    ('ConsecDown>=5 OR Fall3d>=5% OR FirstHr>0.8%', lambda t: t['consec_down']>=5 or t['fall_3d']>=5 or t['first_hr_up']>0.8),
    ('ConsecDown>=6 OR Fall5d>=8% OR Gap>1.5%', lambda t: t['consec_down']>=6 or t['fall_5d']>=8 or t['gap']>1.5),
]

print(f'\n  {"Trap":>55} {"Tr.N":>5} {"Tr/tr":>8} {"Te.N":>5} {"Te/tr":>8} {"Drop":>5} {"TeWR":>4}')
print(f'  {"-"*95}')
for name, is_trap in best_traps:
    tr_r=[t for t in train if not is_trap(t)]
    te_r=[t for t in test if not is_trap(t)]
    if len(tr_r)<100 or len(te_r)<50: continue
    tr_per=sum(t['pnl'] for t in tr_r)/len(tr_r)
    te_per=sum(t['pnl'] for t in te_r)/len(te_r)
    te_wr=sum(1 for t in te_r if t['win'])/len(te_r)*100
    drop=(te_per-tr_per)/tr_per*100
    marker=' <<<' if te_per>3200 else ''
    print(f'  {name:>55} {len(tr_r):>5} Rs{tr_per:>+7,.0f} {len(te_r):>5} Rs{te_per:>+7,.0f} {drop:>+4.0f}% {te_wr:>3.0f}%{marker}')

# Show what we're skipping
print(f'\n  What gets trapped (examples):')
best_trap = lambda t: t['consec_down']>=5 or t['fall_5d']>=7
trapped=[t for t in trades if best_trap(t)]
trapped.sort(key=lambda x:x['pnl'])
print(f'  Worst trapped trades (these are the traps we avoid):')
for t in trapped[:10]:
    print(f'    {t["date"]} {t["sym"]:>12}: ConsecDown={t["consec_down"]}, Fall5d={t["fall_5d"]}%, '
          f'PnL=Rs {t["pnl"]:+,.0f} {"WIN" if t["win"] else "LOSS"}')
print(f'  Best trapped trades (these are wins we lose):')
for t in sorted(trapped, key=lambda x:x['pnl'], reverse=True)[:10]:
    print(f'    {t["date"]} {t["sym"]:>12}: ConsecDown={t["consec_down"]}, Fall5d={t["fall_5d"]}%, '
          f'PnL=Rs {t["pnl"]:+,.0f} {"WIN" if t["win"] else "LOSS"}')
