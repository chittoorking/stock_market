"""How much capital to reach Rs 1 Cr, 5 Cr etc."""
import sys,io
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')

per_trade = 7805  # NET per trade
trades_per_year = 208  # 831 / 4 years
flat_yearly = per_trade * trades_per_year  # Rs 16.2L/year at Rs 10L

print('='*60)
print('FLAT (no compounding, Rs 10L position always)')
print('='*60)
print(f'Rs 10L capital -> Rs {flat_yearly/100000:.1f}L/year')
print()
for target in [50, 100, 200, 500]:
    years = target * 100000 / flat_yearly
    print(f'  Rs {target}L ({target/100:.0f} Cr): {years:.1f} years')

print(f'\n{"="*60}')
print('WITH COMPOUNDING')
print('Scale position size as capital grows')
print('Cap at Rs 50L max position (realistic broker limit)')
print('='*60)

for start in [500000, 1000000, 2500000, 5000000]:
    cap = float(start)
    pct = 7805 / 1000000  # 0.78% per trade

    print(f'\n  Starting Rs {start/100000:.0f}L:')
    for year in range(1, 11):
        year_start = cap
        for t in range(trades_per_year):
            pos = min(cap, 5000000)  # Max Rs 50L position
            pnl = pct * pos
            cap += pnl
        profit = cap - year_start
        label = ''
        if cap >= 10000000 and year_start < 10000000: label = ' *** 1 CRORE ***'
        if cap >= 50000000 and year_start < 50000000: label = ' *** 5 CRORE ***'
        if cap >= 100000000 and year_start < 100000000: label = ' *** 10 CRORE ***'
        print(f'    Year {year}: Rs {year_start/100000:>7.1f}L -> Rs {cap/100000:>8.1f}L  (+Rs {profit/100000:.1f}L){label}')
        if cap >= 100000000: break

print(f'\n{"="*60}')
print('SUMMARY: When do you hit Rs 1 Crore?')
print('='*60)

for start in [500000, 1000000, 2500000, 5000000, 10000000]:
    cap = float(start)
    pct = 7805 / 1000000
    for year in range(1, 20):
        for t in range(trades_per_year):
            pos = min(cap, 5000000)
            cap += pct * pos
        if cap >= 10000000:
            print(f'  Rs {start/100000:>4.0f}L start -> Rs 1 Cr in {year} years')
            break

print()
print('NOTE: Compounding assumes you increase position size')
print('as capital grows. Max Rs 50L position (5x leverage on')
print('Rs 10L = Rs 50L exposure). Beyond Rs 50L capital,')
print('returns become flat at ~Rs 81L/year.')
