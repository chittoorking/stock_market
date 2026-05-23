"""
BOTTOM-UP DISCOVERY — compute every indicator on every signal,
find what combinations are profitable, then narrow to highest WR.

Not: "filter signals with M>=9" (top-down)
But: "what do the WINNERS have in common?" (bottom-up)

Indicators computed for each signal:
1. RSI (14-period)
2. Stochastic %K
3. VWAP distance %
4. EMA9 vs EMA21 spread
5. ADX (trend strength)
6. Bollinger Band position (0-1)
7. Volume ratio vs avg
8. Body ratio %
9. Morning move %
10. Gap %
11. Consecutive direction bars
12. Noise ratio
13. Day of week (Mon-Fri)
14. Time of scan (bar number)
15. India VIX level
16. US overnight return
17. Nifty previous day return
18. Delivery %
19. ATR %
20. Price vs previous day high/low
"""
import sys; sys.path.insert(0, '.')
import csv, json, math
from pathlib import Path
from collections import defaultdict
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.mega_strategies import *
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector
from datetime import datetime

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

vol_agent = VolatilityAgent()
all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))
EXIT_BAR = 69

# Load macro
macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

# Load delivery
delivery = {}
for f in Path('data/delivery').glob('*.csv'):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh:
        delivery[sym] = {r['date']: r for r in csv.DictReader(fh)}

print('Computing ALL indicators for ALL signals on 121 days...', flush=True)
results = []

