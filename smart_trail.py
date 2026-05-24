"""
SMART TRAILING STOP — Conditional, only activates after trade proves itself.

Rules:
  - Entry: Target 1.75%, Stop 1.50% (unchanged)
  - Phase 1 (MFE < threshold): NO trailing. Let it run.
  - Phase 2 (MFE >= threshold): Lock in floor profit. Don't give it ALL back.

Test different activation thresholds and lock-in levels.
Key: Must NOT hurt existing winners that go straight to target.
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

POS=1000000; CHARGES=386; SB=10; TARGET=1.75; STOP=1.50

# Get CD 0-2 signals
signals=[]
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

        signals.append({'date':date,'sym':sym,'entry':db[SB]['close']})

print(f'{len(signals)} signals (CD 0-2)\n')

def simulate(signals, trail_rules):
    """
    trail_rules: list of (activate_at, lock_in)
    e.g. [(0.75, 0.25), (1.0, 0.5), (1.25, 0.75)]
    means: when MFE hits 0.75%, move stop to lock 0.25%
           when MFE hits 1.0%, move stop to lock 0.5%
           when MFE hits 1.25%, move stop to lock 0.75%
    Sort by activate_at ascending.
    """
    trail_rules = sorted(trail_rules, key=lambda x: x[0])
    trades=[]
    for s in signals:
        date=s['date']; sym=s['sym']; entry=s['entry']
        db=date_bars[date][sym]
        tp=entry*(1-TARGET/100)
        original_stop=entry*(1+STOP/100)

        current_stop=original_stop
        mfe=0; activated_level=0
        ep=db[min(69,len(db)-1)]['close']
        exit_r='eod'

        for k in range(SB+1, min(len(db),70)):
            bar_low=db[k]['low']; bar_high=db[k]['high']; bar_close=db[k]['close']

            # Check target first (price went DOWN enough — we're short)
            if bar_low<=tp:
                ep=tp; exit_r='target'; break

            # Update MFE
            fav=(entry-bar_low)/entry*100
            if fav>mfe:
                mfe=fav
                # Check if any trail rule activates
                for activate_at, lock_in in trail_rules:
                    if mfe>=activate_at and activate_at>activated_level:
                        # Move stop to lock in profit
                        new_stop=entry*(1-lock_in/100)  # Lock in lock_in%
                        if new_stop<current_stop:  # Only tighten, never widen
                            current_stop=new_stop
                        activated_level=activate_at

            # Check stop (using current, possibly trailed stop)
            if bar_high>=current_stop:
                ep=current_stop; exit_r='trail_stop' if current_stop<original_stop else 'stop'
                break

        pnl_pct=(entry-ep)/entry*100
        pnl=pnl_pct/100*POS-CHARGES
        trades.append({'pnl':pnl,'win':pnl>0,'exit':exit_r,'mfe':mfe,'pnl_pct':pnl_pct})
    return trades

# ═══════════════════════════════════════════════════════════════
print('='*90)
print('BASELINE: No trailing stop')
print('='*90)

baseline=simulate(signals, [])
n=len(baseline); w=sum(1 for t in baseline if t['win'])
total=sum(t['pnl'] for t in baseline)
print(f'  {n} trades, WR={w/n*100:.0f}%, Rs {total/n:+,.0f}/trade, Total Rs {total:+,.0f}')

from collections import Counter
exits=Counter(t['exit'] for t in baseline)
print(f'  Exits: {dict(exits)}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST: Single activation level')
print('When trade goes X% in favor, lock in Y%')
print('='*90)

print(f'\n  {"Rule":>45} {"N":>5} {"WR":>4} {"/trade":>8} {"Total":>13} {"Change":>7} {"Exits":>30}')
print(f'  {"-"*120}')

configs=[
    # (activate, lock_in)
    ('No trail (baseline)', []),
    ('At +0.50% lock +0.00% (breakeven)', [(0.50, 0.00)]),
    ('At +0.75% lock +0.00% (breakeven)', [(0.75, 0.00)]),
    ('At +0.75% lock +0.25%', [(0.75, 0.25)]),
    ('At +1.00% lock +0.00% (breakeven)', [(1.00, 0.00)]),
    ('At +1.00% lock +0.25%', [(1.00, 0.25)]),
    ('At +1.00% lock +0.50%', [(1.00, 0.50)]),
    ('At +1.25% lock +0.25%', [(1.25, 0.25)]),
    ('At +1.25% lock +0.50%', [(1.25, 0.50)]),
    ('At +1.25% lock +0.75%', [(1.25, 0.75)]),
    ('At +1.40% lock +0.50%', [(1.40, 0.50)]),
    ('At +1.40% lock +0.75%', [(1.40, 0.75)]),
    ('At +1.40% lock +1.00%', [(1.40, 1.00)]),
    ('At +1.50% lock +0.75%', [(1.50, 0.75)]),
    ('At +1.50% lock +1.00%', [(1.50, 1.00)]),
]

baseline_per=total/n
for name, rules in configs:
    trades=simulate(signals, rules)
    n2=len(trades); w2=sum(1 for t in trades if t['win'])
    total2=sum(t['pnl'] for t in trades); per2=total2/n2
    change=(per2-baseline_per)/baseline_per*100
    exits=Counter(t['exit'] for t in trades)
    exit_str=f"tgt={exits.get('target',0)} stp={exits.get('stop',0)} trl={exits.get('trail_stop',0)} eod={exits.get('eod',0)}"
    marker=' <<<' if change>1 else ''
    print(f'  {name:>45} {n2:>5} {w2/n2*100:>3.0f}% Rs{per2:>+7,.0f} Rs{total2:>+12,.0f} {change:>+5.1f}% {exit_str}{marker}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST: Multi-step trailing (progressive lock-in)')
print('="*90')

multi_configs=[
    ('No trail', []),
    # Progressive: each level locks more
    ('Step: 0.75→BE, 1.25→+0.50', [(0.75, 0.00), (1.25, 0.50)]),
    ('Step: 1.0→BE, 1.25→+0.50', [(1.00, 0.00), (1.25, 0.50)]),
    ('Step: 1.0→BE, 1.40→+0.75', [(1.00, 0.00), (1.40, 0.75)]),
    ('Step: 1.0→+0.25, 1.40→+0.75', [(1.00, 0.25), (1.40, 0.75)]),
    ('Step: 1.0→+0.25, 1.25→+0.50, 1.50→+1.0', [(1.00, 0.25), (1.25, 0.50), (1.50, 1.00)]),
    ('Step: 0.75→BE, 1.0→+0.25, 1.25→+0.50, 1.50→+1.0', [(0.75, 0.00), (1.00, 0.25), (1.25, 0.50), (1.50, 1.00)]),
    ('Step: 1.0→BE, 1.25→+0.50, 1.50→+1.0', [(1.00, 0.00), (1.25, 0.50), (1.50, 1.00)]),
    ('Step: 1.25→+0.50, 1.50→+1.0', [(1.25, 0.50), (1.50, 1.00)]),
    # Late activation only (protect almost-won without touching early)
    ('Late only: 1.40→+0.75', [(1.40, 0.75)]),
    ('Late only: 1.40→+1.0', [(1.40, 1.00)]),
    ('Late only: 1.50→+1.0', [(1.50, 1.00)]),
]

print(f'\n  {"Rule":>55} {"WR":>4} {"/trade":>8} {"Total":>13} {"Chg":>5} {"Tgt":>4} {"Stp":>4} {"Trl":>4} {"EOD":>4}')
print(f'  {"-"*105}')

results=[]
for name, rules in multi_configs:
    trades=simulate(signals, rules)
    n2=len(trades); w2=sum(1 for t in trades if t['win'])
    total2=sum(t['pnl'] for t in trades); per2=total2/n2
    change=(per2-baseline_per)/baseline_per*100
    exits=Counter(t['exit'] for t in trades)
    marker=' <<<' if change>1 else ''
    results.append((name, n2, w2/n2*100, per2, total2, change, exits))
    print(f'  {name:>55} {w2/n2*100:>3.0f}% Rs{per2:>+7,.0f} Rs{total2:>+12,.0f} {change:>+4.1f}% '
          f'{exits.get("target",0):>4} {exits.get("stop",0):>4} {exits.get("trail_stop",0):>4} {exits.get("eod",0):>4}{marker}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('DEEP DIVE: What happens to the 44 "almost won" trades with best trail?')
print('='*90)

# Find best trail config
# Simulate baseline and trail side by side for "almost won" trades
baseline_trades=simulate(signals, [])
trail_trades=simulate(signals, [(1.00, 0.25), (1.40, 0.75)])

# Find trades where baseline lost but had high MFE
print(f'\n  Trades that had MFE >= 0.88% (almost target) and LOST in baseline:')
print(f'  {"#":>3} {"MFE":>6} {"Base PnL":>10} {"Base Exit":>10} {"Trail PnL":>10} {"Trail Exit":>10} {"Saved?":>8}')
print(f'  {"-"*65}')

saved=0; hurt=0
for i,(b,t) in enumerate(zip(baseline_trades, trail_trades)):
    if b['mfe']>=0.88 and not b['win']:
        diff=t['pnl']-b['pnl']
        status='SAVED' if t['pnl']>b['pnl'] else 'SAME' if abs(diff)<10 else 'HURT'
        if t['pnl']>b['pnl']: saved+=1
        elif t['pnl']<b['pnl']: hurt+=1
        print(f'  {i:>5} {b["mfe"]:>5.2f}% Rs{b["pnl"]:>+9,.0f} {b["exit"]:>10} Rs{t["pnl"]:>+9,.0f} {t["exit"]:>10} {status:>8}')

print(f'\n  Saved: {saved} | Hurt: {hurt}')

# Walk-forward
print(f'\n{"="*90}')
print('WALK-FORWARD VERIFY')
print('='*90)

train_sigs=[s for s in signals if s['date']<'2025-01-01']
test_sigs=[s for s in signals if s['date']>='2025-01-01']

best_configs=[
    ('No trail', []),
    ('At +1.00% lock BE', [(1.00, 0.00)]),
    ('At +1.00% lock +0.25, +1.40 lock +0.75', [(1.00, 0.25), (1.40, 0.75)]),
    ('At +1.25% lock +0.50', [(1.25, 0.50)]),
    ('At +1.40% lock +0.75', [(1.40, 0.75)]),
    ('Step: 1.0→BE, 1.25→+0.50, 1.50→+1.0', [(1.00, 0.00), (1.25, 0.50), (1.50, 1.00)]),
]

print(f'\n  {"Config":>50} {"Train/tr":>9} {"Test/tr":>9} {"Drop":>5}')
print(f'  {"-"*80}')
for name, rules in best_configs:
    tr=simulate(train_sigs, rules)
    te=simulate(test_sigs, rules)
    tr_per=sum(t['pnl'] for t in tr)/len(tr)
    te_per=sum(t['pnl'] for t in te)/len(te)
    drop=(te_per-tr_per)/tr_per*100
    marker=' <<<' if te_per>6200 else ''
    print(f'  {name:>50} Rs{tr_per:>+8,.0f} Rs{te_per:>+8,.0f} {drop:>+4.0f}%{marker}')
