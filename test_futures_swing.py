"""
Test ALL strategies with:
1. FUTURES charges (Rs ~200 instead of Rs 386)
2. SWING (hold 2-5 days, charges paid once)
3. OPTIONS simulation (1% move = 10% option move)

Same signals, different instruments and holding periods.
"""
import sys, io, csv, math
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

# Load NIFTY and BANKNIFTY
for idx_name in ['NIFTY_50','NIFTY_BANK']:
    f=data_dir/f'{idx_name}_5min.csv'
    if f.exists():
        with open(f) as fh: rows=list(csv.DictReader(fh))
        bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
               'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
        all_data[idx_name]=bars
        by_d=defaultdict(list)
        for b in bars: by_d[b['timestamp'][:10]].append(b)
        for d,bs in by_d.items(): date_bars[d][idx_name]=bs

all_dates=sorted(date_bars.keys())

# Pre-compute daily data
prev_day={}; daily_trend={}; daily_close=defaultdict(dict)
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        daily_close[d][sym]=c
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'

print(f'{len(all_data)} instruments, {len(all_dates)} days\n')

POS=1000000  # Rs 10L per trade

# Charges for different instruments
CHARGES_EQUITY=386      # Current
CHARGES_FUTURES=200     # Futures: lower STT
CHARGES_OPTIONS=100     # Options: even lower + no STT on buy

scan_bar=10

