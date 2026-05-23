"""
GRIND: ORB + MoE + Raw Indicators + Relative Strength + Stock Filters
Goal: Push ORB from 61% WR to 75%+ while keeping 50+ trading days.

Plan:
1. Generate ORB signals for all range periods (1-6 bars = 5-30min)
2. Compute MoE scores + 50 raw indicators on each ORB signal
3. Compute relative strength rank for each stock
4. Test every filter combination exhaustively
5. Test stock-specific whitelist/blacklist
6. Cross-strategy confirmation: does ORB + momentum signal = better?
7. Walk-forward: train first 80 days, test last 41
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.agents.coded_moe import score_volume, score_price, score_momentum
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer

# ─── DATA LOADING ───
data_dir = Path('data/5min')
all_data = {}
date_bars = defaultdict(dict)

print('Loading data...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items():
        date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

prev_close_map = {}
prev_day_data = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            prev_d = dates_for_sym[i-1]
            pdb = date_bars[prev_d].get(sym, [])
            if pdb:
                prev_close_map[(d, sym)] = pdb[-1]['close']
                prev_day_data[(d, sym)] = {
                    'high': max(b['high'] for b in pdb),
                    'low': min(b['low'] for b in pdb),
                    'close': pdb[-1]['close'],
                }

prev_5d_vol = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        vols = []
        for j in range(max(0, i-5), i):
            pd = dates_for_sym[j]
            pd_bars = date_bars[pd].get(sym, [])
            if len(pd_bars) > 6:
                vols.append(sum(b['volume'] for b in pd_bars[:7]))
        if vols:
            prev_5d_vol[(d, sym)] = sum(vols)/len(vols)

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

delivery = {}
for f in Path('data/delivery').glob('*.csv'):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh:
        delivery[sym] = {r['date']: r for r in csv.DictReader(fh)}

vol_agent = VolatilityAgent()

# Pre-compute sector/market
SCAN_BARS_FOR_MOE = [3, 6, 10]
sector_cache = {}
market_cache = {}
print('Pre-computing sector/market...', flush=True)
for sb in SCAN_BARS_FOR_MOE:
    for date in all_dates:
        sector_cache[(date, sb)] = SectorAnalyzer.compute(all_data, date, sb)
        up = 0; down = 0; total = 0
        for sym in date_bars[date]:
            db = date_bars[date][sym]
            if len(db) <= sb: continue
            total += 1
            move = (db[sb]['close'] - db[0]['open']) / db[0]['open'] * 100
            if move > 0.15: up += 1
            elif move < -0.15: down += 1
        market_cache[(date, sb)] = (up, down, total)

def fast_score_sector(symbol, date, scan_bar, direction):
    sb = min(SCAN_BARS_FOR_MOE, key=lambda x: abs(x - scan_bar))
    score = 5.0
    sector = get_sector(symbol)
    sec_info = SECTOR_MAP.get(sector, {})
    if not sec_info: return 4.0
    sec = sector_cache[(date, sb)].get(sector, {})
    if not sec: return 4.0
    leader_chg = sec.get('leader_change_pct', 0)
    strength = sec.get('strength', 0)
    aligned = (direction == "LONG" and leader_chg > 0.3) or (direction == "SHORT" and leader_chg < -0.3)
    against = (direction == "LONG" and leader_chg < -0.3) or (direction == "SHORT" and leader_chg > 0.3)
    if aligned and abs(leader_chg) > 1.0: score += 2.5
    elif aligned: score += 1.5
    elif against: score -= 2.0
    if strength >= 0.75: score += 1.0
    elif strength <= 0.25: score -= 1.5
    return max(0, min(10, score))

def fast_score_market(date, scan_bar, direction):
    sb = min(SCAN_BARS_FOR_MOE, key=lambda x: abs(x - scan_bar))
    score = 5.0
    up, down, total = market_cache[(date, sb)]
    if total == 0: return 5.0
    up_pct = up / total * 100; down_pct = down / total * 100
    if direction == "LONG" and up_pct > 65: score += 2.0
    elif direction == "LONG" and up_pct > 55: score += 1.0
    elif direction == "LONG" and up_pct < 35: score -= 2.0
    elif direction == "SHORT" and down_pct > 65: score += 2.0
    elif direction == "SHORT" and down_pct > 55: score += 1.0
    elif direction == "SHORT" and down_pct < 35: score -= 2.0
    flat = total - up - down
    if flat > total * 0.4: score -= 1.0
    return max(0, min(10, score))

def fast_score_macro(date, direction):
    score = 5.0
    ctx = macro.get(date, {})
    if not ctx: return 5.0
    vix = ctx.get('india_vix', 0)
    sp = ctx.get('sp500_overnight', 0)
    nq = ctx.get('nasdaq_overnight', 0)
    nifty_prev = ctx.get('nifty_prev_return', 0)
    if vix > 22: score -= 1.5
    elif vix > 18: score -= 0.5
    elif vix < 13: score += 1.0
    us_avg = (sp + nq) / 2
    if direction == "LONG" and us_avg > 0.5: score += 1.5
    elif direction == "LONG" and us_avg < -0.5: score -= 1.5
    elif direction == "SHORT" and us_avg < -0.5: score += 1.5
    elif direction == "SHORT" and us_avg > 0.5: score -= 1.5
    if direction == "LONG" and 0 < nifty_prev < 1.0: score += 0.5
    elif direction == "LONG" and nifty_prev > 1.5: score -= 0.5
    elif direction == "SHORT" and -1.0 < nifty_prev < 0: score += 0.5
    elif direction == "SHORT" and nifty_prev < -1.5: score -= 0.5
    return max(0, min(10, score))

print('Done. Starting ORB scan...\n', flush=True)

# ═══ GENERATE ORB SIGNALS WITH EVERYTHING ═══
t0 = time.time()
all_signals = []

for di, date in enumerate(all_dates):
    mc = macro.get(date, {})
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: dow = -1

    # Compute relative strength at bar 3 (for ranking)
    rs_at_3 = {}
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) <= 3: continue
        pc = prev_close_map.get((date, sym))
        if pc is None: continue
        move = (db[3]['close'] - db[0]['open']) / db[0]['open'] * 100
        rs_at_3[sym] = move

    # Market breadth at open
    crowd = 0; total_syms = 0
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pc) / pc * 100
        if abs(gap) > 0.3: crowd += 1

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue
        pd = prev_day_data.get((date, sym), {})

        for range_bars in [1, 2, 3, 4, 5, 6]:
            if len(db) <= range_bars + 10: continue
            range_high = max(b['high'] for b in db[:range_bars])
            range_low = min(b['low'] for b in db[:range_bars])
            range_size = range_high - range_low
            if range_size == 0: continue
            range_pct = range_size / pc * 100

            # Scan for breakout
            for j in range(range_bars, min(len(db), 24)):
                direction = None
                if db[j]['close'] > range_high: direction = 'LONG'
                elif db[j]['close'] < range_low: direction = 'SHORT'
                if not direction: continue

                entry = db[j]['close']
                breakout_bar = j
                avg_bar_vol = sum(b['volume'] for b in db[:range_bars]) / range_bars if range_bars > 0 else 1
                vol_spike = db[j]['volume'] / avg_bar_vol if avg_bar_vol > 0 else 1
                vol_ok = vol_spike >= 1.2

                # MoE scores (use breakout_bar as scan_bar)
                bsf = db[:breakout_bar+1]
                V = score_volume(sym, bsf, all_data, date, breakout_bar)
                S = fast_score_sector(sym, date, breakout_bar, direction)
                P = score_price(sym, bsf, breakout_bar, pc, direction)
                M = score_momentum(sym, bsf, breakout_bar, direction)
                Mkt = fast_score_market(date, breakout_bar, direction)
                Mac = fast_score_macro(date, direction)
                moe = V*1.5 + M*1.3 + P*0.8 + S*0.5 + Mkt*0.5 + Mac*0.5

                # Raw indicators on bsf
                closes = [b['close'] for b in bsf]
                highs = [b['high'] for b in bsf]
                lows = [b['low'] for b in bsf]
                n = len(closes)
                dir_mult = 1 if direction == 'LONG' else -1

                # Gap
                gap = (db[0]['open'] - pc) / pc * 100
                gap_adj = gap * dir_mult

                # Morning move
                morning_move = (entry - db[0]['open']) / db[0]['open'] * 100
                morning_adj = morning_move * dir_mult

                # VWAP
                tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
                cum_vol = sum(b['volume'] for b in bsf)
                vwap = tp_vol/cum_vol if cum_vol > 0 else entry
                vwap_adj = (entry - vwap)/vwap * 100 * dir_mult

                # Candle pattern on breakout bar
                body = abs(db[j]['close']-db[j]['open'])
                rng = db[j]['high']-db[j]['low']
                body_ratio = body/rng*100 if rng > 0 else 0
                uw = db[j]['high'] - max(db[j]['open'], db[j]['close'])
                lw = min(db[j]['open'], db[j]['close']) - db[j]['low']
                candle = 0
                if rng > 0:
                    br = body/rng
                    if br > 0.8: candle = 2 if db[j]['close'] > db[j]['open'] else -2
                    elif lw > body*2 and uw < body*0.5: candle = 1
                    elif uw > body*2 and lw < body*0.5: candle = -1

                # Noise
                if n >= 3:
                    ranges_sum = sum(highs[i]-lows[i] for i in range(max(0,n-5), n))
                    move_abs = abs(closes[-1]-closes[max(0,n-5)])
                    noise = ranges_sum/(move_abs+0.001)
                else: noise = 5

                # RSI
                if n >= 7:
                    gains = [max(0, closes[i]-closes[i-1]) for i in range(1, n)]
                    loss_l = [max(0, closes[i-1]-closes[i]) for i in range(1, n)]
                    ag = sum(gains[-6:])/6; al = sum(loss_l[-6:])/6
                    rsi = 100 - 100/(1+ag/al) if al > 0 else 50
                else: rsi = 50

                # ATR%
                atr_pct = sum(highs[i]-lows[i] for i in range(n))/n/entry*100 if entry > 0 else 0

                # Relative strength rank (1=strongest, 45=weakest)
                if rs_at_3:
                    sorted_rs = sorted(rs_at_3.items(), key=lambda x: -x[1])
                    rs_rank = next((i+1 for i, (s, _) in enumerate(sorted_rs) if s == sym), len(sorted_rs)//2)
                    rs_pctile = rs_rank / len(sorted_rs) * 100  # 0=strongest, 100=weakest
                else:
                    rs_rank = 20; rs_pctile = 50

                # Previous day context
                vs_pdh = (entry - pd.get('high', entry)) / pd.get('high', entry) * 100 if pd else 0
                vs_pdl = (entry - pd.get('low', entry)) / pd.get('low', entry) * 100 if pd else 0
                pivot = (pd.get('high',0)+pd.get('low',0)+pd.get('close',0))/3 if pd else entry
                pivot_dist = (entry - pivot)/pivot*100 if pivot > 0 else 0

                # Delivery %
                del_data = delivery.get(sym, {})
                recent_del = sorted(d for d in del_data if d < date)
                del_pct = 0
                if recent_del:
                    try: del_pct = float(del_data[recent_del[-1]].get('delivery_pct', 0) or 0)
                    except: pass

                # Consec direction bars before breakout
                consec = 0
                for k in range(j, 0, -1):
                    bg = db[k]['close'] > db[k]['open']
                    if (direction=='LONG' and bg) or (direction=='SHORT' and not bg): consec += 1
                    else: break

                # Breakout strength: how far above/below range
                breakout_strength = abs(entry - (range_high if direction=='LONG' else range_low)) / range_size * 100

                # Simulate across R:R and exits
                atr = sum(b['high']-b['low'] for b in db[max(0,j-5):j+1]) / min(6, j+1)
                for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
                    stop = range_low if direction=='LONG' else range_high
                    risk = abs(entry - stop)
                    if risk == 0: continue
                    target = entry + risk*rr if direction=='LONG' else entry - risk*rr

                    for exit_bar_name, exit_bar in [('1PM',36),('2PM',48),('230PM',54),('3PM',69)]:
                        exit_price = None
                        for k in range(j+1, min(len(db), exit_bar+1)):
                            if direction=='LONG':
                                if db[k]['low'] <= stop: exit_price = stop; break
                                if db[k]['high'] >= target: exit_price = target; break
                            else:
                                if db[k]['high'] >= stop: exit_price = stop; break
                                if db[k]['low'] <= target: exit_price = target; break
                        if not exit_price:
                            exit_price = db[min(exit_bar, len(db)-1)]['close']
                        pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100

                        all_signals.append({
                            'date': date, 'sym': sym, 'dir': direction,
                            'range_bars': range_bars, 'breakout_bar': breakout_bar,
                            'rr': rr, 'exit': exit_bar_name,
                            'range_pct': round(range_pct, 3),
                            'breakout_strength': round(breakout_strength, 1),
                            'vol_spike': round(vol_spike, 2), 'vol_ok': vol_ok,
                            'V': V, 'S': S, 'P': P, 'M': M, 'Mkt': Mkt, 'Mac': Mac,
                            'moe': round(moe, 1),
                            'rsi': round(rsi,1), 'gap_adj': round(gap_adj,3),
                            'morning_adj': round(morning_adj,3), 'vwap_adj': round(vwap_adj,3),
                            'candle': candle, 'body_ratio': round(body_ratio,1),
                            'noise': round(noise,2), 'atr_pct': round(atr_pct,3),
                            'consec': consec, 'rs_rank': rs_rank, 'rs_pctile': round(rs_pctile,1),
                            'vs_pdh': round(vs_pdh,2), 'vs_pdl': round(vs_pdl,2),
                            'pivot_dist': round(pivot_dist,3),
                            'vix': round(mc.get('india_vix',0),1),
                            'del_pct': round(del_pct,1), 'dow': dow,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break  # Only first breakout per range

    if (di+1) % 20 == 0:
        elapsed = time.time() - t0
        rate = (di+1)/elapsed
        remaining = (len(all_dates)-di-1)/rate
        print(f'  {di+1}/{len(all_dates)} ({(di+1)/len(all_dates)*100:.0f}%) | {len(all_signals)} sigs | ETA {remaining:.0f}s', flush=True)

print(f'\nTotal: {len(all_signals)} ORB signals in {time.time()-t0:.1f}s')

# ═══ ANALYSIS ═══

def analyze(signals, label=""):
    """Analyze a set of signals"""
    if not signals: return 0, 0, 0, 0
    # Dedup by date+sym (keep highest moe)
    seen = {}
    for s in sorted(signals, key=lambda x: -x.get('moe', 0)):
        key = (s['date'], s['sym'])
        if key not in seen: seen[key] = s
    unique = list(seen.values())
    w = sum(s['win'] for s in unique)
    wr = w/len(unique)*100 if unique else 0
    pnl = sum(s['pnl'] for s in unique)
    days = len(set(s['date'] for s in unique))
    return len(unique), wr, pnl, days

# ─── 1. BASELINE: ORB by range + R:R + exit ───
print('\n' + '='*80)
print('BASELINE: ORB performance by params')
print('='*80)
print(f"{'Range':>6} {'R:R':>4} {'Exit':>5} {'Vol':>4} | {'N':>5} {'WR':>5} {'Days':>5} {'P&L':>8}")
print('-'*55)
for range_bars in [1, 2, 3, 5, 6]:
    for rr in [1.5, 2.0, 2.5]:
        for exit_name in ['1PM', '3PM']:
            for vol_filter in [False, True]:
                sub = [s for s in all_signals if s['range_bars']==range_bars and s['rr']==rr
                       and s['exit']==exit_name and (not vol_filter or s['vol_ok'])]
                n, wr, pnl, days = analyze(sub)
                if n < 10 or wr < 50: continue
                vl = 'Y' if vol_filter else 'N'
                marker = ' <<<' if wr >= 60 else ''
                print(f"  {range_bars*5:>3}min {rr:>4.1f} {exit_name:>5} {vl:>4} | {n:>5} {wr:>4.0f}% {days:>5} {pnl:>+7.1f}%{marker}")

# ─── 2. ORB + MoE FILTERS ───
print('\n' + '='*80)
print('ORB + MoE COMPONENT FILTERS')
print('='*80)

# Fix params: best baseline
base_params = {'rr': 1.5, 'exit': '1PM'}
base = [s for s in all_signals if s['rr']==base_params['rr'] and s['exit']==base_params['exit']]

best_combos = []
for range_bars in [1, 2, 3, 5]:
    rb = [s for s in base if s['range_bars'] == range_bars]
    for vol_filter in [False, True]:
        sub = [s for s in rb if not vol_filter or s['vol_ok']]

        # MoE component filters
        for m_min in [0, 6, 7, 8, 9]:
            for p_min in [0, 6, 7, 8]:
                for mkt_min in [0, 5, 6, 7]:
                    filt = [s for s in sub if s['M']>=m_min and s['P']>=p_min and s['Mkt']>=mkt_min]
                    n, wr, pnl, days = analyze(filt)
                    if n < 5 or wr < 55: continue
                    vl = '+vol' if vol_filter else ''
                    best_combos.append({
                        'label': f'ORB-{range_bars*5}min{vl} M>={m_min} P>={p_min} Mkt>={mkt_min}',
                        'n': n, 'wr': wr, 'pnl': pnl, 'days': days
                    })

best_combos.sort(key=lambda x: (-x['wr'], -x['n']))
seen_labels = set()
printed = 0
print(f"{'Filter':>60} {'N':>4} {'WR':>5} {'Days':>5} {'P&L':>8}")
print('-'*90)
for c in best_combos:
    if c['label'] in seen_labels: continue
    seen_labels.add(c['label'])
    marker = ' ***' if c['wr'] >= 80 else (' <<' if c['wr'] >= 70 else '')
    print(f"{c['label']:>60} {c['n']:>4} {c['wr']:>4.0f}% {c['days']:>5} {c['pnl']:>+7.1f}%{marker}")
    printed += 1
    if printed >= 40: break

# ─── 3. ORB + RAW INDICATOR FILTERS ───
print('\n' + '='*80)
print('ORB + RAW INDICATOR FILTERS (on top of vol confirmation)')
print('='*80)

raw_combos = []
for range_bars in [1, 2, 3, 5]:
    rb = [s for s in base if s['range_bars'] == range_bars and s['vol_ok']]
    if len(rb) < 10: continue

    for ind, thresholds, direction in [
        ('candle', [-1, 0, 1], 'le'),
        ('noise', [2, 2.5, 3, 4], 'le'),
        ('morning_adj', [0, 0.3, 0.5, 1.0], 'ge'),
        ('gap_adj', [0, 0.3, 0.5], 'ge'),
        ('vwap_adj', [0, 0.2, 0.5], 'ge'),
        ('body_ratio', [50, 60, 70, 80], 'ge'),
        ('consec', [0, 1, 2, 3], 'ge'),
        ('rs_pctile', [10, 20, 30, 50], 'le'),  # Lower = stronger
        ('atr_pct', [0.2, 0.3, 0.5], 'le'),
        ('vol_spike', [1.5, 2.0, 3.0], 'ge'),
        ('breakout_strength', [5, 10, 20, 50], 'ge'),
        ('rsi', [40, 50, 60], 'ge'),
        ('del_pct', [40, 50, 60], 'ge'),
    ]:
        for thresh in thresholds:
            if direction == 'ge':
                filt = [s for s in rb if s[ind] >= thresh]
            else:
                filt = [s for s in rb if s[ind] <= thresh]
            n, wr, pnl, days = analyze(filt)
            if n < 5 or wr < 58: continue
            op = '>=' if direction == 'ge' else '<='
            raw_combos.append({
                'label': f'ORB-{range_bars*5}min+vol {ind}{op}{thresh}',
                'n': n, 'wr': wr, 'pnl': pnl, 'days': days
            })

raw_combos.sort(key=lambda x: (-x['wr'], -x['n']))
seen_labels = set()
printed = 0
print(f"{'Filter':>55} {'N':>4} {'WR':>5} {'Days':>5} {'P&L':>8}")
print('-'*85)
for c in raw_combos:
    if c['label'] in seen_labels: continue
    seen_labels.add(c['label'])
    marker = ' ***' if c['wr'] >= 80 else (' <<' if c['wr'] >= 70 else '')
    print(f"{c['label']:>55} {c['n']:>4} {c['wr']:>4.0f}% {c['days']:>5} {c['pnl']:>+7.1f}%{marker}")
    printed += 1
    if printed >= 40: break

# ─── 4. ORB + MoE + RAW STACKS (the full combine) ───
print('\n' + '='*80)
print('ORB + MoE + RAW INDICATOR STACKS')
print('='*80)

stack_combos = []
for range_bars in [1, 2, 3, 5]:
    for vol_filter in [True]:
        rb = [s for s in base if s['range_bars']==range_bars and (not vol_filter or s['vol_ok'])]
        if len(rb) < 10: continue

        for m_min in [0, 7, 8]:
            for p_min in [0, 7]:
                for mkt_min in [0, 6]:
                    moe_filt = [s for s in rb if s['M']>=m_min and s['P']>=p_min and s['Mkt']>=mkt_min]
                    if len(moe_filt) < 5: continue

                    # Add raw filters
                    for candle_max in [99, 0, -1]:
                        for noise_max in [99, 3, 2.5]:
                            for morning_min in [-99, 0, 0.5]:
                                filt = [s for s in moe_filt
                                        if s['candle']<=candle_max and s['noise']<=noise_max
                                        and s['morning_adj']>=morning_min]
                                n, wr, pnl, days = analyze(filt)
                                if n < 5 or wr < 65: continue
                                vl = '+vol' if vol_filter else ''
                                parts = [f'ORB-{range_bars*5}min{vl}']
                                if m_min > 0: parts.append(f'M>={m_min}')
                                if p_min > 0: parts.append(f'P>={p_min}')
                                if mkt_min > 0: parts.append(f'Mkt>={mkt_min}')
                                if candle_max < 99: parts.append(f'c<={candle_max}')
                                if noise_max < 99: parts.append(f'n<={noise_max}')
                                if morning_min > -99: parts.append(f'morn>={morning_min}')
                                stack_combos.append({
                                    'label': ' '.join(parts),
                                    'n': n, 'wr': wr, 'pnl': pnl, 'days': days
                                })

                    # Add RS filter
                    for rs_max in [20, 30, 50]:
                        filt = [s for s in moe_filt if s['rs_pctile'] <= rs_max]
                        n, wr, pnl, days = analyze(filt)
                        if n < 5 or wr < 65: continue
                        vl = '+vol' if vol_filter else ''
                        parts = [f'ORB-{range_bars*5}min{vl}']
                        if m_min > 0: parts.append(f'M>={m_min}')
                        if p_min > 0: parts.append(f'P>={p_min}')
                        if mkt_min > 0: parts.append(f'Mkt>={mkt_min}')
                        parts.append(f'RS<={rs_max}%')
                        stack_combos.append({
                            'label': ' '.join(parts),
                            'n': n, 'wr': wr, 'pnl': pnl, 'days': days
                        })

stack_combos.sort(key=lambda x: (-x['wr'], -x['n']))
seen_labels = set()
printed = 0
print(f"{'Stack':>65} {'N':>4} {'WR':>5} {'Days':>5} {'P&L':>8}")
print('-'*95)
for c in stack_combos:
    if c['label'] in seen_labels: continue
    seen_labels.add(c['label'])
    marker = ' ***' if c['wr'] >= 90 else (' <<' if c['wr'] >= 80 else (' <' if c['wr'] >= 70 else ''))
    print(f"{c['label']:>65} {c['n']:>4} {c['wr']:>4.0f}% {c['days']:>5} {c['pnl']:>+7.1f}%{marker}")
    printed += 1
    if printed >= 50: break

# ─── 5. PER-STOCK ORB ANALYSIS ───
print('\n' + '='*80)
print('PER-STOCK ORB PERFORMANCE (ORB-5min+vol, R:R 1.5, exit 1PM)')
print('='*80)

stock_sub = [s for s in all_signals if s['range_bars']==1 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM']
stock_results = []
for sym in sorted(all_data.keys()):
    sub = [s for s in stock_sub if s['sym'] == sym]
    if len(sub) < 3: continue
    w = sum(s['win'] for s in sub)
    wr = w/len(sub)*100
    pnl = sum(s['pnl'] for s in sub)
    stock_results.append({'sym': sym, 'n': len(sub), 'wr': wr, 'pnl': pnl})

stock_results.sort(key=lambda x: -x['wr'])
print(f"{'Stock':>12} {'N':>4} {'WR':>5} {'P&L':>8}")
print('-'*35)
best_stocks = []
worst_stocks = []
for s in stock_results:
    marker = ' <<<' if s['wr'] >= 65 else (' !!!' if s['wr'] <= 35 else '')
    print(f"  {s['sym']:>12} {s['n']:>4} {s['wr']:>4.0f}% {s['pnl']:>+7.1f}%{marker}")
    if s['wr'] >= 60: best_stocks.append(s['sym'])
    if s['wr'] <= 35: worst_stocks.append(s['sym'])

# ─── 6. STOCK WHITELIST FILTER ───
print('\n' + '='*80)
print(f'STOCK WHITELIST: Only trade {best_stocks}')
print('='*80)
if best_stocks:
    for range_bars in [1, 2, 3]:
        for rr in [1.5, 2.0, 2.5]:
            for exit_name in ['1PM', '2PM', '3PM']:
                sub = [s for s in all_signals if s['sym'] in best_stocks and s['range_bars']==range_bars
                       and s['rr']==rr and s['exit']==exit_name and s['vol_ok']]
                n, wr, pnl, days = analyze(sub)
                if n < 5 or wr < 55: continue
                print(f'  ORB-{range_bars*5}min R:{rr} {exit_name}: {n:>3} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

# ─── 6b. STOCK BLACKLIST FILTER ───
print(f'\nSTOCK BLACKLIST: Exclude {worst_stocks}')
if worst_stocks:
    for range_bars in [1, 2, 3, 5]:
        for exit_name in ['1PM']:
            sub = [s for s in all_signals if s['sym'] not in worst_stocks and s['range_bars']==range_bars
                   and s['rr']==1.5 and s['exit']==exit_name and s['vol_ok']]
            n, wr, pnl, days = analyze(sub)
            if n < 10: continue
            print(f'  ORB-{range_bars*5}min R:1.5 {exit_name}: {n:>3} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

# ─── 7. WALK-FORWARD VALIDATION ───
print('\n' + '='*80)
print('WALK-FORWARD: Train on first 80 days, test on last 41')
print('='*80)

train_dates = all_dates[:80]
test_dates = all_dates[80:]
print(f'Train: {train_dates[0]} to {train_dates[-1]} ({len(train_dates)} days)')
print(f'Test:  {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} days)')

# Test the best setups on out-of-sample
test_filters = [
    ('ORB-5min+vol R:1.5 1PM (raw)',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-15min+vol R:1.5 1PM',
     lambda s: s['range_bars']==3 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-25min+vol R:1.5 1PM',
     lambda s: s['range_bars']==5 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-5min+vol M>=7 R:1.5 1PM',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['M']>=7 and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-5min+vol M>=8 R:1.5 1PM',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['M']>=8 and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-5min+vol P>=7 M>=7 R:1.5 1PM',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['P']>=7 and s['M']>=7 and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-5min+vol c<=-1 R:1.5 1PM',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['candle']<=-1 and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-5min+vol noise<=3 R:1.5 1PM',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['noise']<=3 and s['rr']==1.5 and s['exit']=='1PM'),
    ('ORB-5min+vol RS<=30% R:1.5 1PM',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['rs_pctile']<=30 and s['rr']==1.5 and s['exit']=='1PM'),
]

# Also dynamically add the top stacks found
if stack_combos:
    top_stack = stack_combos[0]
    # Parse and add (simplified — just test fixed filters)

print(f"\n{'Setup':>45} | {'Train':>25} | {'Test':>25}")
print(f"{'':>45} | {'N':>4} {'WR':>5} {'Days':>4} {'P&L':>8} | {'N':>4} {'WR':>5} {'Days':>4} {'P&L':>8}")
print('-'*110)

for label, filt in test_filters:
    train_sigs = [s for s in all_signals if s['date'] in set(train_dates) and filt(s)]
    test_sigs = [s for s in all_signals if s['date'] in set(test_dates) and filt(s)]

    tn, twr, tpnl, tdays = analyze(train_sigs)
    en, ewr, epnl, edays = analyze(test_sigs)

    overfit = '  OVERFIT' if twr > 55 and ewr < 45 else ''
    holds = '  HOLDS' if ewr >= twr - 10 and ewr >= 50 else ''
    print(f"{label:>45} | {tn:>4} {twr:>4.0f}% {tdays:>4} {tpnl:>+7.1f}% | {en:>4} {ewr:>4.0f}% {edays:>4} {epnl:>+7.1f}%{overfit}{holds}")


# ─── 8. DAILY BEST TRADE PICKER ───
print('\n' + '='*80)
print('DAILY PICKER: 1 trade per day, various strategies')
print('='*80)

daily_strategies = [
    ('ORB-5min+vol, pick highest moe',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: s['moe']),
    ('ORB-5min+vol, pick strongest RS',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: -s['rs_pctile']),
    ('ORB-5min+vol, pick highest vol_spike',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: s['vol_spike']),
    ('ORB-5min+vol M>=7, pick highest moe',
     lambda s: s['range_bars']==1 and s['vol_ok'] and s['M']>=7 and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: s['moe']),
    ('ORB-15min+vol, pick highest moe',
     lambda s: s['range_bars']==3 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: s['moe']),
    ('ORB-15min+vol, pick strongest RS',
     lambda s: s['range_bars']==3 and s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: -s['rs_pctile']),
    ('Any ORB+vol, pick highest moe',
     lambda s: s['vol_ok'] and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: s['moe']),
    ('Any ORB+vol+M>=7, pick highest moe',
     lambda s: s['vol_ok'] and s['M']>=7 and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: s['moe']),
    ('Any ORB+vol+M>=7, pick strongest RS',
     lambda s: s['vol_ok'] and s['M']>=7 and s['rr']==1.5 and s['exit']=='1PM',
     lambda s: -s['rs_pctile']),
]

print(f"\n{'Strategy':>50} {'Days':>5} {'W':>3} {'L':>3} {'WR':>5} {'P&L':>8} {'Avg':>7}")
print('-'*90)
for label, filt, ranker in daily_strategies:
    wins = 0; total = 0; pnl_sum = 0
    for date in all_dates:
        pool = [s for s in all_signals if s['date'] == date and filt(s)]
        if not pool: continue
        # Dedup by sym
        by_sym = {}
        for s in pool:
            if s['sym'] not in by_sym or s['moe'] > by_sym[s['sym']]['moe']:
                by_sym[s['sym']] = s
        pool = list(by_sym.values())
        best = max(pool, key=ranker)
        total += 1
        if best['win']: wins += 1
        pnl_sum += best['pnl']
    if total < 3: continue
    wr = wins/total*100
    avg = pnl_sum/total
    marker = ' <<<' if wr >= 55 else ''
    print(f"{label:>50} {total:>5} {wins:>3} {total-wins:>3} {wr:>4.0f}% {pnl_sum:>+7.2f}% {avg:>+6.3f}%{marker}")


# Save
with open('data/grind_orb_results.json', 'w') as f:
    json.dump({'total_signals': len(all_signals), 'best_stocks': best_stocks, 'worst_stocks': worst_stocks}, f)
print(f'\nDone. {len(all_signals)} signals analyzed.')