for date in all_dates:
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    # Day of week
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()  # 0=Mon
    except: dow = -1

    for scan_bar in [6]:
        for sym in all_data:
            db = [b for b in all_data[sym] if b['timestamp'][:10] == date]
            pb = [b for b in all_data[sym] if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']; bsf = db[:scan_bar+1]

            for scanner in [
                lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=4 else None,
                lambda: OpeningRangeBreakout.scan(sym, bsf, pc) if len(bsf)>=7 else None,
                lambda: PivotBreakout.scan(sym, bsf, pc) if len(bsf)>=4 else None,
                lambda: VWAPCrossSignal.scan(sym, bsf) if len(bsf)>=5 else None,
                lambda: EngulfingPattern.scan(sym, bsf) if len(bsf)>=3 else None,
                lambda: DayHighLowBreak.scan(sym, bsf) if len(bsf)>=5 else None,
                lambda: MomentumBurst.scan(sym, bsf) if len(bsf)>=6 else None,
                lambda: HammerShootingStar.scan(sym, bsf) if len(bsf)>=5 else None,
            ]:
                try: sig = scanner()
                except: continue
                if not sig: continue
                direction = sig.direction
                closes = [b['close'] for b in bsf]
                price = closes[-1]

                # 1. RSI
                if len(closes) >= 7:
                    gains = [max(0,closes[i]-closes[i-1]) for i in range(1,len(closes))]
                    loss_l = [max(0,closes[i-1]-closes[i]) for i in range(1,len(closes))]
                    ag = sum(gains[-6:])/6; al = sum(loss_l[-6:])/6
                    rsi = 100 - 100/(1+ag/al) if al > 0 else 50
                else: rsi = 50

                # 2. Stochastic
                highs = [b['high'] for b in bsf]
                lows = [b['low'] for b in bsf]
                hh = max(highs[-min(7,len(highs)):]); ll = min(lows[-min(7,len(lows)):])
                stoch = (price - ll)/(hh - ll)*100 if hh != ll else 50

                # 3. VWAP dist
                tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
                cum_vol = sum(b['volume'] for b in bsf)
                vwap = tp_vol/cum_vol if cum_vol > 0 else price
                vwap_dist = (price - vwap)/vwap * 100

                # 4. EMA spread
                if len(closes) >= 5:
                    ema_fast = sum(closes[-3:])/3
                    ema_slow = sum(closes[-5:])/5
                    ema_spread = (ema_fast - ema_slow)/ema_slow * 100
                else: ema_spread = 0

                # 5. Body ratio
                body = abs(bsf[-1]['close']-bsf[-1]['open'])
                rng = bsf[-1]['high']-bsf[-1]['low']
                body_ratio = body/rng*100 if rng > 0 else 0

                # 6. Morning move
                morning_move = (price - bsf[0]['open'])/bsf[0]['open']*100
                morning_aligned = (direction=='LONG' and morning_move > 0) or (direction=='SHORT' and morning_move < 0)

                # 7. Gap
                gap_pct = (bsf[0]['open'] - pc)/pc * 100
                gap_aligned = (direction=='LONG' and gap_pct > 0) or (direction=='SHORT' and gap_pct < 0)

                # 8. Volume ratio
                prev_dates = sorted(set(b['timestamp'][:10] for b in pb))[-5:]
                prev_vols = []
                for pd_date in prev_dates:
                    pd_bars = [b for b in pb if b['timestamp'][:10] == pd_date]
                    if len(pd_bars) > scan_bar:
                        prev_vols.append(sum(b['volume'] for b in pd_bars[:scan_bar+1]))
                today_vol = sum(b['volume'] for b in bsf)
                rvol = today_vol / (sum(prev_vols)/len(prev_vols)) if prev_vols and sum(prev_vols) > 0 else 1

                # 9. Consecutive bars
                consec = 0
                for i in range(len(bsf)-1, 0, -1):
                    bar_green = bsf[i]['close'] > bsf[i]['open']
                    if (direction=='LONG' and bar_green) or (direction=='SHORT' and not bar_green):
                        consec += 1
                    else: break

                # 10. Noise ratio
                if len(bsf) >= 3:
                    ranges = [b['high']-b['low'] for b in bsf[-min(5,len(bsf)):]]
                    moves = [abs(bsf[i]['close']-bsf[i-1]['close']) for i in range(max(1,len(bsf)-4), len(bsf))]
                    noise = sum(ranges)/sum(moves) if moves and sum(moves) > 0 else 5
                else: noise = 5

                # 11. ATR %
                atr = sum(b['high']-b['low'] for b in bsf)/len(bsf)
                atr_pct = atr/price*100 if price > 0 else 0

                # 12. Macro
                mc = macro.get(date, {})
                vix = mc.get('india_vix', 0)
                us_ret = mc.get('sp500_overnight', 0)
                nifty_prev = mc.get('nifty_prev_return', 0)

                # 13. Delivery %
                del_data = delivery.get(sym, {})
                recent_del = sorted(d for d in del_data if d < date)
                del_pct = 0
                if recent_del:
                    try: del_pct = float(del_data[recent_del[-1]].get('delivery_pct', 0) or 0)
                    except: pass

                # 14. Previous day high/low position
                prev_day_bars = [b for b in pb if b['timestamp'][:10] == sorted(set(b['timestamp'][:10] for b in pb))[-1]] if pb else []
                if prev_day_bars:
                    prev_high = max(b['high'] for b in prev_day_bars)
                    prev_low = min(b['low'] for b in prev_day_bars)
                    price_vs_pdh = (price - prev_high)/prev_high*100
                    price_vs_pdl = (price - prev_low)/prev_low*100
                else:
                    price_vs_pdh = 0; price_vs_pdl = 0

                # Simulate with 3PM exit
                entry = sig.suggested_entry
                pa = [b for b in all_data[sym] if b['timestamp'][:10] <= date]
                atr_val = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
                stop, _ = SmartStopCalculator.calculate(db, scan_bar, entry, direction, atr_val)
                target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)
                exit_price = None
                for j in range(scan_bar+1, min(len(db), EXIT_BAR+1)):
                    if direction=='LONG':
                        if db[j]['low']<=stop: exit_price=stop; break
                        if db[j]['high']>=target: exit_price=target; break
                    else:
                        if db[j]['high']>=stop: exit_price=stop; break
                        if db[j]['low']<=target: exit_price=target; break
                if not exit_price:
                    exit_price = db[min(EXIT_BAR, len(db)-1)]['close']
                pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100

                results.append({
                    'date':date, 'sym':sym, 'dir':direction, 'strat':sig.strategy_name,
                    'pnl':pnl, 'win': 1 if pnl > 0 else 0,
                    'rsi':round(rsi,1), 'stoch':round(stoch,1), 'vwap_dist':round(vwap_dist,3),
                    'ema_spread':round(ema_spread,3), 'body_ratio':round(body_ratio,1),
                    'morning_move':round(morning_move,3), 'morning_aligned':1 if morning_aligned else 0,
                    'gap_pct':round(gap_pct,3), 'gap_aligned':1 if gap_aligned else 0,
                    'rvol':round(rvol,2), 'consec':consec, 'noise':round(noise,2),
                    'atr_pct':round(atr_pct,3), 'vix':round(vix,1), 'us_ret':round(us_ret,2),
                    'nifty_prev':round(nifty_prev,2), 'del_pct':round(del_pct,1),
                    'dow':dow, 'price_vs_pdh':round(price_vs_pdh,2), 'price_vs_pdl':round(price_vs_pdl,2),
                })

print(f'Total: {len(results)} signals, {sum(r["win"] for r in results)} winners ({sum(r["win"] for r in results)/len(results)*100:.0f}%)')

