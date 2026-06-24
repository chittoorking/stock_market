"""Dry run: simulate trail and SL exit logic exactly as monitor_ws does."""
import sys
sys.path.insert(0, ".")

# Simulate a BUY position
entry = 1000.0
sl_pct = 0.1
trail_pct = 0.10
sl_price = entry * (1 - sl_pct / 100)  # 999.0
trail_level = sl_price
mfe = 0.0
trail_active = False

print(f"Entry: {entry}, SL: {sl_price}, Trail activates at MFE > {trail_pct}%")
print()

prices = [1000.5, 1001, 1001.5, 1002, 1001.8, 1001.5, 1001, 1000.5, 1000, 999.5, 999]

for price in prices:
    direction = 'BUY'
    fav = (price - entry) / entry * 100
    mfe = max(mfe, fav)

    # Trail logic (same as monitor_ws)
    if mfe > trail_pct:
        trail_active = True
        new_trail = mfe - trail_pct
        tp = entry * (1 + new_trail / 100)
        trail_level = max(trail_level, tp)

    # Exit check (same as monitor_ws)
    should_exit = False
    exit_reason = ''
    if trail_active:
        if price <= trail_level:
            should_exit = True
            exit_reason = f'TRAIL (MFE={mfe:.3f}%)'
    if not should_exit:
        if price <= sl_price:
            should_exit = True
            exit_reason = 'STOP LOSS'

    status = f"EXIT: {exit_reason}" if should_exit else "hold"
    print(f"  price={price:>7.1f} mfe={mfe:.2f}% trail_active={trail_active} trail_level={trail_level:.2f} -> {status}")
    if should_exit:
        pnl = (price - entry) / entry * 100
        print(f"  >> EXITED at {price}, PnL = {pnl:+.3f}%")
        break

print()

# Test SHORT
print("=== SHORT POSITION ===")
entry = 1000.0
sl_price = entry * (1 + sl_pct / 100)  # 1001.0
trail_level = sl_price
mfe = 0.0
trail_active = False

print(f"Entry: {entry}, SL: {sl_price}, Trail activates at MFE > {trail_pct}%")

prices = [999.5, 999, 998.5, 998, 998.3, 998.5, 999, 999.5, 1000, 1000.5, 1001]

for price in prices:
    direction = 'SELL'
    fav = (entry - price) / entry * 100
    mfe = max(mfe, fav)

    if mfe > trail_pct:
        trail_active = True
        new_trail = mfe - trail_pct
        tp = entry * (1 - new_trail / 100)
        trail_level = min(trail_level, tp)

    should_exit = False; exit_reason = ''
    if trail_active:
        if price >= trail_level:
            should_exit = True
            exit_reason = f'TRAIL (MFE={mfe:.3f}%)'
    if not should_exit:
        if price >= sl_price:
            should_exit = True
            exit_reason = 'STOP LOSS'

    status = f"EXIT: {exit_reason}" if should_exit else "hold"
    print(f"  price={price:>7.1f} mfe={mfe:.2f}% trail_active={trail_active} trail_level={trail_level:.2f} -> {status}")
    if should_exit:
        pnl = (entry - price) / entry * 100
        print(f"  >> EXITED at {price}, PnL = {pnl:+.3f}%")
        break
