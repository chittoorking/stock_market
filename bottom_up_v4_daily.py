"""
BOTTOM-UP v4 — DAILY TRADE PICKER.
Goal: 1 trade per day, highest WR.
Uses ALL insights from v3:
- MoE component combos (P>=9+M>=8 = 92% WR)
- Raw indicator enhancement (candle <= -1 adds +15pp WR)
- Strategy-specific filters

New approaches:
1. Instead of picking #1 moe score, pick #1 from a FILTERED pool
2. Try different daily selection criteria
3. Find the sweet spot: max WR × max trading days
"""
import sys; sys.path.insert(0, '.')
import json, time
from pathlib import Path
from collections import defaultdict

# Load pre-computed results from v3
print('Loading v3 results...', flush=True)
with open('data/bottom_up_v3_results.json') as f:
    results = json.load(f)

all_dates = sorted(set(r['date'] for r in results))
print(f'{len(results)} signals across {len(all_dates)} days')

# Group by date
by_date = defaultdict(list)
for r in results:
    by_date[r['date']].append(r)

# ═══════════════════════════════════════════════════════════════
# STRATEGY 1: FILTER + PICK BEST
# Apply progressively tighter filters, pick #1 from filtered pool
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY 1: Filter + Pick Best (by moe score)')
print('='*80)

filters = [
    ('No filter', lambda r: True),
    ('M>=7', lambda r: r['M']>=7),
    ('M>=8', lambda r: r['M']>=8),
    ('M>=9', lambda r: r['M']>=9),
    ('P>=7', lambda r: r['P']>=7),
    ('P>=8', lambda r: r['P']>=8),
    ('P>=7 + M>=7', lambda r: r['P']>=7 and r['M']>=7),
    ('P>=7 + M>=8', lambda r: r['P']>=7 and r['M']>=8),
    ('P>=8 + M>=8', lambda r: r['P']>=8 and r['M']>=8),
    ('P>=8 + M>=9', lambda r: r['P']>=8 and r['M']>=9),
    ('P>=7 + M>=9', lambda r: r['P']>=7 and r['M']>=9),
    ('P>=7 + M>=8 + Mkt>=6', lambda r: r['P']>=7 and r['M']>=8 and r['Mkt']>=6),
    ('P>=7 + M>=9 + Mkt>=6', lambda r: r['P']>=7 and r['M']>=9 and r['Mkt']>=6),
    ('P>=7 + M>=9 + Mkt>=7', lambda r: r['P']>=7 and r['M']>=9 and r['Mkt']>=7),
    ('P>=8 + M>=9 + Mkt>=7', lambda r: r['P']>=8 and r['M']>=9 and r['Mkt']>=7),
    # With raw indicator enhancement
    ('P>=7 + M>=8 + candle<=0', lambda r: r['P']>=7 and r['M']>=8 and r['candle']<=0),
    ('P>=7 + M>=8 + candle<=-1', lambda r: r['P']>=7 and r['M']>=8 and r['candle']<=-1),
    ('P>=7 + M>=8 + morning_adj>=0.5', lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=0.5),
    ('P>=7 + M>=8 + gap_adj>=0', lambda r: r['P']>=7 and r['M']>=8 and r['gap_adj']>=0),
    ('P>=7 + M>=8 + vwap_adj>=0.2', lambda r: r['P']>=7 and r['M']>=8 and r['vwap_adj']>=0.2),
    ('P>=7 + M>=8 + consec>=3', lambda r: r['P']>=7 and r['M']>=8 and r['consec']>=3),
    ('moe>=30 + candle<=-1', lambda r: r['moe']>=30 and r['candle']<=-1),
    ('moe>=30 + candle<=-1 + M>=8', lambda r: r['moe']>=30 and r['candle']<=-1 and r['M']>=8),
    ('moe>=33 + candle<=-1', lambda r: r['moe']>=33 and r['candle']<=-1),
    ('moe>=35 + candle<=-1', lambda r: r['moe']>=35 and r['candle']<=-1),
]

print(f"\n{'Filter':>45} {'Days':>5} {'W':>3} {'L':>3} {'WR':>5} {'P&L':>8} {'Avg':>7}")
print('-'*85)
for label, filt in filters:
    wins = 0; total = 0; pnl_sum = 0
    for date in all_dates:
        pool = [r for r in by_date[date] if filt(r)]
        if not pool: continue
        # Pick highest moe score
        best = max(pool, key=lambda x: x['moe'])
        total += 1
        if best['win']: wins += 1
        pnl_sum += best['pnl']
    if total < 2: continue
    wr = wins/total*100
    avg = pnl_sum/total
    marker = ' ***' if wr >= 80 else (' <<<' if wr >= 60 else '')
    print(f"{label:>45} {total:>5} {wins:>3} {total-wins:>3} {wr:>4.0f}% {pnl_sum:>+7.2f}% {avg:>+6.3f}%{marker}")


# ═══════════════════════════════════════════════════════════════
# STRATEGY 2: DEDUP BY SYMBOL, THEN PICK BY DIFFERENT CRITERIA
# Maybe the highest moe isn't the best daily pick.
# Try: highest P+M, highest V, etc.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY 2: Different ranking criteria (within P>=7 + M>=8 filter)')
print('='*80)

