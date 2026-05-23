"""
LOSS ANALYSIS — Find what losing trades have in common.
If we can identify a pattern in losses, we can filter them out → push toward 100%.

For each top strategy, extract ALL losing trades, compute indicators,
find what separates losers from winners.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.agents.coded_moe import score_volume, score_price, score_momentum
from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer

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
                prev_day_data[(d, sym)] = {
                    'high': max(b['high'] for b in pdb), 'low': min(b['low'] for b in pdb),
                    'close': pdb[-1]['close'], 'open': pdb[0]['open'],
                }

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

sector_cache = {}; market_cache = {}
for sb in [3, 6]:
    for date in all_dates:
        sector_cache[(date,sb)] = SectorAnalyzer.compute(all_data, date, sb)
        up=0;dn=0;tot=0
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=sb: continue
            tot+=1
            mv=(db[sb]['close']-db[0]['open'])/db[0]['open']*100
            if mv>0.15: up+=1
            elif mv<-0.15: dn+=1
        market_cache[(date,sb)] = (up,dn,tot)

def fast_mkt(date, sb, d):
    s=5.0; sb2=min([3,6],key=lambda x:abs(x-sb))
    up,dn,tot=market_cache.get((date,sb2),(0,0,0))
    if tot==0: return 5.0
    up_p=up/tot*100; dn_p=dn/tot*100
    if d=="LONG" and up_p>65: s+=2
    elif d=="LONG" and up_p>55: s+=1
    elif d=="LONG" and up_p<35: s-=2
    elif d=="SHORT" and dn_p>65: s+=2
    elif d=="SHORT" and dn_p>55: s+=1
    elif d=="SHORT" and dn_p<35: s-=2
    return max(0,min(10,s))

WHITELIST = {'ULTRACEMCO','EICHERMOT','M&M','TATASTEEL','TECHM','SBILIFE','NTPC',
             'ADANIPORTS','ICICIBANK','SUNPHARMA','TITAN','WIPRO','DIVISLAB','COALINDIA'}

def simulate(db, eb_start, entry, d, stop, target, exit_bar=69):
    for j in range(eb_start+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop, j, 'stop'
            if db[j]['high']>=target: return target, j, 'target'
        else:
            if db[j]['high']>=stop: return stop, j, 'stop'
            if db[j]['low']<=target: return target, j, 'target'
    return db[min(exit_bar,len(db)-1)]['close'], min(exit_bar,len(db)-1), 'time'

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

def candle_score(bar):
    body=abs(bar['close']-bar['open']); rng=bar['high']-bar['low']
    if rng==0: return 0
    br=body/rng; uw=bar['high']-max(bar['open'],bar['close']); lw=min(bar['open'],bar['close'])-bar['low']
    if br>0.8: return 2 if bar['close']>bar['open'] else -2
    if lw>body*2 and uw<body*0.5: return 1
    if uw>body*2 and lw<body*0.5: return -1
    return 0

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')
t0 = time.time()

# ═══════════════════════════════════════════════════════════════
# Generate ORB whitelist trades (our best: 70% WR, 54 days)
# with FULL detail on each trade
# ═══════════════════════════════════════════════════════════════
print('='*80)
print('ANALYZING: ORB + Whitelist (vol confirmed) — 70% WR setup')
print('='*80, flush=True)

orb_trades = []
for date in all_dates:
    mc = macro.get(date, {})
    vix = mc.get('india_vix', 0)
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: dow = -1

    # RS
    rs_map = {}
    for sym in date_bars[date]:
        db = date_bars[date][sym]; pc = prev_close_map.get((date,sym))
        if pc is None or len(db) <= 3: continue
        rs_map[sym] = (db[3]['close']-db[0]['open'])/db[0]['open']*100
    sorted_rs = sorted(rs_map.items(), key=lambda x:-x[1])
    rs_rank = {s:i+1 for i,(s,_) in enumerate(sorted_rs)}
    rs_total = len(sorted_rs) if sorted_rs else 1

    # Market breadth at bar 3
    up_3=0; dn_3=0; tot_3=0
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) <= 3: continue
        tot_3 += 1
        mv = (db[3]['close']-db[0]['open'])/db[0]['open']*100
        if mv > 0.15: up_3 += 1
        elif mv < -0.15: dn_3 += 1
    breadth_pct = up_3/tot_3*100 if tot_3 > 0 else 50

    for sym in WHITELIST:
        if sym not in date_bars[date]: continue
        db = date_bars[date][sym]
        pc = prev_close_map.get((date,sym))
        pd = prev_day_data.get((date,sym))
        if pc is None or len(db) < 40: continue

        rh = db[0]['high']; rl = db[0]['low']; rs_size = rh - rl
        if rs_size == 0: continue

        for j in range(1, min(len(db), 12)):
            d = None
            if db[j]['close'] > rh: d = 'LONG'
            elif db[j]['close'] < rl: d = 'SHORT'
            if not d: continue

            # Volume check
            if db[0]['volume'] > 0 and db[j]['volume'] < db[0]['volume'] * 1.2: break

            entry = db[j]['close']
            stop = rl if d=='LONG' else rh
            risk = abs(entry - stop)
            if risk == 0: break
            target = entry + risk*2.0 if d=='LONG' else entry - risk*2.0

            ep, exit_bar_actual, exit_type = simulate(db, j, entry, d, stop, target, 36)
            pnl = pnl_calc(entry, ep, d)

            # Compute EVERYTHING about this trade
            bsf = db[:j+1]
            closes = [b['close'] for b in bsf]
            dir_mult = 1 if d == 'LONG' else -1
            gap = (db[0]['open'] - pc) / pc * 100
            morning_move = (entry - db[0]['open']) / db[0]['open'] * 100

            # Opening bar analysis
            bar0_body = abs(db[0]['close']-db[0]['open'])
            bar0_range = db[0]['high']-db[0]['low']
            bar0_body_pct = bar0_body/bar0_range*100 if bar0_range > 0 else 0
            bar0_dir = 'green' if db[0]['close'] > db[0]['open'] else 'red'
            bar0_aligned = (d=='LONG' and bar0_dir=='green') or (d=='SHORT' and bar0_dir=='red')

            # Breakout bar analysis
            bb_body = abs(db[j]['close']-db[j]['open'])
            bb_range = db[j]['high']-db[j]['low']
            bb_body_pct = bb_body/bb_range*100 if bb_range > 0 else 0

            # Volume analysis
            avg_vol_5d = sum(b['volume'] for b in db[:max(1,j)])/max(1,j)
            vol_ratio = db[j]['volume']/avg_vol_5d if avg_vol_5d > 0 else 1

            # Noise
            if len(bsf) >= 3:
                ranges_sum = sum(b['high']-b['low'] for b in bsf[-5:])
                move_abs = abs(closes[-1]-closes[max(0,len(closes)-5)])
                noise = ranges_sum/(move_abs+0.001)
            else: noise = 5

            # Candle
            c = candle_score(db[j])

            # MoE scores
            Mkt = fast_mkt(date, j, d)
            M = score_momentum(sym, bsf, j, d) if j >= 2 else 5
            P = score_price(sym, bsf, j, pc, d) if j >= 2 else 5
            V = score_volume(sym, bsf, all_data, date, j)

            # Sector
            sector = get_sector(sym)
            sec_data = sector_cache.get((date, 3), {}).get(sector, {})
            sec_leader_chg = sec_data.get('leader_change_pct', 0)
            sec_aligned = (d=='LONG' and sec_leader_chg > 0) or (d=='SHORT' and sec_leader_chg < 0)

            # Previous day
            if pd:
                prev_range_pct = (pd['high']-pd['low'])/pd['close']*100
                prev_dir = 'up' if pd['close'] > pd['open'] else 'down'
                prev_aligned = (d=='LONG' and prev_dir=='up') or (d=='SHORT' and prev_dir=='down')
            else:
                prev_range_pct = 0; prev_aligned = False

            # How far above/below range at entry
            range_extension = abs(entry - (rh if d=='LONG' else rl)) / rs_size * 100

            # Bar count before breakout
            bars_to_breakout = j

            # After entry: what happened?
            # Max favorable excursion (MFE) and max adverse excursion (MAE)
            mfe = 0; mae = 0
            for k in range(j+1, min(len(db), 37)):
                if d == 'LONG':
                    excursion = (db[k]['high'] - entry) / entry * 100
                    adverse = (entry - db[k]['low']) / entry * 100
                else:
                    excursion = (entry - db[k]['low']) / entry * 100
                    adverse = (db[k]['high'] - entry) / entry * 100
                mfe = max(mfe, excursion)
                mae = max(mae, adverse)

            orb_trades.append({
                'date': date, 'sym': sym, 'dir': d,
                'pnl': round(pnl, 4), 'win': pnl > 0,
                'exit_type': exit_type,
                # Entry context
                'gap': round(gap, 3), 'gap_adj': round(gap*dir_mult, 3),
                'morning_adj': round(morning_move*dir_mult, 3),
                'bar0_body_pct': round(bar0_body_pct, 1),
                'bar0_aligned': bar0_aligned,
                'bb_body_pct': round(bb_body_pct, 1),
                'breakout_bar': j, 'range_ext': round(range_extension, 1),
                'vol_ratio': round(vol_ratio, 2),
                'candle': c, 'noise': round(noise, 1),
                # MoE
                'M': M, 'P': P, 'V': V, 'Mkt': Mkt,
                # Context
                'rs_pct': round(rs_rank.get(sym, rs_total//2)/rs_total*100, 1),
                'breadth': round(breadth_pct, 0),
                'sec_aligned': sec_aligned,
                'prev_aligned': prev_aligned,
                'prev_range': round(prev_range_pct, 2) if pd else 0,
                'vix': round(vix, 1), 'dow': dow,
                'sector': sector,
                # Post-entry
                'mfe': round(mfe, 3), 'mae': round(mae, 3),
            })
            break

# ─── ANALYSIS ───
winners = [t for t in orb_trades if t['win']]
losers = [t for t in orb_trades if not t['win']]

print(f'\nTotal: {len(orb_trades)} trades | W:{len(winners)} L:{len(losers)} | WR:{len(winners)/len(orb_trades)*100:.0f}%')

print(f'\n{"="*80}')
print('WINNER vs LOSER PROFILE')
print('='*80)

indicators = ['gap_adj','morning_adj','bar0_body_pct','bb_body_pct','breakout_bar',
              'range_ext','vol_ratio','candle','noise','M','P','V','Mkt',
              'rs_pct','breadth','prev_range','vix','mfe','mae']

print(f"\n{'Indicator':>15} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Signal':>8}")
print('-'*60)
for ind in indicators:
    w_vals = [t[ind] for t in winners if isinstance(t[ind], (int, float))]
    l_vals = [t[ind] for t in losers if isinstance(t[ind], (int, float))]
    if not w_vals or not l_vals: continue
    w_mean = sum(w_vals)/len(w_vals)
    l_mean = sum(l_vals)/len(l_vals)
    delta = w_mean - l_mean
    # Is it significant?
    all_vals = w_vals + l_vals
    rng = max(all_vals) - min(all_vals) if all_vals else 1
    edge = delta / rng * 100 if rng > 0 else 0
    signal = '<<<' if abs(edge) > 10 else ('<<' if abs(edge) > 5 else '')
    print(f"  {ind:>15} {w_mean:>10.2f} {l_mean:>10.2f} {delta:>+10.3f} {signal:>8}")

# Boolean indicators
bool_inds = ['bar0_aligned', 'sec_aligned', 'prev_aligned']
print(f"\n{'Bool Indicator':>15} {'Win %True':>10} {'Loss %True':>10}")
print('-'*40)
for ind in bool_inds:
    w_true = sum(1 for t in winners if t[ind]) / len(winners) * 100
    l_true = sum(1 for t in losers if t[ind]) / len(losers) * 100
    signal = '<<<' if abs(w_true - l_true) > 15 else ''
    print(f"  {ind:>15} {w_true:>9.0f}% {l_true:>9.0f}% {signal}")

# ─── LOSS DEEP DIVE ───
print(f'\n{"="*80}')
print('EVERY LOSING TRADE — DETAILED')
print('='*80)
print(f"{'Date':>12} {'Sym':>12} {'Dir':>6} {'PnL':>7} {'Exit':>6} | {'Gap':>5} {'Morn':>5} {'B0%':>4} {'B0A':>3} {'Vol':>5} {'C':>2} {'N':>4} | {'M':>3} {'P':>3} {'Mkt':>3} {'RS%':>4} {'Sec':>3} {'Brd':>3}")
print('-'*115)
for t in sorted(losers, key=lambda x: x['pnl']):
    b0a = 'Y' if t['bar0_aligned'] else 'N'
    sec = 'Y' if t['sec_aligned'] else 'N'
    print(f"  {t['date']:>12} {t['sym']:>12} {t['dir']:>6} {t['pnl']:>+6.2f}% {t['exit_type']:>6} | {t['gap_adj']:>+4.1f} {t['morning_adj']:>+4.1f} {t['bar0_body_pct']:>4.0f} {b0a:>3} {t['vol_ratio']:>5.1f} {t['candle']:>2} {t['noise']:>4.1f} | {t['M']:>3.0f} {t['P']:>3.0f} {t['Mkt']:>3.0f} {t['rs_pct']:>4.0f} {sec:>3} {t['breadth']:>3.0f}")

# ─── CAN WE FILTER OUT LOSERS? ───
print(f'\n{"="*80}')
print('LOSS ELIMINATION: Test filters that remove losers but keep winners')
print('='*80)

# Try every indicator threshold
best_filters = []
for ind in indicators:
    vals = sorted(set(t[ind] for t in orb_trades if isinstance(t[ind], (int, float))))
    if len(vals) < 3: continue

    for percentile in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        idx = int(len(vals) * percentile)
        thresh = vals[min(idx, len(vals)-1)]

        # Try >= threshold
        filtered = [t for t in orb_trades if t[ind] >= thresh]
        if len(filtered) >= 5:
            w = sum(1 for t in filtered if t['win'])
            wr = w/len(filtered)*100
            removed_losers = len(losers) - sum(1 for t in filtered if not t['win'])
            removed_winners = len(winners) - sum(1 for t in filtered if t['win'])
            if wr > 70 and removed_winners <= 3:  # Don't remove too many winners
                best_filters.append({
                    'filter': f'{ind}>={thresh:.2f}', 'n': len(filtered), 'wr': wr,
                    'removed_L': removed_losers, 'removed_W': removed_winners,
                    'net': removed_losers - removed_winners,
                })

        # Try <= threshold
        filtered = [t for t in orb_trades if t[ind] <= thresh]
        if len(filtered) >= 5:
            w = sum(1 for t in filtered if t['win'])
            wr = w/len(filtered)*100
            removed_losers = len(losers) - sum(1 for t in filtered if not t['win'])
            removed_winners = len(winners) - sum(1 for t in filtered if t['win'])
            if wr > 70 and removed_winners <= 3:
                best_filters.append({
                    'filter': f'{ind}<={thresh:.2f}', 'n': len(filtered), 'wr': wr,
                    'removed_L': removed_losers, 'removed_W': removed_winners,
                    'net': removed_losers - removed_winners,
                })

best_filters.sort(key=lambda x: (-x['wr'], -x['n']))
print(f"\n{'Filter':>25} {'N':>4} {'WR':>5} {'Removed L':>10} {'Removed W':>10} {'Net':>5}")
print('-'*65)
for f in best_filters[:30]:
    print(f"  {f['filter']:>25} {f['n']:>4} {f['wr']:>4.0f}% {f['removed_L']:>10} {f['removed_W']:>10} {f['net']:>+5}")

# ─── COMBO FILTERS ───
print(f'\n{"="*80}')
print('2-FILTER COMBOS to eliminate more losses')
print('='*80)

combo_filters = []
# Use top single filters as building blocks
top_singles = best_filters[:15]
for i in range(len(top_singles)):
    for j in range(i+1, len(top_singles)):
        f1 = top_singles[i]['filter']
        f2 = top_singles[j]['filter']

        # Parse filters
        def parse_filter(fstr):
            if '>=' in fstr:
                parts = fstr.split('>=')
                return parts[0], '>=', float(parts[1])
            else:
                parts = fstr.split('<=')
                return parts[0], '<=', float(parts[1])

        ind1, op1, val1 = parse_filter(f1)
        ind2, op2, val2 = parse_filter(f2)
        if ind1 == ind2: continue  # Same indicator

        filtered = []
        for t in orb_trades:
            v1 = t.get(ind1)
            v2 = t.get(ind2)
            if v1 is None or v2 is None: continue
            pass1 = (v1 >= val1) if op1 == '>=' else (v1 <= val1)
            pass2 = (v2 >= val2) if op2 == '>=' else (v2 <= val2)
            if pass1 and pass2:
                filtered.append(t)

        if len(filtered) < 5: continue
        w = sum(1 for t in filtered if t['win'])
        wr = w/len(filtered)*100
        if wr <= 70: continue
        removed_l = len(losers) - sum(1 for t in filtered if not t['win'])
        removed_w = len(winners) - sum(1 for t in filtered if t['win'])
        combo_filters.append({
            'filter': f'{f1} + {f2}', 'n': len(filtered), 'wr': wr,
            'removed_L': removed_l, 'removed_W': removed_w,
            'net': removed_l - removed_w,
        })

combo_filters.sort(key=lambda x: (-x['wr'], -x['n']))
print(f"\n{'Filter Combo':>55} {'N':>4} {'WR':>5} {'-L':>4} {'-W':>4} {'Net':>4}")
print('-'*80)
for f in combo_filters[:25]:
    marker = ' ***' if f['wr'] >= 90 else (' <<' if f['wr'] >= 80 else '')
    print(f"  {f['filter']:>55} {f['n']:>4} {f['wr']:>4.0f}% {f['removed_L']:>4} {f['removed_W']:>4} {f['net']:>+4}{marker}")


# ─── DAY-OF-WEEK ANALYSIS ───
print(f'\n{"="*80}')
print('DAY-OF-WEEK ANALYSIS')
print('='*80)
dow_names = ['Mon','Tue','Wed','Thu','Fri']
for d in range(5):
    sub = [t for t in orb_trades if t['dow'] == d]
    if not sub: continue
    w = sum(1 for t in sub if t['win'])
    wr = w/len(sub)*100
    print(f'  {dow_names[d]}: {len(sub)} trades, WR={wr:.0f}% ({w}W/{len(sub)-w}L)')


# ─── SECTOR ANALYSIS ───
print(f'\n{"="*80}')
print('SECTOR ANALYSIS')
print('='*80)
for sector in sorted(set(t['sector'] for t in orb_trades)):
    sub = [t for t in orb_trades if t['sector'] == sector]
    if not sub: continue
    w = sum(1 for t in sub if t['win'])
    wr = w/len(sub)*100
    print(f'  {sector:>12}: {len(sub)} trades, WR={wr:.0f}%')


# ─── PER-STOCK LOSS ANALYSIS ───
print(f'\n{"="*80}')
print('PER-STOCK ANALYSIS (whitelist only)')
print('='*80)
for sym in sorted(WHITELIST):
    sub = [t for t in orb_trades if t['sym'] == sym]
    if not sub: continue
    w = sum(1 for t in sub if t['win'])
    wr = w/len(sub)*100
    losses = [t for t in sub if not t['win']]
    loss_dates = [t['date'] for t in losses]
    print(f'  {sym:>12}: {len(sub)} trades, WR={wr:.0f}% | Losses: {", ".join(loss_dates[:5])}')


# ─── EXIT TYPE ANALYSIS ───
print(f'\n{"="*80}')
print('EXIT TYPE ANALYSIS')
print('='*80)
for et in ['stop', 'target', 'time']:
    sub = [t for t in orb_trades if t['exit_type'] == et]
    if not sub: continue
    w = sum(1 for t in sub if t['win'])
    wr = w/len(sub)*100
    avg_pnl = sum(t['pnl'] for t in sub)/len(sub)
    print(f'  {et:>8}: {len(sub)} trades, WR={wr:.0f}%, avg P&L={avg_pnl:+.3f}%')


# ─── MFE/MAE ANALYSIS ───
print(f'\n{"="*80}')
print('MFE/MAE ANALYSIS (how far did price go for/against?)')
print('='*80)
w_mfe = sum(t['mfe'] for t in winners)/len(winners)
w_mae = sum(t['mae'] for t in winners)/len(winners)
l_mfe = sum(t['mfe'] for t in losers)/len(losers)
l_mae = sum(t['mae'] for t in losers)/len(losers)
print(f'  Winners: avg MFE={w_mfe:.3f}% (went in our favor), avg MAE={w_mae:.3f}% (went against)')
print(f'  Losers:  avg MFE={l_mfe:.3f}% (went in our favor), avg MAE={l_mae:.3f}% (went against)')
print(f'\n  Insight: Losers had MFE={l_mfe:.3f}% — price DID move in our favor {l_mfe:.3f}% before reversing!')
if l_mfe > 0.1:
    print(f'  → A break-even stop after {l_mfe:.2f}% gain would have saved {sum(1 for t in losers if t["mfe"]>0.1)} of {len(losers)} losses')


print(f'\nTime: {time.time()-t0:.1f}s')
