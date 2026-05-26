"""Analyze gap fills vs non-fills — what patterns predict which gaps fill?"""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
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
all_dates=sorted(date_bars.keys())
daily_ohlc=defaultdict(dict)
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],
            'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'volume':sum(b['volume'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}

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

vix_data={}
vf=Path('data/vix_daily.csv')
if vf.exists():
    with open(vf) as f:
        for r in csv.DictReader(f): vix_data[r['date']]=float(r['close'])

POS=1000000;CHARGES=386
print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

# Collect all gaps > 1.5% with features
fills = []  # gaps that filled
no_fills = []  # gaps that didn't fill

for date in all_dates:
    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db = date_bars[date][sym]
        if len(db) <= 15: continue

        sd2 = sorted(daily_ohlc.get(sym,{}).keys())
        di = sd2.index(date) if date in sd2 else -1
        if di < 5: continue

        prev_close = daily_ohlc[sym][sd2[di-1]]['close']
        today_open = db[0]['open']
        gap = (today_open - prev_close) / prev_close * 100

        if abs(gap) < 1.5: continue

        # Did the gap fill in first hour (10 bars)?
        filled = False
        if gap > 0:  # Gap UP
            for k in range(1, min(11, len(db))):
                if db[k]['low'] <= prev_close:
                    filled = True; break
            direction = 'UP'
        else:  # Gap DOWN
            for k in range(1, min(11, len(db))):
                if db[k]['high'] >= prev_close:
                    filled = True; break
            direction = 'DOWN'

        # Features available BEFORE trading
        trend = daily_trend.get((date, sym), 'SIDE')

        # Consecutive days in gap direction
        cd = 0
        for back in range(1, 20):
            if di-back < 1: break
            if direction == 'UP':
                if daily_ohlc[sym][sd2[di-back]]['close'] > daily_ohlc[sym][sd2[di-back-1]]['close']:
                    cd += 1
                else: break
            else:
                if daily_ohlc[sym][sd2[di-back]]['close'] < daily_ohlc[sym][sd2[di-back-1]]['close']:
                    cd += 1
                else: break

        # Yesterday's range and body
        pc = daily_ohlc[sym][sd2[di-1]]
        yd_range = pc['range'] / pc['close'] * 100 if pc['close'] > 0 else 0
        yd_body = abs(pc['close'] - pc['open']) / pc['range'] if pc['range'] > 0 else 0.5

        # Gap vs trend alignment
        # Gap UP + trend UP = aligned (gap might NOT fill — momentum)
        # Gap UP + trend DOWN = against trend (gap likely FILLS — reversal)
        if direction == 'UP':
            aligned = trend == 'UP'
            against = trend == 'DOWN'
        else:
            aligned = trend == 'DOWN'
            against = trend == 'UP'

        # VIX
        vix = vix_data.get(date, 15)

        # Volume yesterday vs average
        vols = [daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,5) if di-k>=0]
        vol_ratio = vols[0] / max(1, sum(vols)/len(vols)) if vols else 1

        # First bar direction (does stock continue gap or reverse?)
        first_bar_ret = (db[0]['close'] - db[0]['open']) / db[0]['open'] * 100
        first_bar_reverses = (gap > 0 and first_bar_ret < 0) or (gap < 0 and first_bar_ret > 0)

        record = {
            'gap': round(abs(gap), 2), 'direction': direction, 'filled': filled,
            'trend': trend, 'aligned': aligned, 'against': against,
            'cd': cd, 'yd_range': round(yd_range, 2), 'yd_body': round(yd_body, 2),
            'vix': round(vix, 1), 'vol_ratio': round(vol_ratio, 2),
            'first_bar_reverses': first_bar_reverses,
            'date': date, 'sym': sym,
        }
        if filled: fills.append(record)
        else: no_fills.append(record)

total = len(fills) + len(no_fills)
print(f'Total gaps > 1.5%: {total}')
print(f'Filled: {len(fills)} ({len(fills)/total*100:.0f}%)')
print(f'Not filled: {len(no_fills)} ({len(no_fills)/total*100:.0f}%)')

# ═══ COMPARE FEATURES ═══
print(f'\n{"="*80}')
print('WHAT PREDICTS GAP FILL vs NO FILL?')
print('='*80)

def avg(lst): return sum(lst)/len(lst) if lst else 0

print(f'\n  {"Feature":>30} {"FILLS":>10} {"NO FILL":>10} {"Signal":>10}')
print(f'  {"-"*65}')

for name, key in [
    ('Gap size %', 'gap'),
    ('ConsecDays in gap dir', 'cd'),
    ('Yesterday range %', 'yd_range'),
    ('Yesterday body ratio', 'yd_body'),
    ('VIX', 'vix'),
    ('Volume ratio', 'vol_ratio'),
]:
    f = avg([r[key] for r in fills])
    nf = avg([r[key] for r in no_fills])
    diff = abs(f-nf)/max(abs(f),0.001)*100
    marker = ' <<<' if diff > 15 else ''
    print(f'  {name:>30} {f:>10.2f} {nf:>10.2f} {diff:>9.0f}%{marker}')

# Boolean features
for name, key in [
    ('Gap aligned with trend %', 'aligned'),
    ('Gap against trend %', 'against'),
    ('First bar reverses %', 'first_bar_reverses'),
]:
    f = sum(1 for r in fills if r[key])/len(fills)*100
    nf = sum(1 for r in no_fills if r[key])/len(no_fills)*100
    diff = abs(f-nf)
    marker = ' <<<' if diff > 10 else ''
    print(f'  {name:>30} {f:>9.1f}% {nf:>9.1f}% {diff:>8.1f}pp{marker}')

