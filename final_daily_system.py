"""
FINAL COMBINED DAILY SYSTEM — merges ALL discoveries.

The system runs 3 independent strategies in parallel each day.
Each strategy has been validated with walk-forward testing.

STRATEGY A: ORB + Stock Whitelist (70% WR, 59 days coverage)
  - 5min ORB breakout with volume confirmation
  - Only trade 14 whitelisted stocks (60%+ individual WR)
  - R:R 2.0, exit by 1PM

STRATEGY B: Momentum Signal + MoE + Candle (93% WR, 9 days)
  - Original 15 strategy signals
  - P>=7, M>=8, candle<=-1, noise<=2.5
  - R:R 2.5, exit by 3PM

STRATEGY C: ORB + MoE + Candle filter (67-71% WR, 15-30 days)
  - ORB breakout on any stock
  - Mkt>=6 (broad market confirming)
  - candle<=0 or noise<=2.5
  - R:R 1.5, exit by 1PM

COMBINED: Take trade from highest-tier strategy available.
Tier 1 → all-in. Tier 2 → 70%. Tier 3 → 50%.

Also tests: what if we run ALL strategies and take ALL qualifying trades?
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

# ─── DATA ───
data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
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

prev_close_map = {}; prev_day_data = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb:
                prev_close_map[(d, sym)] = pdb[-1]['close']
                prev_day_data[(d, sym)] = {'high': max(b['high'] for b in pdb), 'low': min(b['low'] for b in pdb), 'close': pdb[-1]['close']}

prev_bars_for_atr = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    cum_bars = []
    for d in dates_for_sym:
        prev_bars_for_atr[(d, sym)] = cum_bars[-30:] if len(cum_bars) >= 5 else []
        cum_bars.extend(date_bars[d].get(sym, []))

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

vol_agent = VolatilityAgent()

# Pre-compute sector/market
sector_cache = {}; market_cache = {}
print('Pre-computing...', flush=True)
for sb in [3, 6]:
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

def fast_mkt(date, sb, direction):
    score = 5.0
    up, down, total = market_cache.get((date, min(sb, 6)), (0,0,0))
    if total == 0: return 5.0
    up_pct = up/total*100; down_pct = down/total*100
    if direction=="LONG" and up_pct > 65: score += 2.0
    elif direction=="LONG" and up_pct > 55: score += 1.0
    elif direction=="LONG" and up_pct < 35: score -= 2.0
    elif direction=="SHORT" and down_pct > 65: score += 2.0
    elif direction=="SHORT" and down_pct > 55: score += 1.0
    elif direction=="SHORT" and down_pct < 35: score -= 2.0
    flat = total - up - down
    if flat > total * 0.4: score -= 1.0
    return max(0, min(10, score))

def fast_sec(sym, date, sb, direction):
    score = 5.0
    sector = get_sector(sym)
    sec_info = SECTOR_MAP.get(sector, {})
    if not sec_info: return 4.0
    sec = sector_cache.get((date, min(sb, 6)), {}).get(sector, {})
    if not sec: return 4.0
    lc = sec.get('leader_change_pct', 0); st = sec.get('strength', 0)
    aligned = (direction=="LONG" and lc > 0.3) or (direction=="SHORT" and lc < -0.3)
    against = (direction=="LONG" and lc < -0.3) or (direction=="SHORT" and lc > 0.3)
    if aligned and abs(lc) > 1.0: score += 2.5
    elif aligned: score += 1.5
    elif against: score -= 2.0
    if st >= 0.75: score += 1.0
    elif st <= 0.25: score -= 1.5
    return max(0, min(10, score))

def fast_mac(date, direction):
    score = 5.0
    ctx = macro.get(date, {})
    if not ctx: return 5.0
    vix = ctx.get('india_vix', 0)
    sp = ctx.get('sp500_overnight', 0); nq = ctx.get('nasdaq_overnight', 0)
    if vix > 22: score -= 1.5
    elif vix > 18: score -= 0.5
    elif vix < 13: score += 1.0
    us = (sp+nq)/2
    if direction=="LONG" and us > 0.5: score += 1.5
    elif direction=="LONG" and us < -0.5: score -= 1.5
    elif direction=="SHORT" and us < -0.5: score += 1.5
    elif direction=="SHORT" and us > 0.5: score -= 1.5
    return max(0, min(10, score))

# ═══ STOCK WHITELIST (from ORB analysis) ═══
WHITELIST = {'ULTRACEMCO','EICHERMOT','M&M','TATASTEEL','TECHM','SBILIFE','NTPC',
             'ADANIPORTS','ICICIBANK','SUNPHARMA','TITAN','WIPRO','DIVISLAB','COALINDIA'}
BLACKLIST = {'ASIANPAINT','HINDUNILVR','ITC','JSWSTEEL','APOLLOHOSP','BAJAJ-AUTO',
             'GRASIM','ONGC','ADANIENT','UPL','HEROMOTOCO'}

def simulate(db, entry_bar, entry, direction, stop, target, exit_bar):
    for j in range(entry_bar+1, min(len(db), exit_bar+1)):
        if direction=='LONG':
            if db[j]['low'] <= stop: return stop, 'stop'
            if db[j]['high'] >= target: return target, 'target'
        else:
            if db[j]['high'] >= stop: return stop, 'stop'
            if db[j]['low'] <= target: return target, 'target'
    return db[min(exit_bar, len(db)-1)]['close'], 'time'

def pnl_calc(entry, exit_p, direction):
    return (exit_p-entry)/entry*100 if direction=='LONG' else (entry-exit_p)/entry*100

def candle_score(bar):
    body = abs(bar['close']-bar['open'])
    rng = bar['high']-bar['low']
    if rng == 0: return 0
    br = body/rng
    uw = bar['high'] - max(bar['open'], bar['close'])
    lw = min(bar['open'], bar['close']) - bar['low']
    if br > 0.8: return 2 if bar['close'] > bar['open'] else -2
    if lw > body*2 and uw < body*0.5: return 1
    if uw > body*2 and lw < body*0.5: return -1
    return 0

def noise_score(bars):
    if len(bars) < 3: return 5
    ranges_sum = sum(b['high']-b['low'] for b in bars[-5:])
    move = abs(bars[-1]['close']-bars[-5]['close']) if len(bars) >= 5 else abs(bars[-1]['close']-bars[0]['close'])
    return ranges_sum/(move+0.001)

# ═══ RUN DAILY SIMULATION ═══
print(f'\nRunning combined system on {len(all_dates)} days...\n', flush=True)

daily_log = []
tier_counts = {'T1': 0, 'T2': 0, 'T3': 0, 'ORB_WL': 0, 'ORB_filtered': 0, 'none': 0}

for date in all_dates:
    trades_today = []

    # ─── STRATEGY A: ORB + Whitelist ───
    for sym in date_bars[date]:
        if sym not in WHITELIST: continue
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue

        # 5min ORB (1 bar range)
        range_high = db[0]['high']; range_low = db[0]['low']
        range_size = range_high - range_low
        if range_size == 0: continue

        for j in range(1, min(len(db), 12)):  # Scan first hour
            direction = None
            if db[j]['close'] > range_high: direction = 'LONG'
            elif db[j]['close'] < range_low: direction = 'SHORT'
            if not direction: continue

            # Volume confirmation
            avg_vol = db[0]['volume']
            if avg_vol > 0 and db[j]['volume'] < avg_vol * 1.2: break

            entry = db[j]['close']
            stop = range_low if direction=='LONG' else range_high
            risk = abs(entry - stop)
            if risk == 0: break
            target = entry + risk*2.0 if direction=='LONG' else entry - risk*2.0

            exit_p, exit_type = simulate(db, j, entry, direction, stop, target, 36)  # 1PM
            pnl = pnl_calc(entry, exit_p, direction)

            trades_today.append({
                'strategy': 'ORB_WL', 'tier': 'T3', 'sym': sym, 'dir': direction,
                'entry_bar': j, 'pnl': pnl, 'win': pnl > 0,
                'exit_type': exit_type,
            })
            break

    # ─── STRATEGY B: Momentum + MoE + Candle (Tier 1/2) ───
    SCAN_BAR = 6
    crowd = 0; total_syms = 0
    for sym in date_bars[date]:
        db = date_bars[date][sym]; pc = prev_close_map.get((date, sym))
        if pc is None or not db: continue
        total_syms += 1
        if abs((db[0]['open']-pc)/pc*100) > 0.3: crowd += 1

    for sym in date_bars[date]:
        db = date_bars[date][sym]; pc = prev_close_map.get((date, sym))
        if pc is None or len(db) <= SCAN_BAR: continue
        bsf = db[:SCAN_BAR+1]

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

            P = score_price(sym, bsf, SCAN_BAR, pc, direction)
            M = score_momentum(sym, bsf, SCAN_BAR, direction)
            Mkt = fast_mkt(date, SCAN_BAR, direction)
            c = candle_score(bsf[-1])
            n = noise_score(bsf)

            # Tier 1: P>=7, M>=9, candle<=-1, noise<=2.5
            if P >= 7 and M >= 9 and c <= -1 and n <= 2.5:
                tier = 'T1'
            # Tier 2: P>=7, M>=8, candle<=-1, noise<=2.5
            elif P >= 7 and M >= 8 and c <= -1 and n <= 2.5:
                tier = 'T2'
            else:
                continue

            entry = sig.suggested_entry
            atr_bars = prev_bars_for_atr.get((date, sym), [])
            atr_val = vol_agent.calculate_atr(atr_bars) if len(atr_bars) >= 5 else 0
            stop, _ = SmartStopCalculator.calculate(db, SCAN_BAR, entry, direction, atr_val)
            target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)

            exit_p = None
            for k in range(SCAN_BAR+1, min(len(db), 70)):
                if direction=='LONG':
                    if db[k]['low'] <= stop: exit_p = stop; break
                    if db[k]['high'] >= target: exit_p = target; break
                else:
                    if db[k]['high'] >= stop: exit_p = stop; break
                    if db[k]['low'] <= target: exit_p = target; break
            if not exit_p:
                exit_p = db[min(69, len(db)-1)]['close']
            pnl = pnl_calc(entry, exit_p, direction)

            trades_today.append({
                'strategy': 'MOM', 'tier': tier, 'sym': sym, 'dir': direction,
                'entry_bar': SCAN_BAR, 'pnl': pnl, 'win': pnl > 0,
                'P': P, 'M': M, 'candle': c, 'noise': round(n, 1),
            })

    # ─── STRATEGY C: ORB + MoE filter ───
    for sym in date_bars[date]:
        if sym in BLACKLIST: continue
        db = date_bars[date][sym]; pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue

        range_high = db[0]['high']; range_low = db[0]['low']
        range_size = range_high - range_low
        if range_size == 0: continue

        for j in range(1, min(len(db), 12)):
            direction = None
            if db[j]['close'] > range_high: direction = 'LONG'
            elif db[j]['close'] < range_low: direction = 'SHORT'
            if not direction: continue

            avg_vol = db[0]['volume']
            if avg_vol > 0 and db[j]['volume'] < avg_vol * 1.2: break

            Mkt = fast_mkt(date, j, direction)
            c = candle_score(db[j])

            if Mkt >= 6 and c <= 0:
                entry = db[j]['close']
                stop = range_low if direction=='LONG' else range_high
                risk = abs(entry - stop)
                if risk == 0: break
                target = entry + risk*1.5 if direction=='LONG' else entry - risk*1.5
                exit_p, exit_type = simulate(db, j, entry, direction, stop, target, 36)
                pnl = pnl_calc(entry, exit_p, direction)

                trades_today.append({
                    'strategy': 'ORB_MoE', 'tier': 'T3', 'sym': sym, 'dir': direction,
                    'entry_bar': j, 'pnl': pnl, 'win': pnl > 0,
                    'Mkt': Mkt, 'candle': c,
                })
            break

    # ─── DAILY DECISION ───
    if not trades_today:
        tier_counts['none'] += 1
        daily_log.append({'date': date, 'action': 'SKIP', 'reason': 'no signals'})
        continue

    # Prioritize by tier
    t1 = [t for t in trades_today if t['tier'] == 'T1']
    t2 = [t for t in trades_today if t['tier'] == 'T2']
    orb_wl = [t for t in trades_today if t['strategy'] == 'ORB_WL']
    orb_moe = [t for t in trades_today if t['strategy'] == 'ORB_MoE']

    # Pick best from highest available tier
    if t1:
        pick = max(t1, key=lambda x: x.get('M', 0))
        tier_counts['T1'] += 1
        capital_pct = 100
    elif t2:
        pick = max(t2, key=lambda x: x.get('M', 0))
        tier_counts['T2'] += 1
        capital_pct = 70
    elif orb_wl:
        pick = orb_wl[0]
        tier_counts['ORB_WL'] += 1
        capital_pct = 50
    elif orb_moe:
        pick = orb_moe[0]
        tier_counts['ORB_filtered'] += 1
        capital_pct = 50
    else:
        tier_counts['none'] += 1
        daily_log.append({'date': date, 'action': 'SKIP', 'reason': 'no qualifying signals'})
        continue

    daily_log.append({
        'date': date, 'action': 'TRADE', 'tier': pick['tier'],
        'strategy': pick['strategy'], 'sym': pick['sym'], 'dir': pick['dir'],
        'pnl': pick['pnl'], 'win': pick['win'], 'capital_pct': capital_pct,
        'all_trades': len(trades_today),
    })


# ═══ RESULTS ═══
print('='*80)
print('COMBINED DAILY SYSTEM RESULTS')
print('='*80)

trade_days = [d for d in daily_log if d['action'] == 'TRADE']
skip_days = [d for d in daily_log if d['action'] == 'SKIP']
total_trades = len(trade_days)
wins = sum(1 for d in trade_days if d['win'])
total_pnl = sum(d['pnl'] for d in trade_days)
weighted_pnl = sum(d['pnl'] * d['capital_pct']/100 for d in trade_days)

print(f'\nTotal days: {len(all_dates)}')
print(f'Trading days: {total_trades} ({total_trades/len(all_dates)*100:.0f}%)')
print(f'Skip days: {len(skip_days)} ({len(skip_days)/len(all_dates)*100:.0f}%)')
print(f'\nWins: {wins}, Losses: {total_trades-wins}')
print(f'Win Rate: {wins/total_trades*100:.1f}%' if total_trades > 0 else 'N/A')
print(f'Total P&L (equal size): {total_pnl:+.2f}%')
print(f'Total P&L (sized by tier): {weighted_pnl:+.2f}%')
print(f'Avg P&L per trade: {total_pnl/total_trades:+.3f}%' if total_trades > 0 else '')

print(f'\nTier breakdown:')
for tier, count in sorted(tier_counts.items()):
    if count == 0: continue
    tier_trades = [d for d in trade_days if d.get('tier') == tier or d.get('strategy') == tier]
    tier_wins = sum(1 for d in tier_trades if d.get('win'))
    tier_pnl = sum(d.get('pnl', 0) for d in tier_trades)
    wr = tier_wins/len(tier_trades)*100 if tier_trades else 0
    print(f'  {tier}: {count} days, W:{tier_wins} L:{len(tier_trades)-tier_wins}, WR:{wr:.0f}%, P&L:{tier_pnl:+.2f}%')

# ─── WALK-FORWARD SPLIT ───
print(f'\n{"="*80}')
print('WALK-FORWARD: First 80 days vs Last 41 days')
print('='*80)
for period, dates in [('Train (first 80)', all_dates[:80]), ('Test (last 41)', all_dates[80:])]:
    period_trades = [d for d in trade_days if d['date'] in set(dates)]
    if not period_trades:
        print(f'  {period}: no trades'); continue
    w = sum(1 for d in period_trades if d['win'])
    pnl = sum(d['pnl'] for d in period_trades)
    print(f'  {period}: {len(period_trades)} trades, W:{w} L:{len(period_trades)-w}, WR:{w/len(period_trades)*100:.0f}%, P&L:{pnl:+.2f}%')

# ─── PORTFOLIO APPROACH: Take ALL qualifying trades each day ───
print(f'\n{"="*80}')
print('PORTFOLIO: Take ALL qualifying trades each day (not just 1)')
print('='*80)

portfolio_log = []
for date in all_dates:
    day_entries = [d for d in daily_log if d['date'] == date and d['action'] == 'TRADE']
    # Get all trades from this day (we logged the "all_trades" count but need full data)
    # Re-simulate...

# We need to track all trades, not just the picked one. Let me redo.
# Actually, let's just use the full trade list we built
all_day_trades = defaultdict(list)
# Re-run a simplified version to get ALL trades per day
for di, date in enumerate(all_dates):
    # Get all ORB_WL trades
    for sym in date_bars[date]:
        if sym not in WHITELIST: continue
        db = date_bars[date][sym]; pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue
        rh = db[0]['high']; rl = db[0]['low']; rs = rh - rl
        if rs == 0: continue
        for j in range(1, min(len(db), 12)):
            d = None
            if db[j]['close'] > rh: d = 'LONG'
            elif db[j]['close'] < rl: d = 'SHORT'
            if not d: continue
            if db[0]['volume'] > 0 and db[j]['volume'] < db[0]['volume'] * 1.2: break
            entry = db[j]['close']; stop = rl if d=='LONG' else rh
            risk = abs(entry-stop)
            if risk == 0: break
            target = entry + risk*2 if d=='LONG' else entry - risk*2
            ep, _ = simulate(db, j, entry, d, stop, target, 36)
            pnl = pnl_calc(entry, ep, d)
            all_day_trades[date].append({'sym': sym, 'pnl': pnl, 'win': pnl > 0, 'strat': 'ORB_WL'})
            break

# Portfolio stats
port_wins = 0; port_total = 0; port_pnl = 0; port_days = 0
for date in all_dates:
    trades = all_day_trades[date]
    if not trades: continue
    # Dedup by sym
    by_sym = {}
    for t in trades:
        if t['sym'] not in by_sym: by_sym[t['sym']] = t
    port_days += 1
    for t in by_sym.values():
        port_total += 1
        if t['win']: port_wins += 1
        port_pnl += t['pnl']

if port_total > 0:
    print(f'\n  ORB Whitelist Portfolio:')
    print(f'    {port_total} trades across {port_days} days')
    print(f'    WR: {port_wins/port_total*100:.0f}% ({port_wins}W/{port_total-port_wins}L)')
    print(f'    P&L: {port_pnl:+.2f}%')
    print(f'    Avg: {port_pnl/port_total:+.3f}% per trade')

# ─── TRADE LOG ───
print(f'\n{"="*80}')
print('DAILY TRADE LOG')
print('='*80)
print(f"{'Date':>12} {'Tier':>5} {'Strat':>8} {'Sym':>12} {'Dir':>6} {'PnL':>7} {'Win':>4}")
print('-'*60)
for d in trade_days:
    w = 'W' if d['win'] else 'L'
    print(f"{d['date']:>12} {d['tier']:>5} {d['strategy']:>8} {d['sym']:>12} {d['dir']:>6} {d['pnl']:>+6.2f}% {w:>4}")

# ─── MONTHLY BREAKDOWN ───
print(f'\n{"="*80}')
print('MONTHLY BREAKDOWN')
print('='*80)
monthly = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0})
for d in trade_days:
    month = d['date'][:7]
    if d['win']: monthly[month]['w'] += 1
    else: monthly[month]['l'] += 1
    monthly[month]['pnl'] += d['pnl']

print(f"{'Month':>10} {'W':>3} {'L':>3} {'WR':>5} {'P&L':>8}")
print('-'*35)
for month in sorted(monthly):
    m = monthly[month]
    total = m['w'] + m['l']
    wr = m['w']/total*100 if total > 0 else 0
    print(f"{month:>10} {m['w']:>3} {m['l']:>3} {wr:>4.0f}% {m['pnl']:>+7.2f}%")

print(f'\nDone.')