# BOTTOM-UP: for each indicator, find the RANGE where WR is highest
print('\n' + '='*80)
print('BOTTOM-UP: Which indicator ranges produce highest WR?')
print('='*80)

indicators = ['rsi','stoch','vwap_dist','ema_spread','body_ratio','morning_move',
              'rvol','consec','noise','atr_pct','vix','us_ret','nifty_prev','del_pct',
              'dow','morning_aligned','gap_aligned','price_vs_pdh','price_vs_pdl']

for ind in indicators:
    vals = [r[ind] for r in results]
    if not vals: continue
    vmin = min(vals); vmax = max(vals)
    if vmin == vmax: continue

    # Split into 5 buckets
    step = (vmax - vmin) / 5
    if step == 0: continue
    print(f'\n{ind}:')
    for i in range(5):
        lo = vmin + step * i
        hi = vmin + step * (i+1) if i < 4 else vmax + 0.01
        bucket = [r for r in results if lo <= r[ind] < hi]
        if len(bucket) < 10: continue
        w = sum(r['win'] for r in bucket)
        wr = w/len(bucket)*100
        avg = sum(r['pnl'] for r in bucket)/len(bucket)
        marker = ' <<<' if wr >= 50 else ''
        print(f'  [{lo:>7.1f} - {hi:>7.1f}): {len(bucket):>5} trades, WR={wr:>4.0f}%, avg={avg:>+6.3f}%{marker}')

# COMBO DISCOVERY: find top 2-indicator combos
print('\n' + '='*80)
print('TOP 2-INDICATOR COMBOS (>50% WR, min 20 trades):')
print('='*80)

best_combos = []
bool_inds = ['morning_aligned', 'gap_aligned']
range_inds = [
    ('rsi', [(0,30),(30,50),(50,70),(70,100)]),
    ('stoch', [(0,25),(25,50),(50,75),(75,100)]),
    ('rvol', [(0,1),(1,1.5),(1.5,2.5),(2.5,99)]),
    ('consec', [(0,2),(2,3),(3,4),(4,99)]),
    ('noise', [(0,2),(2,3),(3,4),(4,99)]),
    ('body_ratio', [(0,30),(30,50),(50,70),(70,100)]),
    ('vix', [(0,14),(14,18),(18,22),(22,99)]),
    ('del_pct', [(0,30),(30,45),(45,55),(55,100)]),
    ('dow', [(0,1),(1,2),(2,3),(3,4),(4,5)]),
]

for bi in bool_inds:
    for ri_name, ri_ranges in range_inds:
        for lo, hi in ri_ranges:
            sub = [r for r in results if r[bi]==1 and lo <= r[ri_name] < hi]
            if len(sub) < 20: continue
            w = sum(r['win'] for r in sub)
            wr = w/len(sub)*100
            avg = sum(r['pnl'] for r in sub)/len(sub)
            total = sum(r['pnl'] for r in sub)
            if wr >= 45:
                best_combos.append({'name':f'{bi}=1 + {ri_name}[{lo}-{hi})', 'n':len(sub), 'w':w, 'wr':wr, 'avg':avg, 'total':total})

for ri1_name, ri1_ranges in range_inds:
    for ri2_name, ri2_ranges in range_inds:
        if ri1_name >= ri2_name: continue
        for lo1, hi1 in ri1_ranges:
            for lo2, hi2 in ri2_ranges:
                sub = [r for r in results if lo1<=r[ri1_name]<hi1 and lo2<=r[ri2_name]<hi2]
                if len(sub) < 20: continue
                w = sum(r['win'] for r in sub)
                wr = w/len(sub)*100
                avg = sum(r['pnl'] for r in sub)/len(sub)
                total = sum(r['pnl'] for r in sub)
                if wr >= 50:
                    best_combos.append({'name':f'{ri1_name}[{lo1}-{hi1}) + {ri2_name}[{lo2}-{hi2})', 'n':len(sub), 'w':w, 'wr':wr, 'avg':avg, 'total':total})

best_combos.sort(key=lambda x: (-x['wr'], -x['total']))
print(f'\nFound {len(best_combos)} profitable combos')
print(f"{'Combo':>50} {'N':>5} {'W':>4} {'WR':>5} {'Avg':>7} {'Total':>8}")
print('-'*85)
for c in best_combos[:30]:
    print(f"{c['name']:>50} {c['n']:>5} {c['w']:>4} {c['wr']:>4.0f}% {c['avg']:>+6.3f}% {c['total']:>+7.2f}%")
