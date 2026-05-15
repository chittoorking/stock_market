"""Find what ALL target-hit winners share that NO stop-loss loser has."""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from app.agents.data_providers import SectorAnalyzer
from app.agents.candle_patterns import detect_patterns
from app.signals.base import get_sector

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

targets = [
    ('TCS', '2025-11-19', 6, 'LONG'), ('HINDUNILVR', '2025-11-19', 6, 'LONG'),
    ('HINDALCO', '2025-11-21', 6, 'SHORT'), ('TECHM', '2025-11-24', 6, 'LONG'),
    ('SBIN', '2025-11-25', 6, 'LONG'), ('ADANIENT', '2025-11-28', 6, 'LONG'),
    ('M&M', '2025-11-28', 6, 'LONG'),
    ('HCLTECH', '2025-12-04', 6, 'LONG'), ('HCLTECH', '2025-12-05', 6, 'LONG'),
    ('INFY', '2025-12-05', 6, 'LONG'), ('DIVISLAB', '2025-12-10', 6, 'LONG'),
    ('HINDALCO', '2025-12-10', 6, 'LONG'), ('DIVISLAB', '2025-12-11', 6, 'LONG'),
    ('TATASTEEL', '2025-12-12', 6, 'LONG'),
    ('BHARTIARTL', '2025-12-22', 6, 'LONG'), ('UPL', '2025-12-22', 6, 'LONG'),
    ('GRASIM', '2025-12-23', 6, 'LONG'),
]

stop_losses = [
    ('LT', '2025-11-18', 15, 'SHORT'), ('POWERGRID', '2025-11-26', 6, 'LONG'),
    ('ADANIPORTS', '2025-12-01', 6, 'LONG'), ('MARUTI', '2025-12-01', 6, 'LONG'),
    ('HEROMOTOCO', '2025-12-01', 6, 'LONG'), ('EICHERMOT', '2025-12-03', 6, 'LONG'),
    ('BPCL', '2025-12-08', 6, 'SHORT'), ('BRITANNIA', '2025-12-09', 6, 'SHORT'),
    ('ASIANPAINT', '2025-12-11', 6, 'LONG'), ('HINDALCO', '2025-12-11', 6, 'LONG'),
    ('NESTLEIND', '2025-12-16', 6, 'LONG'), ('INDUSINDBK', '2025-12-17', 6, 'SHORT'),
    ('TATAMOTORS', '2025-12-19', 6, 'LONG'), ('LT', '2025-12-23', 6, 'LONG'),
    ('TECHM', '2025-12-29', 6, 'LONG'),
]

def compute(sym, date, bar, direction):
    sb = [b for b in all_data.get(sym, []) if b['timestamp'][:10] == date]
    pb = [b for b in all_data.get(sym, []) if b['timestamp'][:10] < date]
    if not sb or not pb or len(sb) <= bar: return None

    pc = pb[-1]['close']
    eb = sb[bar]; entry = eb['close']
    gap = (sb[0]['open'] - pc) / pc * 100
    gap_aligned = (direction == 'LONG' and gap > 0) or (direction == 'SHORT' and gap < 0)

    eb_body = abs(eb['close'] - eb['open'])
    eb_range = eb['high'] - eb['low']
    eb_body_pct = eb_body / eb_range * 100 if eb_range > 0 else 0
    entry_bar_with = (direction == 'LONG' and eb['close'] > eb['open']) or (direction == 'SHORT' and eb['close'] < eb['open'])

    prev_vol = sb[bar-1]['volume'] if bar > 0 else 1
    vol_spike = (eb['volume'] - prev_vol) / prev_vol * 100 if prev_vol > 0 else 0

    sec = get_sector(sym)
    sec_data = SectorAnalyzer.compute(all_data, date, bar)
    sec_info = sec_data.get(sec, {})
    strength = sec_info.get('strength', 0)
    leader_chg = sec_info.get('leader_change_pct', 0)
    leader_aligned = (direction == 'LONG' and leader_chg > 0.2) or (direction == 'SHORT' and leader_chg < -0.2)

    with_count = sum(1 for i in range(max(0,bar-5), bar+1)
                     if (direction=='LONG' and sb[i]['close']>sb[i]['open']) or
                        (direction=='SHORT' and sb[i]['close']<sb[i]['open']))
    momentum_pct = with_count / min(6, bar+1)

    pats = detect_patterns(sb, bar)
    against = sum(1 for p in pats if (direction=='LONG' and p.direction=='bearish') or (direction=='SHORT' and p.direction=='bullish'))

    morning_move = (entry - sb[0]['open']) / sb[0]['open'] * 100
    morning_aligned = (direction == 'LONG' and morning_move > 0) or (direction == 'SHORT' and morning_move < 0)

    bar0_quality = abs(sb[0]['close'] - sb[0]['open']) / (sb[0]['high'] - sb[0]['low']) * 100 if (sb[0]['high'] - sb[0]['low']) > 0 else 0

    return {
        'sym': sym, 'date': date, 'direction': direction,
        'gap_aligned': gap_aligned, 'eb_body_pct': eb_body_pct, 'entry_bar_with': entry_bar_with,
        'vol_spike': vol_spike, 'strength': strength, 'leader_aligned': leader_aligned,
        'momentum_pct': momentum_pct, 'against_pats': against,
        'morning_aligned': morning_aligned, 'bar0_quality': bar0_quality,
        'sector_known': sec != 'unknown',
    }

