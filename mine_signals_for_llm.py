"""
MINE SIGNALS FOR LLM — Find what DATA helps predict winners.
NOT filters. DATA that the LLM reads and reasons about.

Every mistake = a signal we were missing.
Every pattern = context for the LLM.

Output: ranked list of enrichment signals with predictive power,
plus a "trade profile" that the LLM should receive before deciding.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading 4 years...', flush=True)
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
                    'range': max(b['high'] for b in pdb) - min(b['low'] for b in pdb),
                    'body': abs(pdb[-1]['close'] - pdb[0]['open']),
                    'dir': 'UP' if pdb[-1]['close'] > pdb[0]['open'] else 'DOWN',
                }

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

def sim(db, eb, entry, d, stop, target, exit_bar=69):
    for j in range(eb+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop, j, 'stop'
            if db[j]['high']>=target: return target, j, 'target'
        else:
            if db[j]['high']>=stop: return stop, j, 'stop'
            if db[j]['low']<=target: return target, j, 'target'
    return db[min(exit_bar,len(db)-1)]['close'], min(exit_bar,len(db)-1), 'time'

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

# ═══ GENERATE TRADES WITH MAXIMUM ENRICHMENT ═══
# Use ORB as base signal (simple, clear entry) but compute EVERYTHING about each trade
print('Generating enriched ORB trades across 4 years...', flush=True)

trades = []
for di, date in enumerate(all_dates):
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: dow = -1

    mc = macro.get(date, {})
    vix = mc.get('india_vix', 0)
    us_ret = mc.get('sp500_overnight', 0)
    nifty_prev = mc.get('nifty_prev_return', 0)

    # Market breadth at bar 3 and bar 6
    breadth = {}
    for check_bar in [3, 6]:
        up=0;dn=0;tot=0
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=check_bar: continue
            tot+=1
            mv=(db[check_bar]['close']-db[0]['open'])/db[0]['open']*100
            if mv>0.15: up+=1
            elif mv<-0.15: dn+=1
        breadth[check_bar] = (up/tot*100 if tot else 50, dn/tot*100 if tot else 50, tot)

    # Relative strength
    rs_map = {}
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        if pc is None or len(db)<=3: continue
        rs_map[sym] = (db[3]['close']-db[0]['open'])/db[0]['open']*100
    sorted_rs = sorted(rs_map.items(), key=lambda x:-x[1])
    rs_rank = {s:i+1 for i,(s,_) in enumerate(sorted_rs)}
    rs_total = len(sorted_rs) if sorted_rs else 1

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        pd = prev_day_data.get((date, sym))
        if pc is None or len(db) < 40: continue

        rh=db[0]['high']; rl=db[0]['low']; rs_size=rh-rl
        if rs_size == 0: continue

        # ORB breakout scan
        for j in range(1, min(len(db), 15)):
            d_dir = None
            if db[j]['close'] > rh: d_dir = 'LONG'
            elif db[j]['close'] < rl: d_dir = 'SHORT'
            if not d_dir: continue

            entry = db[j]['close']
            dir_mult = 1 if d_dir == 'LONG' else -1
            stop = rl if d_dir=='LONG' else rh
            risk = abs(entry - stop)
            if risk == 0: break

            # Simulate with R:R 2.5 (our best surviving setup)
            target = entry + risk*2.5 if d_dir=='LONG' else entry - risk*2.5
            ep, exit_bar_actual, exit_type = sim(db, j, entry, d_dir, stop, target, 69)
            trade_pnl = pnl_calc(entry, ep, d_dir)

            # ═══ COMPUTE EVERY POSSIBLE SIGNAL ═══
            bsf = db[:j+1]
            closes = [b['close'] for b in bsf]
            highs = [b['high'] for b in bsf]
            lows = [b['low'] for b in bsf]
            volumes = [b['volume'] for b in bsf]
            n = len(closes)

            # --- Opening bar ---
            bar0 = db[0]
            bar0_body = abs(bar0['close']-bar0['open'])
            bar0_range = bar0['high']-bar0['low']
            bar0_body_pct = bar0_body/bar0_range*100 if bar0_range>0 else 0
            bar0_green = bar0['close'] > bar0['open']
            bar0_aligned = (d_dir=='LONG' and bar0_green) or (d_dir=='SHORT' and not bar0_green)

            # --- Breakout bar ---
            bb = db[j]
            bb_body = abs(bb['close']-bb['open'])
            bb_range = bb['high']-bb['low']
            bb_body_pct = bb_body/bb_range*100 if bb_range>0 else 0
            bb_uw = bb['high']-max(bb['open'],bb['close'])
            bb_lw = min(bb['open'],bb['close'])-bb['low']

            # --- Volume ---
            vol_ratio = db[j]['volume']/(db[0]['volume']+1)
            avg_vol = sum(volumes)/n if n else 1
            vol_spike = db[j]['volume']/avg_vol if avg_vol else 1
            vol_sustain = sum(volumes[n//2:])/sum(volumes[:max(1,n//2)]) if sum(volumes[:max(1,n//2)])>0 else 1

            # --- Gap ---
            gap = (db[0]['open']-pc)/pc*100
            gap_adj = gap * dir_mult
            gap_size = abs(gap)

            # --- Morning move ---
            morning = (entry-db[0]['open'])/db[0]['open']*100
            morning_adj = morning * dir_mult

            # --- VWAP ---
            tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
            cum_vol = sum(volumes)
            vwap = tp_vol/cum_vol if cum_vol else entry
            vwap_adj = (entry-vwap)/vwap*100 * dir_mult

            # --- Noise ---
            if n >= 3:
                ranges_sum = sum(highs[i]-lows[i] for i in range(max(0,n-5),n))
                move_abs = abs(closes[-1]-closes[max(0,n-5)])
                noise = ranges_sum/(move_abs+0.001)
            else: noise = 5

            # --- Candle pattern ---
            candle = 0
            if bb_range > 0:
                br = bb_body/bb_range
                if br > 0.8: candle = 2 if bb['close']>bb['open'] else -2
                elif bb_lw > bb_body*2 and bb_uw < bb_body*0.5: candle = 1
                elif bb_uw > bb_body*2 and bb_lw < bb_body*0.5: candle = -1

            # --- Consecutive bars ---
            consec = 0
            for k in range(j, 0, -1):
                bg = db[k]['close'] > db[k]['open']
                if (d_dir=='LONG' and bg) or (d_dir=='SHORT' and not bg): consec += 1
                else: break

            # --- ATR ---
            atr = sum(highs[i]-lows[i] for i in range(n))/n if n else 0
            atr_pct = atr/entry*100 if entry else 0

            # --- Range extension ---
            range_ext = abs(entry-(rh if d_dir=='LONG' else rl))/rs_size*100

            # --- ORB range as % of price ---
            orb_range_pct = rs_size/pc*100

            # --- Prev day context ---
            if pd:
                pd_range_pct = pd['range']/pd['close']*100 if pd['close'] else 0
                pd_body_pct = pd['body']/pd['range']*100 if pd['range'] else 0
                pd_aligned = (d_dir=='LONG' and pd['dir']=='UP') or (d_dir=='SHORT' and pd['dir']=='DOWN')
                pd_gap_from_close = (db[0]['open']-pd['close'])/pd['close']*100
            else:
                pd_range_pct=0; pd_body_pct=0; pd_aligned=False; pd_gap_from_close=0

            # --- Camarilla levels ---
            if pd:
                h=pd['high'];l=pd['low'];c=pd['close'];rng2=h-l
                r3=c+rng2*1.1/4; s3=c-rng2*1.1/4
                near_r3 = abs(entry-r3)/entry*100
                near_s3 = abs(entry-s3)/entry*100
                near_cam = min(near_r3, near_s3)
                above_r3 = entry > r3
                below_s3 = entry < s3
            else:
                near_cam=99; above_r3=False; below_s3=False

            # --- Market context ---
            up3, dn3, _ = breadth.get(3, (50,50,0))
            up6, dn6, _ = breadth.get(6, (50,50,0))
            breadth_aligned = (d_dir=='LONG' and up3>55) or (d_dir=='SHORT' and dn3>55)
            breadth_against = (d_dir=='LONG' and up3<35) or (d_dir=='SHORT' and dn3<35)

            # --- RS rank ---
            rs_pct = rs_rank.get(sym, rs_total//2)/rs_total*100
            rs_top10 = rs_pct <= 10
            rs_bottom10 = rs_pct >= 90

            # --- Time ---
            breakout_bar = j

            # --- Sector ---
            from app.signals.base import get_sector
            sector = get_sector(sym)

            # --- Post-trade (for learning only, not usable live) ---
            mfe = 0; mae = 0
            for k in range(j+1, min(len(db), 70)):
                if d_dir=='LONG':
                    mfe = max(mfe, (db[k]['high']-entry)/entry*100)
                    mae = max(mae, (entry-db[k]['low'])/entry*100)
                else:
                    mfe = max(mfe, (entry-db[k]['low'])/entry*100)
                    mae = max(mae, (db[k]['high']-entry)/entry*100)

            trades.append({
                'date':date, 'sym':sym, 'dir':d_dir, 'sector':sector,
                'pnl':round(trade_pnl,4), 'win': 1 if trade_pnl>0 else 0,
                'exit_type':exit_type,
                # Pre-trade signals (usable by LLM)
                'bar0_body_pct':round(bar0_body_pct,1), 'bar0_aligned': 1 if bar0_aligned else 0,
                'bb_body_pct':round(bb_body_pct,1), 'breakout_bar':j,
                'vol_ratio':round(vol_ratio,2), 'vol_spike':round(vol_spike,2), 'vol_sustain':round(vol_sustain,2),
                'gap_adj':round(gap_adj,3), 'gap_size':round(gap_size,3),
                'morning_adj':round(morning_adj,3), 'vwap_adj':round(vwap_adj,3),
                'noise':round(noise,2), 'candle':candle, 'consec':consec,
                'atr_pct':round(atr_pct,3), 'range_ext':round(range_ext,1),
                'orb_range_pct':round(orb_range_pct,3),
                'pd_range_pct':round(pd_range_pct,2), 'pd_body_pct':round(pd_body_pct,1),
                'pd_aligned':1 if pd_aligned else 0, 'pd_gap':round(pd_gap_from_close,3),
                'near_cam':round(near_cam,3), 'above_r3':1 if above_r3 else 0, 'below_s3':1 if below_s3 else 0,
                'breadth_up':round(up3,0), 'breadth_aligned':1 if breadth_aligned else 0,
                'breadth_against':1 if breadth_against else 0,
                'rs_pct':round(rs_pct,1), 'rs_top10':1 if rs_top10 else 0,
                'dow':dow, 'vix':round(vix,1), 'us_ret':round(us_ret,2),
                # Post-trade (learning only)
                'mfe':round(mfe,3), 'mae':round(mae,3),
            })
            break

    if (di+1) % 200 == 0:
        print(f'  {di+1}/{len(all_dates)} | {len(trades)} trades', flush=True)

print(f'\nTotal: {len(trades)} trades')
winners = [t for t in trades if t['win']]
losers = [t for t in trades if not t['win']]
print(f'Winners: {len(winners)} ({len(winners)/len(trades)*100:.0f}%) | Losers: {len(losers)}')

# ═══ RANK ALL SIGNALS BY PREDICTIVE POWER ═══
print(f'\n{"="*80}')
print('SIGNAL RANKING: Which data most separates winners from losers?')
print('="*80')

skip = {'date','sym','dir','pnl','win','exit_type','sector','mfe','mae'}
signals = [k for k in trades[0].keys() if k not in skip]

ranked = []
for sig in signals:
    w_vals = [t[sig] for t in winners if isinstance(t[sig], (int, float))]
    l_vals = [t[sig] for t in losers if isinstance(t[sig], (int, float))]
    if not w_vals or not l_vals: continue
    w_mean = sum(w_vals)/len(w_vals)
    l_mean = sum(l_vals)/len(l_vals)
    delta = w_mean - l_mean
    all_vals = w_vals + l_vals
    rng = max(all_vals) - min(all_vals)
    # Normalized predictive power (0-100)
    power = abs(delta/rng*100) if rng > 0 else 0
    # Direction: positive means higher value = more likely to win
    direction = 'HIGHER=WIN' if delta > 0 else 'LOWER=WIN'
    ranked.append({'signal':sig, 'power':round(power,1), 'w_mean':round(w_mean,3),
                   'l_mean':round(l_mean,3), 'delta':round(delta,3), 'direction':direction})

ranked.sort(key=lambda x: -x['power'])
print(f"\n{'Rank':>4} {'Signal':>20} {'Power':>6} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Direction':>15}")
print('-'*85)
for i, r in enumerate(ranked, 1):
    marker = ' <<<' if r['power'] >= 5 else ''
    print(f"  {i:>3}. {r['signal']:>20} {r['power']:>5.1f}% {r['w_mean']:>10.3f} {r['l_mean']:>10.3f} {r['delta']:>+10.3f} {r['direction']:>15}{marker}")

# ═══ MISTAKE ANALYSIS: What pattern do losses share? ═══
print(f'\n{"="*80}')
print('MISTAKE PATTERNS: Common traits of losing trades')
print('="*80')

# For each signal, find the "danger zone" where WR drops below 40%
print('\nDANGER ZONES (WR < 40% in this range):')
for r in ranked[:20]:
    sig = r['signal']
    vals = sorted(set(t[sig] for t in trades if isinstance(t[sig], (int, float))))
    if len(vals) < 5: continue
    n_bins = 5
    for q in range(n_bins):
        lo = vals[int(len(vals)*q/n_bins)]
        hi = vals[min(int(len(vals)*(q+1)/n_bins), len(vals)-1)]
        bucket = [t for t in trades if lo <= t[sig] <= hi]
        if len(bucket) < 20: continue
        w = sum(t['win'] for t in bucket)
        wr = w/len(bucket)*100
        if wr < 40:
            print(f'  {sig} in [{lo:.2f} - {hi:.2f}]: WR={wr:.0f}% ({len(bucket)} trades) -- DANGER')

# ═══ WHAT WINNERS LOOK LIKE vs LOSERS ═══
print(f'\n{"="*80}')
print('IDEAL TRADE PROFILE (top 10% of winners vs bottom 10% of losers)')
print('='*80)

sorted_by_pnl = sorted(trades, key=lambda t: -t['pnl'])
top_winners = sorted_by_pnl[:len(sorted_by_pnl)//10]
worst_losers = sorted_by_pnl[-(len(sorted_by_pnl)//10):]

print(f'\n{"Signal":>20} {"Top Winners":>12} {"Worst Losers":>12} {"What LLM should look for":>35}')
print('-'*85)
for r in ranked[:15]:
    sig = r['signal']
    tw = [t[sig] for t in top_winners if isinstance(t[sig], (int, float))]
    wl = [t[sig] for t in worst_losers if isinstance(t[sig], (int, float))]
    if not tw or not wl: continue
    tw_mean = sum(tw)/len(tw)
    wl_mean = sum(wl)/len(wl)
    # Generate human-readable insight
    if tw_mean > wl_mean:
        insight = f'{sig} > {(tw_mean+wl_mean)/2:.2f} is better'
    else:
        insight = f'{sig} < {(tw_mean+wl_mean)/2:.2f} is better'
    print(f"  {sig:>20} {tw_mean:>12.3f} {wl_mean:>12.3f} {insight:>35}")

# ═══ GENERATE LLM PROMPT SIGNALS ═══
print(f'\n{"="*80}')
print('ENRICHMENT SIGNALS FOR LLM (ranked by importance)')
print('='*80)

print('\nThese should be passed to the LLM as context before each trade decision:')
print()
for i, r in enumerate(ranked[:25], 1):
    sig = r['signal']
    if r['direction'] == 'HIGHER=WIN':
        desc = f"Higher {sig} = higher chance of winning"
    else:
        desc = f"Lower {sig} = higher chance of winning"
    print(f"{i:>2}. {sig:>20}: {desc} (power={r['power']:.1f}%)")

# ═══ EXIT TYPE ANALYSIS — the biggest mistake ═══
print(f'\n{"="*80}')
print('EXIT TYPE ANALYSIS')
print('='*80)
for et in ['stop','target','time']:
    sub = [t for t in trades if t['exit_type']==et]
    if not sub: continue
    w = sum(t['win'] for t in sub)
    wr = w/len(sub)*100
    avg = sum(t['pnl'] for t in sub)/len(sub)
    print(f'  {et:>8}: {len(sub)} trades ({len(sub)/len(trades)*100:.0f}%), WR={wr:.0f}%, avg={avg:+.3f}%')

# How many losers had MFE > 0.2% (price went in our favor then reversed)?
saveable = [t for t in losers if t['mfe'] > 0.2]
print(f'\nLosers where price went +0.2% in our favor before stopping out: {len(saveable)}/{len(losers)} ({len(saveable)/len(losers)*100:.0f}%)')
print(f'  A break-even stop after +0.2% would save these trades')
print(f'  Potential P&L improvement: {sum(abs(t["pnl"]) for t in saveable):+.1f}%')

# ═══ SAVE ENRICHMENT CONFIG ═══
enrichment = {
    'ranked_signals': ranked[:25],
    'danger_zones': [],
    'ideal_profile': {},
}
for r in ranked[:15]:
    sig = r['signal']
    tw = [t[sig] for t in top_winners if isinstance(t[sig], (int, float))]
    wl = [t[sig] for t in worst_losers if isinstance(t[sig], (int, float))]
    if tw and wl:
        enrichment['ideal_profile'][sig] = {
            'winner_avg': round(sum(tw)/len(tw), 3),
            'loser_avg': round(sum(wl)/len(wl), 3),
            'threshold': round((sum(tw)/len(tw) + sum(wl)/len(wl))/2, 3),
            'direction': r['direction'],
        }

with open('data/llm_enrichment_signals.json', 'w') as f:
    json.dump(enrichment, f, indent=2)
print(f'\nSaved enrichment config to data/llm_enrichment_signals.json')
print(f'LLM should receive these {len(enrichment["ranked_signals"])} signals as context for every trade decision.')
