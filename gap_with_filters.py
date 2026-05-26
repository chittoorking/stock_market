"""Gap fill strategy with first bar reversal + other filters — full backtest."""
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

POS=1000000; CHARGES=386
print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

def run_gap(gap_min=1.5, require_fb_reversal=False, require_against_trend=False,
            max_gap=99, min_yd_range=0, target_pct=None, stop_pct=1.0,
            entry_bar=0, label=''):
    """
    Gap fill strategy.
    entry_bar=0: enter at open
    entry_bar=1: enter after first bar (confirm reversal)
    target_pct=None: target is prev close (full gap fill)
    """
    trades = []
    yearly = defaultdict(lambda:{'n':0,'w':0,'pnl':0})

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

            if abs(gap) < gap_min or abs(gap) > max_gap: continue

            # First bar reversal check
            first_bar_ret = (db[0]['close'] - db[0]['open']) / db[0]['open'] * 100
            fb_reverses = (gap > 0 and first_bar_ret < 0) or (gap < 0 and first_bar_ret > 0)
            if require_fb_reversal and not fb_reverses: continue

            # Trend check
            trend = daily_trend.get((date, sym), 'SIDE')
            if require_against_trend:
                if gap > 0 and trend != 'DOWN': continue  # Gap UP needs DOWN trend
                if gap < 0 and trend != 'UP': continue    # Gap DOWN needs UP trend

            # Yesterday range
            pc = daily_ohlc[sym][sd2[di-1]]
            yd_range = pc['range'] / pc['close'] * 100 if pc['close'] > 0 else 0
            if yd_range < min_yd_range: continue

            # Entry
            if entry_bar == 0:
                entry = today_open
            else:
                if len(db) <= entry_bar: continue
                entry = db[entry_bar]['close']

            # Target and stop
            if gap > 0:
                # Gap UP → SHORT
                if target_pct:
                    tp = entry * (1 - target_pct/100)
                else:
                    tp = prev_close  # Full gap fill
                sp = entry * (1 + stop_pct/100)
                ep = db[min(69, len(db)-1)]['close']
                for k in range(entry_bar+1, min(len(db), 70)):
                    if db[k]['low'] <= tp: ep = tp; break
                    if db[k]['high'] >= sp: ep = sp; break
                pnl_pct = (entry - ep) / entry * 100
            else:
                # Gap DOWN → LONG
                if target_pct:
                    tp = entry * (1 + target_pct/100)
                else:
                    tp = prev_close
                sp = entry * (1 - stop_pct/100)
                ep = db[min(69, len(db)-1)]['close']
                for k in range(entry_bar+1, min(len(db), 70)):
                    if db[k]['high'] >= tp: ep = tp; break
                    if db[k]['low'] <= sp: ep = sp; break
                pnl_pct = (ep - entry) / entry * 100

            pnl = pnl_pct / 100 * POS - CHARGES
            trades.append({'pnl':pnl, 'win':pnl>0, 'date':date})
            y = date[:4]
            yearly[y]['n'] += 1; yearly[y]['pnl'] += pnl
            if pnl > 0: yearly[y]['w'] += 1

    if not trades or len(trades) < 10:
        return

    n = len(trades); w = sum(1 for t in trades if t['win']); total = sum(t['pnl'] for t in trades)
    train = [t for t in trades if t['date'] < '2025-01-01']
    test = [t for t in trades if t['date'] >= '2025-01-01']
    tr_wr = sum(1 for t in train if t['win'])/len(train)*100 if train else 0
    te_wr = sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    te_per = sum(t['pnl'] for t in test)/len(test) if test else 0

    marker = ' <<<' if w/n*100 >= 70 else ''
    print(f'  {label:>55}: {n:>4} trades, WR={w/n*100:.0f}%, Rs{total/n:>+7,.0f}/tr | TestWR={te_wr:.0f}% Rs{te_per:>+7,.0f}{marker}')


