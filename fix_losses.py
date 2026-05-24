"""
Can we DETECT choppy days and trend reversals BEFORE entering?
Look at data available at bar 10 (entry time) — before we commit.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt
import math

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
prev_day={}; daily_trend={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
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
print(f'Loaded.\n')

# Compute all trades with PRE-ENTRY features
trades = []
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
            fav=(entry-db[k]['low'])/entry*100
            adv=(db[k]['high']-entry)/entry*100
            mfe=max(mfe,fav); mae=max(mae,adv)
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break

        pnl_pct=(entry-ep)/entry*100
        pnl_rs=pnl_pct/100*POS - CHARGES

        # Categorize loss
        cat='WIN'
        if pnl_rs<=0:
            if exit_r=='stop': cat='STOP'
            elif mfe>=TARGET*0.5: cat='ALMOST'
            elif mfe<0.3 and mae<0.5: cat='FLAT'
            elif mae>0.5 and mfe<0.3: cat='WRONG_DIR'
            else: cat='CHOPPY'

        # ═══ PRE-ENTRY FEATURES (available at bar 10, before we trade) ═══

        first_bars = db[:SCAN_BAR+1]  # Bar 0-10

        # 1. CHOPPINESS: How many times did price change direction in first 10 bars?
        direction_changes = 0
        for k in range(2, len(first_bars)):
            prev_dir = first_bars[k-1]['close'] - first_bars[k-2]['close']
            curr_dir = first_bars[k]['close'] - first_bars[k-1]['close']
            if prev_dir * curr_dir < 0:  # Sign changed
                direction_changes += 1

        # 2. FIRST HOUR RANGE vs previous day range
        first_hr_high = max(b['high'] for b in first_bars)
        first_hr_low = min(b['low'] for b in first_bars)
        first_hr_range = (first_hr_high - first_hr_low) / entry * 100
        range_ratio = first_hr_range / (rng/entry*100) if rng > 0 else 1

        # 3. FIRST HOUR TREND: Where is price relative to open?
        first_hr_move = (db[SCAN_BAR]['close'] - db[0]['open']) / db[0]['open'] * 100

        # 4. BAR-TO-BAR CONSISTENCY: How many of first 10 bars closed DOWN?
        bars_down = sum(1 for k in range(1, SCAN_BAR+1) if db[k]['close'] < db[k-1]['close'])

        # 5. VOLUME TREND: Is volume increasing or decreasing?
        vol_first5 = sum(b['volume'] for b in db[:5]) / 5
        vol_last5 = sum(b['volume'] for b in db[5:SCAN_BAR+1]) / max(1, len(db[5:SCAN_BAR+1]))
        vol_trend = vol_last5 / vol_first5 if vol_first5 > 0 else 1

        # 6. GAP from previous close
        gap = (db[0]['open'] - pd['close']) / pd['close'] * 100

        # 7. WHERE is price relative to R3? (above/below/at)
        r3_position = (entry - r3) / entry * 100  # Negative = below R3

        # 8. ATR of first 10 bars (volatility)
        atr_10 = sum(db[k]['high']-db[k]['low'] for k in range(SCAN_BAR+1)) / (SCAN_BAR+1)
        atr_pct = atr_10 / entry * 100

        # 9. Previous day close position within range (0=low, 1=high)
        pd_close_pos = (pd['close'] - pd['low']) / rng if rng > 0 else 0.5

        # 10. VIX
        vix = vix_data.get(date, 0)

        # 11. Body-to-wick ratio of first 10 bars (strong bars = more body, less wick)
        total_body = sum(abs(b['close']-b['open']) for b in first_bars)
        total_range = sum(b['high']-b['low'] for b in first_bars)
        body_ratio = total_body / total_range if total_range > 0 else 0.5

        # 12. Number of bars that closed BELOW previous bar's low (strong selling)
        strong_down_bars = sum(1 for k in range(1, SCAN_BAR+1) if db[k]['close'] < db[k-1]['low'])

        trades.append({
            'cat':cat, 'pnl':pnl_rs, 'sym':sym, 'date':date,
            'dir_changes':direction_changes, 'range_ratio':round(range_ratio,2),
            'first_hr_move':round(first_hr_move,3), 'bars_down':bars_down,
            'vol_trend':round(vol_trend,2), 'gap':round(gap,2),
            'r3_pos':round(r3_position,3), 'atr_pct':round(atr_pct,3),
            'pd_close_pos':round(pd_close_pos,2), 'vix':vix,
            'body_ratio':round(body_ratio,3), 'strong_down':strong_down_bars,
            'first_hr_range':round(first_hr_range,3),
        })

def avg(lst): return sum(lst)/len(lst) if lst else 0

wins = [t for t in trades if t['cat']=='WIN']
choppy = [t for t in trades if t['cat']=='CHOPPY']
wrong = [t for t in trades if t['cat']=='WRONG_DIR']
all_losses = [t for t in trades if t['cat']!='WIN']

# ═══════════════════════════════════════════════════════════════
print('='*90)
print('FEATURE COMPARISON: What looks different BEFORE entry?')
print('='*90)

features = [
    ('Direction changes (0-9)', 'dir_changes'),
    ('First hr range %', 'first_hr_range'),
    ('Range ratio (today/yesterday)', 'range_ratio'),
    ('First hr move %', 'first_hr_move'),
    ('Bars down (of 10)', 'bars_down'),
    ('Volume trend (later/earlier)', 'vol_trend'),
    ('Gap %', 'gap'),
    ('R3 position %', 'r3_pos'),
    ('ATR %', 'atr_pct'),
    ('Prev close position (0-1)', 'pd_close_pos'),
    ('Body/wick ratio', 'body_ratio'),
    ('Strong down bars', 'strong_down'),
    ('VIX', 'vix'),
]

print(f'\n  {"Feature":>35} {"WINS":>8} {"CHOPPY":>8} {"WRONG":>8} {"ALL LOSS":>8}')
print(f'  {"-"*75}')
for name, key in features:
    w = avg([t[key] for t in wins])
    c = avg([t[key] for t in choppy])
    wr = avg([t[key] for t in wrong])
    al = avg([t[key] for t in all_losses])
    # Highlight if significant difference
    diff_c = abs(w-c)/max(abs(w),0.001)*100
    diff_w = abs(w-wr)/max(abs(w),0.001)*100
    marker = ' <<<' if diff_c>15 or diff_w>15 else ''
    print(f'  {name:>35} {w:>8.3f} {c:>8.3f} {wr:>8.3f} {al:>8.3f}{marker}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST CHOPPINESS FILTER: Skip trades with high direction changes')
print('='*90)

for max_dir_changes in range(2, 10):
    filtered = [t for t in trades if t['dir_changes'] <= max_dir_changes]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    skipped_choppy = sum(1 for t in choppy if t['dir_changes'] > max_dir_changes)
    skipped_wins = sum(1 for t in wins if t['dir_changes'] > max_dir_changes)
    print(f'  DirChanges <= {max_dir_changes}: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade '
          f'(skip {len(trades)-n} trades: {skipped_choppy} choppy, {skipped_wins} wins)')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST TREND STRENGTH FILTER: Skip if first hour not clearly DOWN')
print('='*90)

for min_bars_down in range(3, 9):
    filtered = [t for t in trades if t['bars_down'] >= min_bars_down]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    skipped_wrong = sum(1 for t in wrong if t['bars_down'] < min_bars_down)
    skipped_wins = sum(1 for t in wins if t['bars_down'] < min_bars_down)
    print(f'  BarsDown >= {min_bars_down}: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade '
          f'(skip {len(trades)-n} trades: {skipped_wrong} wrong_dir, {skipped_wins} wins)')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST FIRST HOUR MOVE FILTER: Skip if first hour moved UP')
print('='*90)

for max_fh_move in [0.5, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3]:
    filtered = [t for t in trades if t['first_hr_move'] <= max_fh_move]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    skipped_wrong = sum(1 for t in wrong if t['first_hr_move'] > max_fh_move)
    skipped_wins = sum(1 for t in wins if t['first_hr_move'] > max_fh_move)
    print(f'  FirstHrMove <= {max_fh_move:>+5.1f}%: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade '
          f'(skip {len(trades)-n}: {skipped_wrong} wrong_dir, {skipped_wins} wins)')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST BODY RATIO FILTER: Skip weak/indecisive bars')
print('='*90)

for min_body in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55]:
    filtered = [t for t in trades if t['body_ratio'] >= min_body]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    skipped_choppy = sum(1 for t in choppy if t['body_ratio'] < min_body)
    skipped_wins = sum(1 for t in wins if t['body_ratio'] < min_body)
    print(f'  BodyRatio >= {min_body:.2f}: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade '
          f'(skip {len(trades)-n}: {skipped_choppy} choppy, {skipped_wins} wins)')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST STRONG DOWN BARS FILTER: Need strong selling confirmation')
print('='*90)

for min_strong in range(0, 6):
    filtered = [t for t in trades if t['strong_down'] >= min_strong]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    print(f'  StrongDown >= {min_strong}: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST ATR FILTER: Skip low-volatility (flat) setups')
print('='*90)

for min_atr in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4]:
    filtered = [t for t in trades if t['atr_pct'] >= min_atr]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    print(f'  ATR >= {min_atr:.2f}%: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST RANGE RATIO: Skip if today already moved MORE than yesterday')
print('='*90)

for max_rr in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.5]:
    filtered = [t for t in trades if t['range_ratio'] <= max_rr]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    print(f'  RangeRatio <= {max_rr:.1f}: {n:>5} trades, WR={w/n*100:.0f}%, Rs {total/n:>+7,.0f}/trade')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('COMBINATIONS — Best filters together')
print('='*90)

baseline_per = sum(t['pnl'] for t in trades) / len(trades)
print(f'  BASELINE: {len(trades)} trades, Rs {baseline_per:+,.0f}/trade\n')

combos = [
    ('DirChanges<=6', lambda t: t['dir_changes']<=6),
    ('DirChanges<=5', lambda t: t['dir_changes']<=5),
    ('BarsDown>=4', lambda t: t['bars_down']>=4),
    ('BarsDown>=5', lambda t: t['bars_down']>=5),
    ('FirstHr<=+0.1%', lambda t: t['first_hr_move']<=0.1),
    ('FirstHr<=0.0%', lambda t: t['first_hr_move']<=0.0),
    ('BodyRatio>=0.40', lambda t: t['body_ratio']>=0.40),
    ('BodyRatio>=0.45', lambda t: t['body_ratio']>=0.45),
    ('ATR>=0.20%', lambda t: t['atr_pct']>=0.20),
    ('DirChg<=6 + BarsDown>=4', lambda t: t['dir_changes']<=6 and t['bars_down']>=4),
    ('DirChg<=6 + FirstHr<=0.1', lambda t: t['dir_changes']<=6 and t['first_hr_move']<=0.1),
    ('DirChg<=6 + Body>=0.40', lambda t: t['dir_changes']<=6 and t['body_ratio']>=0.40),
    ('BarsDown>=5 + FirstHr<=0.0', lambda t: t['bars_down']>=5 and t['first_hr_move']<=0.0),
    ('DirChg<=5 + BarsDown>=5', lambda t: t['dir_changes']<=5 and t['bars_down']>=5),
    ('DirChg<=6 + BarsDown>=4 + Body>=0.40', lambda t: t['dir_changes']<=6 and t['bars_down']>=4 and t['body_ratio']>=0.40),
    ('DirChg<=6 + BarsDown>=5 + FirstHr<=0.1', lambda t: t['dir_changes']<=6 and t['bars_down']>=5 and t['first_hr_move']<=0.1),
    ('ALL FILTERS: DirChg<=6 + BarsDown>=4 + Body>=0.40 + ATR>=0.20', lambda t: t['dir_changes']<=6 and t['bars_down']>=4 and t['body_ratio']>=0.40 and t['atr_pct']>=0.20),
]

results = []
for name, filt in combos:
    filtered = [t for t in trades if filt(t)]
    if len(filtered) < 100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    per = total/n
    # Count what we skip
    skip_choppy = sum(1 for t in choppy if not filt(t))
    skip_wrong = sum(1 for t in wrong if not filt(t))
    skip_wins = sum(1 for t in wins if not filt(t))
    improvement = (per - baseline_per) / baseline_per * 100
    results.append((name, n, w/n*100, per, total, skip_choppy, skip_wrong, skip_wins, improvement))

results.sort(key=lambda x: x[3], reverse=True)
print(f'  {"Filter":>55} {"N":>5} {"WR":>4} {"/trade":>8} {"Total":>12} {"SkipChop":>8} {"SkipWrng":>8} {"SkipWin":>8} {"Improv":>7}')
print(f'  {"-"*125}')
for name, n, wr, per, total, sc, sw, swi, imp in results:
    marker = ' <<<' if imp > 5 and swi < 200 else ''
    print(f'  {name:>55} {n:>5} {wr:>3.0f}% Rs{per:>+7,.0f} Rs{total:>+11,.0f} {sc:>8} {sw:>8} {swi:>8} {imp:>+6.1f}%{marker}')

# Walk-forward verify the best
print(f'\n{"="*90}')
print('WALK-FORWARD VERIFY best filters')
print('='*90)

train = [t for t in trades if t['date'] < '2025-01-01']
test = [t for t in trades if t['date'] >= '2025-01-01']

best_filters = [
    ('NO FILTER (baseline)', lambda t: True),
    ('DirChg<=6 + BarsDown>=4', lambda t: t['dir_changes']<=6 and t['bars_down']>=4),
    ('DirChg<=6 + Body>=0.40', lambda t: t['dir_changes']<=6 and t['body_ratio']>=0.40),
    ('DirChg<=6 + FirstHr<=0.1', lambda t: t['dir_changes']<=6 and t['first_hr_move']<=0.1),
    ('BarsDown>=5 + FirstHr<=0.0', lambda t: t['bars_down']>=5 and t['first_hr_move']<=0.0),
]

print(f'\n  {"Filter":>40} {"Train N":>8} {"Train/tr":>9} {"Test N":>7} {"Test/tr":>9} {"Drop":>6}')
print(f'  {"-"*85}')
for name, filt in best_filters:
    tr_f = [t for t in train if filt(t)]
    te_f = [t for t in test if filt(t)]
    if not tr_f or not te_f: continue
    tr_per = sum(t['pnl'] for t in tr_f) / len(tr_f)
    te_per = sum(t['pnl'] for t in te_f) / len(te_f)
    drop = (te_per - tr_per) / tr_per * 100
    marker = ' <<<' if te_per > 3200 else ''
    print(f'  {name:>40} {len(tr_f):>8} Rs{tr_per:>+8,.0f} {len(te_f):>7} Rs{te_per:>+8,.0f} {drop:>+5.0f}%{marker}')