base_filter = lambda r: r['P']>=7 and r['M']>=8
rankers = [
    ('By moe (default)', lambda x: x['moe']),
    ('By P+M', lambda x: x['P']+x['M']),
    ('By P*M', lambda x: x['P']*x['M']),
    ('By M alone', lambda x: x['M']),
    ('By P alone', lambda x: x['P']),
    ('By V+P+M', lambda x: x['V']+x['P']+x['M']),
    ('By morning_adj', lambda x: x['morning_adj']),
    ('By gap_adj', lambda x: x['gap_adj']),
    ('By vwap_adj', lambda x: x['vwap_adj']),
    ('By pnl expectancy (P*M/noise)', lambda x: x['P']*x['M']/(x['noise']+1)),
    ('By least noise', lambda x: -x['noise']),
    ('By highest body_ratio', lambda x: x['body_ratio']),
    ('By lowest consec (freshest)', lambda x: -x['consec']),
]

print(f"\n{'Ranker':>40} {'Days':>5} {'W':>3} {'L':>3} {'WR':>5} {'P&L':>8}")
print('-'*70)
for label, ranker in rankers:
    wins = 0; total = 0; pnl_sum = 0
    for date in all_dates:
        pool = [r for r in by_date[date] if base_filter(r)]
        if not pool: continue
        # Dedup by symbol (keep highest moe per symbol)
        by_sym = {}
        for r in pool:
            if r['sym'] not in by_sym or r['moe'] > by_sym[r['sym']]['moe']:
                by_sym[r['sym']] = r
        pool = list(by_sym.values())
        # Rank by criteria
        best = max(pool, key=ranker)
        total += 1
        if best['win']: wins += 1
        pnl_sum += best['pnl']
    if total < 2: continue
    wr = wins/total*100
    marker = ' <<<' if wr >= 60 else ''
    print(f"{label:>40} {total:>5} {wins:>3} {total-wins:>3} {wr:>4.0f}% {pnl_sum:>+7.2f}%{marker}")


# ═══════════════════════════════════════════════════════════════
# STRATEGY 3: MULTIPLE TRADES PER DAY (portfolio approach)
# Instead of picking 1, take ALL that pass filter. Average WR matters.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY 3: ALL trades that pass filter (portfolio, deduped by sym)')
print('='*80)

for label, filt in filters:
    wins = 0; total = 0; pnl_sum = 0; trading_days = 0
    for date in all_dates:
        pool = [r for r in by_date[date] if filt(r)]
        if not pool: continue
        # Dedup by symbol
        by_sym = {}
        for r in pool:
            if r['sym'] not in by_sym or r['moe'] > by_sym[r['sym']]['moe']:
                by_sym[r['sym']] = r
        pool = list(by_sym.values())
        trading_days += 1
        for r in pool:
            total += 1
            if r['win']: wins += 1
            pnl_sum += r['pnl']
    if total < 5: continue
    wr = wins/total*100
    avg = pnl_sum/total
    marker = ' ***' if wr >= 80 else (' <<<' if wr >= 60 else '')
    print(f"  {label:>43} | {total:>4} trades, {trading_days} days, WR={wr:.0f}%, P&L={pnl_sum:+.2f}%, avg={avg:+.3f}%{marker}")


# ═══════════════════════════════════════════════════════════════
# STRATEGY 4: CONFIRMATION STACK — require multiple confirmations
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY 4: Confirmation stacks (MoE + raw) — portfolio')
print('='*80)

