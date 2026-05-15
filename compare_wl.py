"""Compare ALL winners vs ALL losers at entry moment — find what's missing."""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from app.agents.data_providers import SectorAnalyzer, VolumeProfiler, PriceStructure
from app.agents.candle_patterns import detect_patterns
from app.signals.base import get_sector, SECTOR_MAP

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

winners = [
    ('DIVISLAB','2025-11-18',6,'SHORT'), ('WIPRO','2025-11-18',6,'SHORT'),
    ('TCS','2025-11-19',6,'LONG'), ('HEROMOTOCO','2025-11-19',6,'LONG'),
    ('HINDUNILVR','2025-11-19',6,'LONG'), ('JSWSTEEL','2025-11-20',6,'LONG'),
    ('INDUSINDBK','2025-11-24',6,'LONG'), ('GRASIM','2025-11-25',6,'LONG'),
    ('ONGC','2025-11-26',6,'LONG'), ('HINDALCO','2025-11-26',6,'LONG'),
    ('ADANIENT','2025-11-28',6,'LONG'), ('HCLTECH','2025-12-04',6,'LONG'),
    ('HCLTECH','2025-12-05',6,'LONG'), ('HCLTECH','2025-12-09',6,'SHORT'),
    ('MARUTI','2025-12-11',6,'LONG'), ('TATASTEEL','2025-12-12',6,'LONG'),
    ('TITAN','2025-12-16',6,'LONG'), ('HINDALCO','2025-12-17',6,'LONG'),
    ('HINDALCO','2025-12-22',6,'LONG'), ('INFY','2025-12-22',6,'LONG'),
    ('WIPRO','2025-12-22',6,'LONG'),
]

losers = [
    ('NTPC','2025-11-18',6,'SHORT'), ('JSWSTEEL','2025-11-20',6,'LONG'),
    ('INFY','2025-11-24',6,'LONG'), ('SBIN','2025-11-26',6,'LONG'),
    ('APOLLOHOSP','2025-11-26',6,'LONG'), ('HEROMOTOCO','2025-12-01',6,'LONG'),
    ('ADANIPORTS','2025-12-01',6,'LONG'), ('TATAMOTORS','2025-12-02',6,'SHORT'),
    ('TECHM','2025-12-02',6,'SHORT'), ('ULTRACEMCO','2025-12-02',6,'SHORT'),
    ('HINDALCO','2025-12-11',6,'LONG'), ('NESTLEIND','2025-12-16',6,'LONG'),
    ('HINDALCO','2025-12-19',6,'LONG'), ('HINDALCO','2025-12-22',6,'LONG'),
    ('TECHM','2025-12-29',6,'LONG'),
]

def compute(sym, date, bar, direction):
    sb = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
    pb = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]<date]
    if not sb or not pb or len(sb)<=bar: return None
    pc = pb[-1]['close']; eb = sb[bar]; entry = eb['close']

    # 1. Gap
    gap = (sb[0]['open'] - pc) / pc * 100
    gap_aligned = (direction=='LONG' and gap>0) or (direction=='SHORT' and gap<0)

    # 2. Morning move
    morning = (entry - sb[0]['open']) / sb[0]['open'] * 100
    morning_aligned = (direction=='LONG' and morning>0) or (direction=='SHORT' and morning<0)

    # 3. Entry bar
    eb_body = abs(eb['close']-eb['open'])/(eb['high']-eb['low'])*100 if eb['high']!=eb['low'] else 0
    entry_with = (direction=='LONG' and eb['close']>eb['open']) or (direction=='SHORT' and eb['close']<eb['open'])

    # 4. Volume
    prev_vol = sb[bar-1]['volume'] if bar>0 else 1
    vol_change = (eb['volume']-prev_vol)/prev_vol*100 if prev_vol>0 else 0

    # 5. Sector
    sec = get_sector(sym)
    sec_data = SectorAnalyzer.compute(all_data, date, bar)
    sec_info = sec_data.get(sec, {})
    leader_chg = sec_info.get('leader_change_pct', 0)
    strength = sec_info.get('strength', 0)
    leader_aligned = (direction=='LONG' and leader_chg>0.2) or (direction=='SHORT' and leader_chg<-0.2)

    # 6. How many of last 6 bars support direction
    with_bars = sum(1 for i in range(max(0,bar-5),bar+1)
        if (direction=='LONG' and sb[i]['close']>sb[i]['open']) or
           (direction=='SHORT' and sb[i]['close']<sb[i]['open']))

    # 7. Candle patterns
    pats = detect_patterns(sb, bar)
    support_pats = sum(1 for p in pats if (direction=='LONG' and p.direction=='bullish') or (direction=='SHORT' and p.direction=='bearish'))
    against_pats = sum(1 for p in pats if (direction=='LONG' and p.direction=='bearish') or (direction=='SHORT' and p.direction=='bullish'))

    # 8. Price vs previous close (how far has it already moved?)
    already_moved = abs(entry - pc) / pc * 100

    # 9. Bar 0 move (opening bar strength)
    bar0_move = (sb[0]['close'] - sb[0]['open']) / sb[0]['open'] * 100
    bar0_aligned = (direction=='LONG' and bar0_move>0) or (direction=='SHORT' and bar0_move<0)

    # 10. Number of days since last trade on this stock (proxy for familiarity)
    prev_dates = sorted(set(b['timestamp'][:10] for b in pb))
    days_of_data = len(prev_dates)

    return {
        'sym': sym, 'gap_aligned': gap_aligned, 'morning_aligned': morning_aligned,
        'eb_body': eb_body, 'entry_with': entry_with, 'vol_change': vol_change,
        'leader_aligned': leader_aligned, 'strength': strength,
        'with_bars': with_bars, 'support_pats': support_pats, 'against_pats': against_pats,
        'already_moved': already_moved, 'bar0_aligned': bar0_aligned,
        'gap': gap, 'morning': morning, 'leader_chg': leader_chg,
    }

w_data = [d for d in [compute(*t) for t in winners] if d]
l_data = [d for d in [compute(*t) for t in losers] if d]

print(f'Winners: {len(w_data)} | Losers: {len(l_data)}')
print()

# Boolean features
print(f'{"Feature":>25} {"W%":>8} {"L%":>8} {"Gap":>8}')
print('-' * 55)
for feat in ['gap_aligned','morning_aligned','entry_with','leader_aligned','bar0_aligned']:
    w = sum(1 for d in w_data if d[feat])/len(w_data)*100
    l = sum(1 for d in l_data if d[feat])/len(l_data)*100
    marker = ' <<<' if abs(w-l)>15 else ''
    print(f'{feat:>25} {w:>7.0f}% {l:>7.0f}% {w-l:>+7.0f}%{marker}')

# Numeric features
print()
print(f'{"Feature":>25} {"W avg":>8} {"L avg":>8} {"Gap":>8}')
print('-' * 55)
for feat in ['eb_body','vol_change','strength','with_bars','support_pats','against_pats','already_moved','gap','morning','leader_chg']:
    w = sum(d[feat] for d in w_data)/len(w_data)
    l = sum(d[feat] for d in l_data)/len(l_data)
    marker = ' <<<' if abs(w-l)>10 or (feat in ('strength','against_pats') and abs(w-l)>0.1) else ''
    print(f'{feat:>25} {w:>8.2f} {l:>8.2f} {w-l:>+8.2f}{marker}')

# The specific stocks that keep losing
print()
print('REPEAT LOSERS:')
from collections import Counter
loss_syms = Counter(sym for sym,_,_,_ in losers)
for sym, count in loss_syms.most_common(5):
    print(f'  {sym}: {count} losses')