# ═══ RAW GAP FILL ═══
print('='*90)
print('RAW GAP FILL (no filters)')
print('='*90)
for gm in [1.5, 2.0, 2.5, 3.0]:
    run_gap(gap_min=gm, label=f'Raw gap>{gm}%')

# ═══ ADD FIRST BAR REVERSAL ═══
print(f'\n{"="*90}')
print('+ FIRST BAR REVERSAL (only trade if first bar goes opposite to gap)')
print('='*90)
for gm in [1.5, 2.0, 2.5, 3.0]:
    run_gap(gap_min=gm, require_fb_reversal=True, label=f'Gap>{gm}% + FB reversal')

# ═══ ADD AGAINST TREND ═══
print(f'\n{"="*90}')
print('+ AGAINST TREND (gap direction opposes 5-day trend)')
print('='*90)
for gm in [1.5, 2.0, 2.5, 3.0]:
    run_gap(gap_min=gm, require_against_trend=True, label=f'Gap>{gm}% + against trend')

# ═══ BOTH FILTERS ═══
print(f'\n{"="*90}')
print('+ FB REVERSAL + AGAINST TREND (both)')
print('='*90)
for gm in [1.5, 2.0, 2.5, 3.0]:
    run_gap(gap_min=gm, require_fb_reversal=True, require_against_trend=True,
            label=f'Gap>{gm}% + FB rev + against')

# ═══ ENTER AFTER FIRST BAR (not at open) ═══
print(f'\n{"="*90}')
print('ENTER AFTER FIRST BAR (wait for confirmation)')
print('='*90)
for gm in [1.5, 2.0, 2.5]:
    run_gap(gap_min=gm, require_fb_reversal=True, entry_bar=1,
            label=f'Gap>{gm}% + FB rev + enter bar 1')
    run_gap(gap_min=gm, require_fb_reversal=True, require_against_trend=True, entry_bar=1,
            label=f'Gap>{gm}% + FB rev + against + bar 1')

# ═══ FIXED TARGET (not full gap fill) ═══
print(f'\n{"="*90}')
print('FIXED TARGET (instead of full gap fill)')
print('='*90)
for gm in [1.5, 2.0]:
    for tp in [0.50, 0.75, 1.00, 1.50]:
        run_gap(gap_min=gm, require_fb_reversal=True, target_pct=tp,
                label=f'Gap>{gm}% + FB rev + T={tp}%')

# ═══ WITH YESTERDAY RANGE ═══
print(f'\n{"="*90}')
print('+ YESTERDAY RANGE > 2%')
print('='*90)
for gm in [1.5, 2.0]:
    run_gap(gap_min=gm, require_fb_reversal=True, min_yd_range=2.0,
            label=f'Gap>{gm}% + FB rev + yd>2%')
    run_gap(gap_min=gm, require_fb_reversal=True, require_against_trend=True, min_yd_range=2.0,
            label=f'Gap>{gm}% + FB + against + yd>2%')

# ═══ BEST COMBO WITH DIFFERENT STOPS ═══
print(f'\n{"="*90}')
print('BEST COMBOS — Different stop levels')
print('='*90)
for gm in [1.5, 2.0]:
    for sp in [0.75, 1.0, 1.5, 2.0]:
        run_gap(gap_min=gm, require_fb_reversal=True, stop_pct=sp,
                label=f'Gap>{gm}% + FB rev + S={sp}%')

# ═══ SMALL GAPS (1.5-3%) vs BIG GAPS (3%+) ═══
print(f'\n{"="*90}')
print('SMALL vs BIG GAPS')
print('='*90)
run_gap(gap_min=1.5, max_gap=3.0, require_fb_reversal=True, label='Small gap 1.5-3% + FB rev')
run_gap(gap_min=3.0, require_fb_reversal=True, label='Big gap >3% + FB rev')
run_gap(gap_min=1.5, max_gap=2.5, require_fb_reversal=True, label='Small gap 1.5-2.5% + FB rev')