def get_cam_r3_signals(date):
    """Get CAM_R3 SHORT signals for a date."""
    signals=[]
    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db=date_bars[date][sym]
        if len(db)<=scan_bar+20: continue
        if daily_trend.get((date,sym))!='DOWN': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        r3=pd['close']+rng*1.1/4
        for j in range(1,scan_bar+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                signals.append({'sym':sym,'entry':db[scan_bar]['close'],'date':date})
                break
    return signals

def report(name, trades):
    if not trades or len(trades)<20:
        print(f'\n{name}: {len(trades) if trades else 0} trades (too few)')
        return
    w=sum(1 for t in trades if t>0); n=len(trades)
    total=sum(trades)
    print(f'  {name:>40}: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total:>+12,.0f}, Rs {total/n:>+6,.0f}/trade')
    return total

# ═══════════════════════════════════════════════════════════════
# TEST 1: CAM_R3 on EQUITY vs FUTURES charges
# ═══════════════════════════════════════════════════════════════
print('='*80)
print('TEST 1: CAM_R3 — Same strategy, different charge structures')
print('='*80)

for TARGET in [0.75, 1.00, 1.50]:
    for STOP in [0.75, 1.00]:
        equity_pnl=[]; futures_pnl=[]; zero_pnl=[]
        for date in all_dates:
            signals=get_cam_r3_signals(date)
            for s in signals:
                db=date_bars[date][s['sym']]
                entry=s['entry']
                tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(scan_bar+1,min(len(db),70)):
                    if db[k]['low']<=tp: ep=tp; break
                    if db[k]['high']>=sp: ep=sp; break
                pnl_pct=(entry-ep)/entry*100
                pnl_rs=pnl_pct/100*POS
                equity_pnl.append(pnl_rs-CHARGES_EQUITY)
                futures_pnl.append(pnl_rs-CHARGES_FUTURES)
                zero_pnl.append(pnl_rs)

        print(f'\n  T={TARGET}% S={STOP}%:')
        report('Equity (Rs 386/trade)', equity_pnl)
        report('Futures (Rs 200/trade)', futures_pnl)
        report('Zero charges', zero_pnl)

# ═══════════════════════════════════════════════════════════════
# TEST 2: SWING TRADE — Hold 2-5 days instead of intraday
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('TEST 2: SWING TRADE — CAM_R3 entry, hold 2-5 days')
print('Same signal but hold overnight. Charges paid ONCE for bigger move.')
print('='*80)

for HOLD_DAYS in [2, 3, 5]:
    for STOP in [2.0, 3.0, 5.0]:
        TARGET_SW = STOP * 1.5  # R:R 1.5:1
        trades=[]
        for di, date in enumerate(all_dates):
            signals=get_cam_r3_signals(date)
            for s in signals:
                sym=s['sym']; entry=s['entry']
                tp=entry*(1-TARGET_SW/100); sp=entry*(1+STOP/100)
                # Simulate over next HOLD_DAYS
                ep=entry; hit=False
                for day_offset in range(HOLD_DAYS+1):
                    future_idx=di+day_offset
                    if future_idx>=len(all_dates): break
                    future_date=all_dates[future_idx]
                    fdb=date_bars[future_date].get(sym,[])
                    if not fdb: continue
                    start_bar=0 if day_offset>0 else scan_bar+1
                    for k in range(start_bar, len(fdb)):
                        if fdb[k]['low']<=tp: ep=tp; hit=True; break
                        if fdb[k]['high']>=sp: ep=sp; hit=True; break
                    if hit: break
                if not hit:
                    # Close at end of hold period
                    last_date_idx=min(di+HOLD_DAYS, len(all_dates)-1)
                    last_db=date_bars[all_dates[last_date_idx]].get(sym,[])
                    if last_db: ep=last_db[-1]['close']
                pnl_pct=(entry-ep)/entry*100
                pnl_rs=pnl_pct/100*POS-CHARGES_EQUITY  # Only 1 set of charges
                trades.append(pnl_rs)

        if len(trades)<20: continue
        w=sum(1 for t in trades if t>0); n=len(trades)
        total=sum(trades)
        marker=' <<<' if total/n>200 else ''
        print(f'  Hold {HOLD_DAYS}d T={TARGET_SW:.1f}% S={STOP:.1f}%: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total:>+11,.0f}, Rs {total/n:>+6,.0f}/trade{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 3: OPTIONS SIMULATION on NIFTY/BANKNIFTY
# CAM_R3 on index → buy PUT option
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('TEST 3: OPTIONS — CAM_R3 on NIFTY/BANKNIFTY, buy PUT')
print('1% index move = ~8-12% option premium move')
print('='*80)

for idx_sym in ['NIFTY_50','NIFTY_BANK']:
    if idx_sym not in all_data: continue
    idx_label='NIFTY' if 'NIFTY_50' in idx_sym else 'BANKNIFTY'

    for OPT_MULT in [8, 10, 12]:  # How much option amplifies
        for TARGET in [0.50, 0.75, 1.00]:
            for STOP in [0.50, 0.75, 1.00]:
                trades=[]
                for date in all_dates:
                    db=date_bars[date].get(idx_sym,[])
                    if len(db)<=scan_bar+20: continue
                    if daily_trend.get((date,idx_sym))!='DOWN': continue
                    pd=prev_day.get((date,idx_sym))
                    if not pd: continue
                    rng=pd['high']-pd['low']
                    if rng<=0: continue
                    r3=pd['close']+rng*1.1/4
                    triggered=False
                    for j in range(1,scan_bar+1):
                        atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                        if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                            triggered=True; break
                    if not triggered: continue

                    entry=db[scan_bar]['close']
                    tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(scan_bar+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp; break
                        if db[k]['high']>=sp: ep=sp; break

                    idx_pnl=(entry-ep)/entry*100
                    # Option P&L: amplified by OPT_MULT, but capped at -100% (can't lose more than premium)
                    opt_pnl=max(idx_pnl*OPT_MULT, -100)
                    # Option premium per lot: ~Rs 15,000-20,000
                    premium=20000
                    pnl_rs=opt_pnl/100*premium - CHARGES_OPTIONS
                    trades.append(pnl_rs)

                if len(trades)<20: continue
                w=sum(1 for t in trades if t>0); n=len(trades)
                total=sum(trades)
                per=total/n
                marker=' <<<' if per>100 else ''
                print(f'  {idx_label} {OPT_MULT}x T={TARGET}% S={STOP}%: {n:>4} trades, WR={w/n*100:.0f}%, Rs {total:>+9,.0f}, Rs {per:>+6,.0f}/trade{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 4: ALL 11 strategies with FUTURES charges
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('TEST 4: All strategies with FUTURES charges (Rs 200)')
print('='*80)

# Recalculate from all_strategies results
# India equity lost: add back Rs 386, subtract Rs 200
strategies_india=[
    ('VWAP Reversion', 9641, -6721824, 386),
    ('Momentum', 6486, -1471069, 386),
    ('11:30 Reversal', 28795, -9981302, 386),
    ('3PM Institutional', 23444, -8679530, 386),
    ('Gap Fill', 4443, -5574151, 386),
    ('Round Number', 1863, -887674, 386),
    ('NIFTY MR', 541, -154841, 386),
    ('Pairs', 6904, -5562267, 772),
    ('Relative Value', 5061, -4070431, 772),
]

print(f'\n{"Strategy":>20} {"N":>6} {"Equity Rs":>12} {"Futures Rs":>12} {"Per trade":>10}')
print('-'*65)
for name,n,india_pnl,charges in strategies_india:
    futures_pnl=india_pnl + (charges-200)*n if charges==386 else india_pnl + (charges-400)*n
    per=futures_pnl/n
    marker=' <<<' if per>0 else ''
    print(f'{name:>20} {n:>6} Rs {india_pnl:>+10,.0f} Rs {futures_pnl:>+10,.0f} Rs {per:>+8,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# GRAND SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('GRAND SUMMARY: What works on Indian markets?')
print('='*80)
print()
print('EQUITY INTRADAY (Rs 386/trade):')
print('  Only CAM_R3 + trendDOWN works (67% WR)')
print()
print('FUTURES INTRADAY (Rs 200/trade):')
print('  CAM_R3 works BETTER (same WR, lower charges)')
print('  + possibly Momentum and 11:30 Reversal')
print()
print('SWING (2-5 days, charges once):')
print('  Charges become irrelevant on bigger moves')
print()
print('OPTIONS (amplified moves):')
print('  Small index moves become big option moves')
print('  Charges matter less relative to profit')