stack_filters = [
    # MoE base + 1 raw confirmation
    ('P>=7+M>=8 + morning_adj>=1', lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1),
    ('P>=7+M>=8 + gap_adj>=0.3', lambda r: r['P']>=7 and r['M']>=8 and r['gap_adj']>=0.3),
    ('P>=7+M>=8 + vwap_adj>=0.2', lambda r: r['P']>=7 and r['M']>=8 and r['vwap_adj']>=0.2),
    ('P>=7+M>=8 + noise<3', lambda r: r['P']>=7 and r['M']>=8 and r['noise']<3),
    ('P>=7+M>=8 + body_ratio>=50', lambda r: r['P']>=7 and r['M']>=8 and r['body_ratio']>=50),
    ('P>=7+M>=8 + consec>=2', lambda r: r['P']>=7 and r['M']>=8 and r['consec']>=2),
    ('P>=7+M>=8 + candle<0', lambda r: r['P']>=7 and r['M']>=8 and r['candle']<0),
    # MoE base + 2 raw confirmations
    ('P>=7+M>=8 + morning_adj>=1 + gap_adj>=0.3', lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1 and r['gap_adj']>=0.3),
    ('P>=7+M>=8 + morning_adj>=1 + noise<3', lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1 and r['noise']<3),
    ('P>=7+M>=8 + gap_adj>=0.3 + vwap_adj>=0.2', lambda r: r['P']>=7 and r['M']>=8 and r['gap_adj']>=0.3 and r['vwap_adj']>=0.2),
    ('P>=7+M>=8 + morning_adj>=1 + vwap_adj>=0.2', lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1 and r['vwap_adj']>=0.2),
    ('P>=7+M>=8 + candle<0 + noise<3', lambda r: r['P']>=7 and r['M']>=8 and r['candle']<0 and r['noise']<3),
    ('P>=7+M>=8 + gap_adj>=0.3 + noise<3', lambda r: r['P']>=7 and r['M']>=8 and r['gap_adj']>=0.3 and r['noise']<3),
    # MoE base + 3 raw confirmations
    ('P>=7+M>=8 + morning>=1 + gap>=0.3 + noise<3',
     lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1 and r['gap_adj']>=0.3 and r['noise']<3),
    ('P>=7+M>=8 + morning>=1 + vwap>=0.2 + noise<3',
     lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1 and r['vwap_adj']>=0.2 and r['noise']<3),
    ('P>=7+M>=8 + morning>=1 + gap>=0.3 + vwap>=0.2',
     lambda r: r['P']>=7 and r['M']>=8 and r['morning_adj']>=1 and r['gap_adj']>=0.3 and r['vwap_adj']>=0.2),
    # Including V
    ('V>=6+P>=7+M>=8 + morning>=1', lambda r: r['V']>=6 and r['P']>=7 and r['M']>=8 and r['morning_adj']>=1),
    ('V>=6+P>=7+M>=8 + noise<3', lambda r: r['V']>=6 and r['P']>=7 and r['M']>=8 and r['noise']<3),
    ('V>=7+P>=7+M>=8', lambda r: r['V']>=7 and r['P']>=7 and r['M']>=8),
    ('V>=7+P>=8+M>=8', lambda r: r['V']>=7 and r['P']>=8 and r['M']>=8),
]

print(f"\n{'Stack':>55} {'N':>4} {'Days':>5} {'WR':>5} {'P&L':>8}")
print('-'*85)
for label, filt in stack_filters:
    wins = 0; total = 0; trading_days = 0; pnl_sum = 0
    for date in all_dates:
        pool = [r for r in by_date[date] if filt(r)]
        if not pool: continue
        by_sym = {}
        for r in pool:
            if r['sym'] not in by_sym or r['moe'] > by_sym[r['sym']]['moe']:
                by_sym[r['sym']] = r
        pool = list(by_sym.values())
        trading_days += 1
        for r in pool:
            total += 1
            if r['win']: wins += 1
            pnl_sum += r['pnl']
    if total < 5: continue
    wr = wins/total*100
    marker = ' ***' if wr >= 80 else (' <<<' if wr >= 65 else '')
    print(f"{label:>55} {total:>4} {trading_days:>5} {wr:>4.0f}% {pnl_sum:>+7.2f}%{marker}")


# ═══════════════════════════════════════════════════════════════
# STRATEGY 5: PER-STRATEGY FILTERS
# Different strategies may need different filters
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY 5: Per-strategy analysis (which strategies are best with MoE?)')
print('='*80)
strats = sorted(set(r['strat'] for r in results))
for strat in strats:
    strat_sigs = [r for r in results if r['strat'] == strat]
    total = len(strat_sigs)
    wins = sum(r['win'] for r in strat_sigs)
    if total < 10: continue
    wr = wins/total*100

    # With P>=7+M>=8
    filtered = [r for r in strat_sigs if r['P']>=7 and r['M']>=8]
    if len(filtered) >= 3:
        fw = sum(r['win'] for r in filtered)
        fwr = fw/len(filtered)*100
        print(f"  {strat:>15}: raw={total:>4} WR={wr:.0f}% | P>=7+M>=8: {len(filtered):>3} WR={fwr:.0f}%")
    else:
        print(f"  {strat:>15}: raw={total:>4} WR={wr:.0f}%")


# ═══════════════════════════════════════════════════════════════
# SHOW INDIVIDUAL TRADES for best setups
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('TRADE LOG: P>=7 + M>=8 (deduped by sym, pick best per day)')
print('='*80)
print(f"{'Date':>12} {'Sym':>10} {'Dir':>6} {'Strat':>12} {'P':>3} {'M':>3} {'V':>3} {'Mkt':>3} {'MoE':>5} {'PnL':>7} {'Win':>4}")
print('-'*75)
for date in all_dates:
    pool = [r for r in by_date[date] if r['P']>=7 and r['M']>=8]
    if not pool: continue
    # Dedup by sym
    by_sym = {}
    for r in pool:
        if r['sym'] not in by_sym or r['moe'] > by_sym[r['sym']]['moe']:
            by_sym[r['sym']] = r
    # Pick top 1
    best = max(by_sym.values(), key=lambda x: x['moe'])
    w = 'W' if best['win'] else 'L'
    print(f"{best['date']:>12} {best['sym']:>10} {best['dir']:>6} {best['strat']:>12} {best['P']:>3.0f} {best['M']:>3.0f} {best['V']:>3.0f} {best['Mkt']:>3.0f} {best['moe']:>5.1f} {best['pnl']:>+6.2f}% {w:>4}")