target_data = [d for d in [compute(*t) for t in targets] if d]
loss_data = [d for d in [compute(*t) for t in stop_losses] if d]

print(f'Winners (target hits): {len(target_data)}')
print(f'Losers (stop losses): {len(loss_data)}')

# Boolean features
print(f'\n{"Feature":>25} {"Winners":>10} {"Losers":>10} {"Gap":>10}')
print('-' * 60)
for feat in ['gap_aligned', 'entry_bar_with', 'leader_aligned', 'morning_aligned', 'sector_known']:
    w = sum(1 for d in target_data if d[feat]) / len(target_data) * 100
    l = sum(1 for d in loss_data if d[feat]) / len(loss_data) * 100
    print(f'{feat:>25} {w:>9.0f}% {l:>9.0f}% {w-l:>+9.0f}%{"  <<<" if abs(w-l) > 15 else ""}')

# Numeric features
print(f'\n{"Feature":>25} {"Win avg":>10} {"Loss avg":>10} {"Gap":>10}')
print('-' * 60)
for feat in ['eb_body_pct', 'vol_spike', 'strength', 'momentum_pct', 'against_pats', 'bar0_quality']:
    w = sum(d[feat] for d in target_data) / len(target_data)
    l = sum(d[feat] for d in loss_data) / len(loss_data)
    print(f'{feat:>25} {w:>10.1f} {l:>10.1f} {w-l:>+10.1f}{"  <<<" if abs(w-l) > 8 else ""}')

# GOLDEN FILTER SEARCH
print('\n' + '=' * 60)
print('GOLDEN FILTER: what passes ALL winners, fails ALL losers?')
print('=' * 60)

import itertools
features_bool = {
    'gap_aligned': lambda d: d['gap_aligned'],
    'entry_bar_with': lambda d: d['entry_bar_with'],
    'leader_aligned': lambda d: d['leader_aligned'],
    'morning_aligned': lambda d: d['morning_aligned'],
    'sector_known': lambda d: d['sector_known'],
    'no_against_pats': lambda d: d['against_pats'] == 0,
    'body>30': lambda d: d['eb_body_pct'] > 30,
    'body>45': lambda d: d['eb_body_pct'] > 45,
    'strength>50': lambda d: d['strength'] > 0.5,
    'strength>33': lambda d: d['strength'] > 0.33,
    'momentum>50': lambda d: d['momentum_pct'] > 0.5,
    'vol_spike<50': lambda d: d['vol_spike'] < 50,
    'bar0_quality>40': lambda d: d['bar0_quality'] > 40,
}

# Try all pairs
best = None
for n in [1, 2, 3]:
    for combo in itertools.combinations(features_bool.items(), n):
        names = [c[0] for c in combo]
        fns = [c[1] for c in combo]

        w_pass = sum(1 for d in target_data if all(f(d) for f in fns))
        l_pass = sum(1 for d in loss_data if all(f(d) for f in fns))
        l_fail = len(loss_data) - l_pass

        # We want: high w_pass, low l_pass
        if w_pass >= len(target_data) * 0.7 and l_fail >= len(loss_data) * 0.5:
            ratio = w_pass / max(l_pass, 1)
            print(f'  {" + ".join(names):>50}: W={w_pass}/{len(target_data)} L_blocked={l_fail}/{len(loss_data)} ratio={ratio:.1f}')
