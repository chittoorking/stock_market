"""
BOTTOM-UP v3 — MoE scores + raw indicators, FAST.
Pre-computes sector/market/macro ONCE per date (not per signal).
Then computes per-signal scores (volume, price, momentum) inline.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.mega_strategies import *
from app.agents.coded_moe import score_volume, score_price, score_momentum
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer

# ─── DATA LOADING ───
data_dir = Path('data/5min')
all_data = {}
date_bars = defaultdict(dict)

print('Loading and indexing data...', flush=True)
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

# Pre-compute prev close
prev_close_map = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i == 0: continue
        prev_d = dates_for_sym[i-1]
        prev_d_bars = date_bars[prev_d].get(sym, [])
        if prev_d_bars:
            prev_close_map[(d, sym)] = prev_d_bars[-1]['close']

# Pre-compute ATR bars
prev_bars_for_atr = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    cum_bars = []
    for d in dates_for_sym:
        prev_bars_for_atr[(d, sym)] = cum_bars[-30:] if len(cum_bars) >= 5 else []
        cum_bars.extend(date_bars[d].get(sym, []))

# Macro
macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

# Delivery
delivery = {}
for f in Path('data/delivery').glob('*.csv'):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh:
        delivery[sym] = {r['date']: r for r in csv.DictReader(fh)}

vol_agent = VolatilityAgent()
EXIT_BAR = 69
SCAN_BAR = 6

# ─── PRE-COMPUTE DATE-LEVEL SCORES ───
print('Pre-computing sector, market, macro per date...', flush=True)

# sector_data_cache[date] = SectorAnalyzer.compute result
sector_data_cache = {}
# market_breadth[date] = (up_count, down_count, total_count)
market_breadth_cache = {}
# macro scores per date
macro_cache = {}

for date in all_dates:
    # Sector data (the expensive one)
    sector_data_cache[date] = SectorAnalyzer.compute(all_data, date, SCAN_BAR)

    # Market breadth
    up = 0; down = 0; total = 0
    for sym in date_bars[date]:
        if sym in ('NIFTY_50', 'NIFTY_BANK'): continue
        db = date_bars[date][sym]
        if len(db) <= SCAN_BAR: continue
        total += 1
        move = (db[SCAN_BAR]['close'] - db[0]['open']) / db[0]['open'] * 100
        if move > 0.15: up += 1
        elif move < -0.15: down += 1
    market_breadth_cache[date] = (up, down, total)

    # Macro
    mc = macro.get(date, {})
    macro_cache[date] = mc

print('Pre-computation done.', flush=True)


def fast_score_sector(symbol, date, direction):
    """Sector score using pre-computed sector data."""
    score = 5.0
    sector = get_sector(symbol)
    sec_info = SECTOR_MAP.get(sector, {})
    if not sec_info: return 4.0
    sec = sector_data_cache[date].get(sector, {})
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


def fast_score_market(date, direction):
    """Market score using pre-computed breadth."""
    score = 5.0
    up, down, total = market_breadth_cache[date]
    if total == 0: return 5.0
    up_pct = up / total * 100
    down_pct = down / total * 100
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
    """Macro score using pre-loaded data."""
    score = 5.0
    ctx = macro_cache.get(date, {})
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


# ─── MAIN SCAN ───
print(f'Scanning {len(all_dates)} days × {len(all_data)} stocks × 11 strategies...', flush=True)
results = []
t0 = time.time()

for di, date in enumerate(all_dates):
    crowd = 0; total_syms = 0
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pc) / pc * 100
        if abs(gap) > 0.3: crowd += 1

    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: dow = -1
    mc = macro_cache.get(date, {})
    vix = mc.get('india_vix', 0)
    us_ret = mc.get('sp500_overnight', 0)
    nifty_prev = mc.get('nifty_prev_return', 0)

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc_val = prev_close_map.get((date, sym))
        if pc_val is None or len(db) <= SCAN_BAR: continue
        bsf = db[:SCAN_BAR+1]

        for scanner in [
            lambda: GapAndGoSignal.scan(sym, bsf[:3], pc_val),
            lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
            lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
            lambda: GapDecaySignal.scan(sym, bsf, pc_val, crowd, total_syms) if len(bsf)>=4 else None,
            lambda: OpeningRangeBreakout.scan(sym, bsf, pc_val) if len(bsf)>=7 else None,
            lambda: PivotBreakout.scan(sym, bsf, pc_val) if len(bsf)>=4 else None,
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

            # ═══ MoE EXPERT SCORES (fast) ═══
            v_score = score_volume(sym, db[:SCAN_BAR+1], all_data, date, SCAN_BAR)
            s_score = fast_score_sector(sym, date, direction)
            p_score = score_price(sym, db[:SCAN_BAR+1], SCAN_BAR, pc_val, direction)
            m_score = score_momentum(sym, db[:SCAN_BAR+1], SCAN_BAR, direction)
            mkt_score = fast_score_market(date, direction)
            mac_score = fast_score_macro(date, direction)
            moe_total = v_score*1.5 + m_score*1.3 + p_score*0.8 + s_score*0.5 + mkt_score*0.5 + mac_score*0.5

            # ═══ KEY RAW INDICATORS ═══
            dir_mult = 1 if direction == 'LONG' else -1
            gap_pct = (bsf[0]['open'] - pc_val)/pc_val * 100
            morning_move = (price - bsf[0]['open'])/bsf[0]['open'] * 100
            gap_adj = gap_pct * dir_mult
            morning_adj = morning_move * dir_mult

            # VWAP
            tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
            cum_vol = sum(b['volume'] for b in bsf)
            vwap = tp_vol/cum_vol if cum_vol > 0 else price
            vwap_adj = (price - vwap)/vwap * 100 * dir_mult

            # RSI
            if len(closes) >= 7:
                gains = [max(0, closes[i]-closes[i-1]) for i in range(1, len(closes))]
                loss_l = [max(0, closes[i-1]-closes[i]) for i in range(1, len(closes))]
                ag = sum(gains[-6:])/6; al = sum(loss_l[-6:])/6
                rsi = 100 - 100/(1+ag/al) if al > 0 else 50
            else: rsi = 50

            # Consec
            consec = 0
            for k in range(len(bsf)-1, 0, -1):
                bar_green = bsf[k]['close'] > bsf[k]['open']
                if (direction=='LONG' and bar_green) or (direction=='SHORT' and not bar_green): consec += 1
                else: break

            # Candle
            body = abs(bsf[-1]['close']-bsf[-1]['open'])
            rng = bsf[-1]['high']-bsf[-1]['low']
            body_ratio = body/rng*100 if rng > 0 else 0
            upper_wick = bsf[-1]['high'] - max(bsf[-1]['open'], bsf[-1]['close'])
            lower_wick = min(bsf[-1]['open'], bsf[-1]['close']) - bsf[-1]['low']
            candle = 0
            if rng > 0:
                br = body/rng
                if br > 0.8: candle = 2 if bsf[-1]['close'] > bsf[-1]['open'] else -2
                elif lower_wick > body*2 and upper_wick < body*0.5: candle = 1
                elif upper_wick > body*2 and lower_wick < body*0.5: candle = -1

            # Noise
            ranges_sum = sum(b['high']-b['low'] for b in bsf[-min(5,len(bsf)):])
            move = abs(closes[-1]-closes[max(0,len(closes)-5)])
            noise = ranges_sum / (move + 0.001)

            # ATR%
            atr_pct = sum(b['high']-b['low'] for b in bsf)/len(bsf) / price * 100 if price > 0 else 0

            # Delivery
            del_data = delivery.get(sym, {})
            recent_del = sorted(d for d in del_data if d < date)
            del_pct = 0
            if recent_del:
                try: del_pct = float(del_data[recent_del[-1]].get('delivery_pct', 0) or 0)
                except: pass

            # ═══ SIMULATE TRADE ═══
            entry = sig.suggested_entry
            atr_bars = prev_bars_for_atr.get((date, sym), [])
            atr_val = vol_agent.calculate_atr(atr_bars) if len(atr_bars) >= 5 else 0
            stop, _ = SmartStopCalculator.calculate(db, SCAN_BAR, entry, direction, atr_val)
            target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)
            exit_price = None
            for j in range(SCAN_BAR+1, min(len(db), EXIT_BAR+1)):
                if direction == 'LONG':
                    if db[j]['low'] <= stop: exit_price = stop; break
                    if db[j]['high'] >= target: exit_price = target; break
                else:
                    if db[j]['high'] >= stop: exit_price = stop; break
                    if db[j]['low'] <= target: exit_price = target; break
            if not exit_price:
                exit_price = db[min(EXIT_BAR, len(db)-1)]['close']
            pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100

            results.append({
                'date': date, 'sym': sym, 'dir': direction, 'strat': sig.strategy_name,
                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                'V': v_score, 'S': s_score, 'P': p_score, 'M': m_score,
                'Mkt': mkt_score, 'Mac': mac_score, 'moe': round(moe_total, 1),
                'rsi': round(rsi,1), 'gap_adj': round(gap_adj,3), 'morning_adj': round(morning_adj,3),
                'vwap_adj': round(vwap_adj,3), 'consec': consec, 'candle': candle,
                'body_ratio': round(body_ratio,1), 'noise': round(noise,2),
                'atr_pct': round(atr_pct,3), 'vix': round(vix,1), 'del_pct': round(del_pct,1),
                'dow': dow,
            })

    if (di+1) % 20 == 0:
        elapsed = time.time() - t0
        rate = (di+1) / elapsed
        remaining = (len(all_dates) - di - 1) / rate
        print(f'  {di+1}/{len(all_dates)} ({(di+1)/len(all_dates)*100:.0f}%) | {len(results)} sigs | ETA {remaining:.0f}s', flush=True)

total_w = sum(r['win'] for r in results)
print(f'\nTotal: {len(results)} signals, {total_w} winners ({total_w/len(results)*100:.0f}% WR)')
print(f'Time: {time.time()-t0:.1f}s')

# ═══════════════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════════════

# ─── 1. MoE thresholds check ───
print('\n' + '='*80)
print('MoE SCORE THRESHOLDS:')
print('='*80)
for label, filt in [
    ('100% WR lock: M>=9, Mkt>=7, P>=7', lambda r: r['M']>=9 and r['Mkt']>=7 and r['P']>=7),
    ('V>=5,S>=4,P>=7,M>=9,Mkt>=7', lambda r: r['V']>=5 and r['S']>=4 and r['P']>=7 and r['M']>=9 and r['Mkt']>=7),
    ('moe >= 40', lambda r: r['moe'] >= 40),
    ('moe >= 35', lambda r: r['moe'] >= 35),
    ('moe >= 30', lambda r: r['moe'] >= 30),
    ('moe >= 25', lambda r: r['moe'] >= 25),
    ('moe >= 20', lambda r: r['moe'] >= 20),
]:
    sub = [r for r in results if filt(r)]
    if not sub: print(f'  {label}: 0 trades'); continue
    w = sum(r['win'] for r in sub)
    wr = w/len(sub)*100
    pnl = sum(r['pnl'] for r in sub)
    days = len(set(r['date'] for r in sub))
    print(f'  {label}: {len(sub)} trades, WR={wr:.0f}%, P&L={pnl:+.2f}%, {days} days')

# ─── 2. Individual MoE component ranges ───
print('\n' + '='*80)
print('INDIVIDUAL MoE COMPONENT ANALYSIS:')
print('='*80)
for comp in ['V','S','P','M','Mkt','Mac']:
    print(f'\n{comp}:')
    for thresh in range(3, 10):
        sub = [r for r in results if r[comp] >= thresh]
        if len(sub) < 10: continue
        w = sum(r['win'] for r in sub)
        wr = w/len(sub)*100
        pnl = sum(r['pnl'] for r in sub)
        marker = ' <<<' if wr >= 50 else ''
        print(f'  {comp}>={thresh}: {len(sub):>5} trades, WR={wr:.0f}%, P&L={pnl:+.2f}%{marker}')

# ─── 3. Relaxed MoE + raw indicator combos ───
print('\n' + '='*80)
print('MoE + RAW INDICATOR COMBOS (WR >= 55%):')
print('='*80)

raw_inds = ['rsi','gap_adj','morning_adj','vwap_adj','consec','candle','body_ratio',
            'noise','atr_pct','vix','del_pct','dow']

combos = []
for moe_min in [20, 22, 25, 28, 30, 33, 35]:
    moe_base = [r for r in results if r['moe'] >= moe_min]
    if len(moe_base) < 10: continue
    for ind in raw_inds:
        vals = sorted(r[ind] for r in moe_base)
        n = len(vals)
        for cut in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            idx = int(n * cut)
            threshold = vals[idx]
            # Try >= threshold
            sub = [r for r in moe_base if r[ind] >= threshold]
            if 5 <= len(sub) <= len(moe_base) * 0.8:
                w = sum(r['win'] for r in sub)
                wr = w/len(sub)*100
                if wr >= 55:
                    pnl = sum(r['pnl'] for r in sub)
                    days = len(set(r['date'] for r in sub))
                    combos.append({'label': f'moe>={moe_min} + {ind}>={threshold:.2f}',
                                  'n': len(sub), 'w': w, 'wr': wr, 'pnl': pnl, 'days': days})
            # Try <= threshold
            sub = [r for r in moe_base if r[ind] <= threshold]
            if 5 <= len(sub) <= len(moe_base) * 0.8:
                w = sum(r['win'] for r in sub)
                wr = w/len(sub)*100
                if wr >= 55:
                    pnl = sum(r['pnl'] for r in sub)
                    days = len(set(r['date'] for r in sub))
                    combos.append({'label': f'moe>={moe_min} + {ind}<={threshold:.2f}',
                                  'n': len(sub), 'w': w, 'wr': wr, 'pnl': pnl, 'days': days})

combos.sort(key=lambda x: (-x['wr'], -x['n']))
seen = set()
printed = 0
print(f'Found {len(combos)} combos with WR >= 55%')
print(f"\n{'Filter':>55} {'N':>4} {'W':>3} {'WR':>5} {'Days':>5} {'P&L':>8}")
print('-'*85)
for c in combos:
    if c['label'] in seen: continue
    seen.add(c['label'])
    marker = ' ***' if c['wr'] >= 80 else (' <<' if c['wr'] >= 70 else '')
    print(f"{c['label']:>55} {c['n']:>4} {c['w']:>3} {c['wr']:>4.0f}% {c['days']:>5} {c['pnl']:>+7.2f}%{marker}")
    printed += 1
    if printed >= 50: break

# ─── 4. Exhaustive 2-component MoE combos ───
print('\n' + '='*80)
print('2-COMPONENT MoE COMBOS (WR >= 60%):')
print('='*80)
moe_comps = ['V','S','P','M','Mkt','Mac']
moe_combos = []
for i in range(len(moe_comps)):
    for j in range(i+1, len(moe_comps)):
        c1, c2 = moe_comps[i], moe_comps[j]
        for t1 in range(5, 10):
            for t2 in range(5, 10):
                sub = [r for r in results if r[c1] >= t1 and r[c2] >= t2]
                if len(sub) < 5: continue
                w = sum(r['win'] for r in sub)
                wr = w/len(sub)*100
                if wr < 60: continue
                pnl = sum(r['pnl'] for r in sub)
                days = len(set(r['date'] for r in sub))
                moe_combos.append({'label': f'{c1}>={t1} + {c2}>={t2}', 'n': len(sub), 'w': w,
                                  'wr': wr, 'pnl': pnl, 'days': days})

moe_combos.sort(key=lambda x: (-x['wr'], -x['n']))
print(f'Found {len(moe_combos)} 2-component combos with WR >= 60%')
for c in moe_combos[:30]:
    marker = ' ***' if c['wr'] >= 90 else (' <<' if c['wr'] >= 80 else '')
    print(f"  {c['label']:>20} | {c['n']:>3} trades, WR={c['wr']:.0f}%, {c['days']} days, P&L={c['pnl']:+.2f}%{marker}")

# ─── 5. 3-component MoE combos ───
print('\n' + '='*80)
print('3-COMPONENT MoE COMBOS (WR >= 70%):')
print('='*80)
three_combos = []
for i in range(len(moe_comps)):
    for j in range(i+1, len(moe_comps)):
        for k in range(j+1, len(moe_comps)):
            c1, c2, c3 = moe_comps[i], moe_comps[j], moe_comps[k]
            for t1 in range(5, 10):
                for t2 in range(5, 10):
                    for t3 in range(5, 10):
                        sub = [r for r in results if r[c1]>=t1 and r[c2]>=t2 and r[c3]>=t3]
                        if len(sub) < 5: continue
                        w = sum(r['win'] for r in sub)
                        wr = w/len(sub)*100
                        if wr < 70: continue
                        pnl = sum(r['pnl'] for r in sub)
                        days = len(set(r['date'] for r in sub))
                        three_combos.append({
                            'label': f'{c1}>={t1}+{c2}>={t2}+{c3}>={t3}',
                            'n': len(sub), 'w': w, 'wr': wr, 'pnl': pnl, 'days': days
                        })

three_combos.sort(key=lambda x: (-x['wr'], -x['n']))
# Deduplicate by keeping unique n/wr combos
seen3 = set()
printed3 = 0
print(f'Found {len(three_combos)} 3-component combos with WR >= 70%')
for c in three_combos:
    key = (c['n'], round(c['wr']))
    if key in seen3: continue
    seen3.add(key)
    marker = ' ***' if c['wr'] == 100 else (' <<' if c['wr'] >= 90 else '')
    print(f"  {c['label']:>25} | {c['n']:>3} trades, WR={c['wr']:.0f}%, {c['days']} days, P&L={c['pnl']:+.2f}%{marker}")
    printed3 += 1
    if printed3 >= 30: break

# ─── 6. Daily best trade analysis ───
print('\n' + '='*80)
print('DAILY BEST TRADE (pick #1 moe per day):')
print('='*80)
for moe_min in [20, 22, 25, 28, 30, 33, 35, 38, 40]:
    wins = 0; total = 0; pnl_sum = 0
    for date in all_dates:
        day_sigs = [r for r in results if r['date'] == date and r['moe'] >= moe_min]
        if not day_sigs: continue
        best = max(day_sigs, key=lambda x: x['moe'])
        total += 1
        if best['win']: wins += 1
        pnl_sum += best['pnl']
    if total < 3: continue
    wr = wins/total*100
    marker = ' <<<' if wr >= 60 else ''
    print(f'  moe>={moe_min:>2}: {total:>3} days | W:{wins} L:{total-wins} | WR:{wr:.0f}% | P&L:{pnl_sum:+.2f}%{marker}')

# ─── 7. Profile of 100% WR trades ───
print('\n' + '='*80)
print('PROFILE of 100% WR trades (M>=9, Mkt>=7, P>=7):')
print('='*80)
perfect = [r for r in results if r['M']>=9 and r['Mkt']>=7 and r['P']>=7]
if perfect:
    print(f'{len(perfect)} trades, all winners: {all(r["win"] for r in perfect)}')
    for key in ['V','S','P','M','Mkt','Mac','moe','rsi','gap_adj','morning_adj','vwap_adj',
                'consec','candle','body_ratio','noise','atr_pct','vix','del_pct','dow']:
        vals = [r[key] for r in perfect]
        print(f'  {key:>12}: min={min(vals):>7.2f} max={max(vals):>7.2f} mean={sum(vals)/len(vals):>7.2f}')

# Save
with open('data/bottom_up_v3_results.json', 'w') as f:
    json.dump(results, f)
print(f'\nSaved {len(results)} signals to data/bottom_up_v3_results.json')