# Trend distribution
print(f'\n  Trend distribution:')
for trend in ['UP', 'DOWN', 'SIDE']:
    f = sum(1 for r in fills if r['trend']==trend)/len(fills)*100
    nf = sum(1 for r in no_fills if r['trend']==trend)/len(no_fills)*100
    print(f'    {trend}: Fills={f:.0f}% | NoFill={nf:.0f}%')

# ═══ THE KEY PATTERNS ═══
print(f'\n{"="*80}')
print('PATTERN SEARCH — Which filters improve gap fill WR?')
print('='*80)

all_gaps = fills + no_fills

def test_filter(name, filt):
    subset = [r for r in all_gaps if filt(r)]
    if len(subset) < 20: return
    w = sum(1 for r in subset if r['filled'])
    wr = w/len(subset)*100
    if wr >= 65:
        print(f'  {name:>50}: {len(subset):>4} gaps, Fill rate={wr:.0f}%')

# Gap size
for g in [1.5, 2.0, 2.5, 3.0, 4.0]:
    test_filter(f'Gap > {g}%', lambda r, g=g: r['gap'] > g)
    test_filter(f'Gap < {g}%', lambda r, g=g: r['gap'] < g)

# Against trend
test_filter('Gap UP + trend DOWN (against)', lambda r: r['direction']=='UP' and r['trend']=='DOWN')
test_filter('Gap DOWN + trend UP (against)', lambda r: r['direction']=='DOWN' and r['trend']=='UP')
test_filter('Gap UP + trend UP (aligned)', lambda r: r['direction']=='UP' and r['trend']=='UP')
test_filter('Gap DOWN + trend DOWN (aligned)', lambda r: r['direction']=='DOWN' and r['trend']=='DOWN')
test_filter('Gap + trend SIDE', lambda r: r['trend']=='SIDE')

# ConsecDays
for cd in [0, 1, 2, 3]:
    test_filter(f'CD in gap direction = {cd}', lambda r, cd=cd: r['cd']==cd)
    test_filter(f'CD in gap direction <= {cd}', lambda r, cd=cd: r['cd']<=cd)

# First bar reverses
test_filter('First bar REVERSES gap', lambda r: r['first_bar_reverses'])
test_filter('First bar CONTINUES gap', lambda r: not r['first_bar_reverses'])

# VIX
for v in [12, 15, 18, 20]:
    test_filter(f'VIX > {v}', lambda r, v=v: r['vix'] > v)
    test_filter(f'VIX < {v}', lambda r, v=v: r['vix'] < v)

# Yesterday range
for yr in [1.5, 2.0, 2.5, 3.0]:
    test_filter(f'Yd range > {yr}%', lambda r, yr=yr: r['yd_range'] > yr)

# Combinations
test_filter('Against trend + first bar reverses', lambda r: r['against'] and r['first_bar_reverses'])
test_filter('Against trend + CD <= 1', lambda r: r['against'] and r['cd'] <= 1)
test_filter('Against trend + VIX > 15', lambda r: r['against'] and r['vix'] > 15)
test_filter('Against trend + yd_range > 2%', lambda r: r['against'] and r['yd_range'] > 2)
test_filter('First bar reverses + CD <= 1', lambda r: r['first_bar_reverses'] and r['cd'] <= 1)
test_filter('First bar reverses + gap > 2%', lambda r: r['first_bar_reverses'] and r['gap'] > 2)
test_filter('Against + first_bar_rev + gap > 2%', lambda r: r['against'] and r['first_bar_reverses'] and r['gap'] > 2)
test_filter('Against + first_bar_rev + VIX > 15', lambda r: r['against'] and r['first_bar_reverses'] and r['vix'] > 15)
test_filter('SIDE trend + first bar reverses', lambda r: r['trend']=='SIDE' and r['first_bar_reverses'])
test_filter('SIDE + first_bar_rev + gap > 2%', lambda r: r['trend']=='SIDE' and r['first_bar_reverses'] and r['gap'] > 2)

# ═══ BEST COMBOS — Can we get 80%+ fill rate? ═══
print(f'\n{"="*80}')
print('BEST COMBOS — Hunting for 80%+ gap fill rate')
print('='*80)

best = []
for against in [True, False, None]:
    for fb_rev in [True, False, None]:
        for gap_min in [1.5, 2.0, 2.5, 3.0]:
            for cd_max in [0, 1, 2, 99]:
                for vix_min in [0, 12, 15, 18]:
                    def filt(r, ag=against, fb=fb_rev, gm=gap_min, cm=cd_max, vm=vix_min):
                        if ag is not None and r['against'] != ag: return False
                        if fb is not None and r['first_bar_reverses'] != fb: return False
                        if r['gap'] < gm: return False
                        if r['cd'] > cm: return False
                        if r['vix'] < vm: return False
                        return True
                    subset = [r for r in all_gaps if filt(r)]
                    if len(subset) < 20:
                        continue
                    w = sum(1 for r in subset if r['filled'])
                    wr = w/len(subset)*100
                    if wr >= 75:
                        label = []
                        if against is not None: label.append('against' if against else 'aligned')
                        if fb_rev is not None: label.append('fb_rev' if fb_rev else 'fb_cont')
                        label.append(f'gap>{gap_min}')
                        if cd_max < 99: label.append(f'cd<={cd_max}')
                        if vix_min > 0: label.append(f'vix>{vix_min}')
                        best.append((' + '.join(label), len(subset), wr))

best.sort(key=lambda x: (-x[2], -x[1]))
for name, n, wr in best[:20]:
    marker = ' <<<' if wr >= 80 else ''
    print(f'  {name:>55}: {n:>4} gaps, Fill rate={wr:.0f}%{marker}')
