"""Analyze every single loss in the 96% WR gap fill strategy."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict, Counter
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
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],
            'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'volume':sum(b['volume'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}

vix_data={}
vf=Path('data/vix_daily.csv')
if vf.exists():
    with open(vf) as f:
        for r in csv.DictReader(f): vix_data[r['date']]=float(r['close'])

POS=1000000; CHARGES=386; TARGET=0.50; STOP=1.0
print('Loaded.\n')

wins = []; losses = []
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
        if abs(gap) < 2.0: continue

        # First bar reversal
        fb_ret = (db[0]['close'] - db[0]['open']) / db[0]['open'] * 100
        fb_reverses = (gap > 0 and fb_ret < 0) or (gap < 0 and fb_ret > 0)
        if not fb_reverses: continue

        entry = today_open
        if gap > 0:
            tp = entry * (1 - TARGET/100); sp = entry * (1 + STOP/100)
            ep = db[min(69, len(db)-1)]['close']
            exit_r = 'eod'
            for k in range(1, min(len(db), 70)):
                if db[k]['low'] <= tp: ep = tp; exit_r = 'target'; break
                if db[k]['high'] >= sp: ep = sp; exit_r = 'stop'; break
            pnl_pct = (entry - ep) / entry * 100
            direction = 'SHORT'
        else:
            tp = entry * (1 + TARGET/100); sp = entry * (1 - STOP/100)
            ep = db[min(69, len(db)-1)]['close']
            exit_r = 'eod'
            for k in range(1, min(len(db), 70)):
                if db[k]['high'] >= tp: ep = tp; exit_r = 'target'; break
                if db[k]['low'] <= sp: ep = sp; exit_r = 'stop'; break
            pnl_pct = (ep - entry) / entry * 100
            direction = 'LONG'

        pnl = pnl_pct / 100 * POS - CHARGES

        # MFE / MAE
        mfe = 0; mae = 0
        for k in range(1, min(len(db), 70)):
            if direction == 'SHORT':
                fav = (entry - db[k]['low']) / entry * 100
                adv = (db[k]['high'] - entry) / entry * 100
            else:
                fav = (db[k]['high'] - entry) / entry * 100
                adv = (entry - db[k]['low']) / entry * 100
            mfe = max(mfe, fav); mae = max(mae, adv)

        # Prev day info
        pc = daily_ohlc[sym][sd2[di-1]]
        yd_range = pc['range'] / pc['close'] * 100 if pc['close'] > 0 else 0
        yd_change = (pc['close'] - pc['open']) / pc['open'] * 100

        # ConsecDays in gap direction
        cd = 0
        for back in range(1, 20):
            if di-back < 1: break
            if gap > 0:
                if daily_ohlc[sym][sd2[di-back]]['close'] > daily_ohlc[sym][sd2[di-back-1]]['close']:
                    cd += 1
                else: break
            else:
                if daily_ohlc[sym][sd2[di-back]]['close'] < daily_ohlc[sym][sd2[di-back-1]]['close']:
                    cd += 1
                else: break

        vix = vix_data.get(date, 0)
        dow = dt.strptime(date, '%Y-%m-%d').weekday()
        day_names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

        # First bar size
        fb_size = abs(fb_ret)

        # How fast did it reach 0.5% favorable?
        bars_to_half = 0
        for k in range(1, min(len(db), 70)):
            if direction == 'SHORT':
                if (entry - db[k]['low']) / entry * 100 >= 0.25:
                    bars_to_half = k; break
            else:
                if (db[k]['high'] - entry) / entry * 100 >= 0.25:
                    bars_to_half = k; break

        t = {
            'pnl':pnl, 'win':pnl>0, 'date':date, 'sym':sym, 'dir':direction,
            'gap':round(abs(gap),2), 'exit':exit_r, 'pnl_pct':round(pnl_pct,3),
            'mfe':round(mfe,3), 'mae':round(mae,3), 'entry':round(entry,2), 'ep':round(ep,2),
            'vix':round(vix,1), 'cd':cd, 'yd_range':round(yd_range,2),
            'yd_change':round(yd_change,2), 'day':day_names[dow],
            'fb_size':round(fb_size,3), 'bars_to_half':bars_to_half,
        }
        if pnl > 0: wins.append(t)
        else: losses.append(t)

total = len(wins) + len(losses)
print(f'Total: {total} trades | Wins: {len(wins)} ({len(wins)/total*100:.1f}%) | Losses: {len(losses)} ({len(losses)/total*100:.1f}%)')

# ═══ EVERY SINGLE LOSS ═══
print(f'\n{"="*100}')
print(f'ALL {len(losses)} LOSSES — What happened?')
print(f'{"="*100}')
print(f'\n{"#":>3} {"Date":>10} {"Day":>3} {"Stock":>12} {"Dir":>5} {"Gap":>5} {"Entry":>8} {"Exit":>8} {"PnL%":>7} '
      f'{"MFE":>5} {"MAE":>5} {"VIX":>5} {"CD":>3} {"YdRng":>5} {"FB%":>5} {"Exit":>6}')
print('-'*110)
for i, t in enumerate(sorted(losses, key=lambda x: x['pnl']), 1):
    print(f'{i:>3} {t["date"]:>10} {t["day"]:>3} {t["sym"]:>12} {t["dir"]:>5} {t["gap"]:>5.1f}% '
          f'{t["entry"]:>8.1f} {t["ep"]:>8.1f} {t["pnl_pct"]:>+6.3f}% '
          f'{t["mfe"]:>5.2f} {t["mae"]:>5.2f} {t["vix"]:>5.1f} {t["cd"]:>3} {t["yd_range"]:>5.1f} '
          f'{t["fb_size"]:>5.2f} {t["exit"]:>6}')

# ═══ PATTERNS IN LOSSES ═══
print(f'\n{"="*100}')
print(f'PATTERNS IN THE {len(losses)} LOSSES')
print(f'{"="*100}')

def avg(lst): return sum(lst)/len(lst) if lst else 0

print(f'\n  {"Feature":>25} {"WINS":>10} {"LOSSES":>10}')
print(f'  {"-"*50}')
for name, key in [('Gap size %','gap'),('MFE %','mfe'),('MAE %','mae'),
                   ('VIX','vix'),('ConsecDays','cd'),('Yd range %','yd_range'),
                   ('First bar size %','fb_size'),('Bars to 0.25%','bars_to_half')]:
    w = avg([t[key] for t in wins])
    l = avg([t[key] for t in losses])
    print(f'  {name:>25} {w:>10.2f} {l:>10.2f}')

# Exit type
print(f'\n  Exit type:')
for ex in ['target','stop','eod']:
    wc = sum(1 for t in wins if t['exit']==ex)
    lc = sum(1 for t in losses if t['exit']==ex)
    print(f'    {ex}: Wins={wc} Losses={lc}')

# Stocks
print(f'\n  Stocks that lost:')
loss_stocks = Counter(t['sym'] for t in losses)
for sym, cnt in loss_stocks.most_common(15):
    total_sym = sum(1 for t in wins+losses if t['sym']==sym)
    print(f'    {sym:>12}: {cnt} losses out of {total_sym} trades ({cnt/total_sym*100:.0f}%)')

# Days
print(f'\n  Days:')
for d in ['Mon','Tue','Wed','Thu','Fri']:
    wc = sum(1 for t in wins if t['day']==d)
    lc = sum(1 for t in losses if t['day']==d)
    dn = wc + lc
    if dn: print(f'    {d}: {dn} trades, {lc} losses, WR={wc/dn*100:.0f}%')

# Cluster check
print(f'\n  Loss clustering:')
loss_dates = Counter(t['date'] for t in losses)
for date, cnt in loss_dates.most_common(5):
    syms = [t['sym'] for t in losses if t['date']==date]
    print(f'    {date}: {cnt} losses — {", ".join(syms)}')

# ═══ CAN WE FIX THEM? ═══
print(f'\n{"="*100}')
print(f'CAN WE FILTER OUT THE 4%?')
print(f'{"="*100}')

# Categorize
stops = [t for t in losses if t['exit']=='stop']
eods = [t for t in losses if t['exit']=='eod']

print(f'\n  STOP HITS: {len(stops)} (gap continued in gap direction past 1% stop)')
print(f'  EOD LOSSES: {len(eods)} (didn\'t hit target or stop, ended slightly negative)')

if stops:
    print(f'\n  Stop hit losses — avg gap: {avg([t["gap"] for t in stops]):.1f}% | avg MAE: {avg([t["mae"] for t in stops]):.1f}%')
    big_gap_stops = [t for t in stops if t['gap'] > 3.0]
    small_gap_stops = [t for t in stops if t['gap'] <= 3.0]
    print(f'    Gap > 3%: {len(big_gap_stops)} stops | Gap <= 3%: {len(small_gap_stops)} stops')

# Test filters that might remove losses
print(f'\n  Filter tests:')
all_t = wins + losses
for name, filt in [
    ('No filter', lambda t: True),
    ('Gap < 5%', lambda t: t['gap'] < 5),
    ('Gap < 4%', lambda t: t['gap'] < 4),
    ('Gap 2-3%', lambda t: 2 <= t['gap'] <= 3),
    ('VIX < 20', lambda t: t['vix'] < 20),
    ('VIX < 18', lambda t: t['vix'] < 18),
    ('CD <= 1', lambda t: t['cd'] <= 1),
    ('CD == 0', lambda t: t['cd'] == 0),
    ('FB size > 0.3%', lambda t: t['fb_size'] > 0.3),
    ('FB size > 0.5%', lambda t: t['fb_size'] > 0.5),
    ('Yd range > 2%', lambda t: t['yd_range'] > 2),
    ('Gap 2-4% + FB>0.3%', lambda t: 2<=t['gap']<=4 and t['fb_size']>0.3),
    ('Gap 2-4% + VIX<20', lambda t: 2<=t['gap']<=4 and t['vix']<20),
    ('Gap 2-4% + CD<=1', lambda t: 2<=t['gap']<=4 and t['cd']<=1),
    ('Gap 2-4% + FB>0.3% + VIX<20', lambda t: 2<=t['gap']<=4 and t['fb_size']>0.3 and t['vix']<20),
]:
    sub = [t for t in all_t if filt(t)]
    if len(sub) < 20: continue
    w = sum(1 for t in sub if t['win'])
    wr = w/len(sub)*100
    rem_losses = len(sub) - w
    print(f'    {name:>35}: {len(sub):>4} trades, WR={wr:.1f}%, {rem_losses} losses')
