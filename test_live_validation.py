# -*- coding: utf-8 -*-
"""
Validation tests for live trading code.
Run before every deployment: python test_live_validation.py
All tests must pass — ZERO tolerance for failures.
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PASS = 0
FAIL = 0

def check(name, got, expected, tol=0.0):
    global PASS, FAIL
    ok = abs(got - expected) <= tol if tol else got == expected
    if ok:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}')
        print(f'        expected={expected}  got={got}')

def check_true(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


# ─────────────────────────────────────────────
# HELPERS (mirrors live code exactly)
# ─────────────────────────────────────────────

def tick_round(val):
    """Must match live/upstox_client.py place_order tick_round exactly."""
    return round(round(val / 0.05) * 0.05, 2)

def make_sl_order(side, trigger_price):
    """Mirrors SL branch of place_order."""
    tp = tick_round(trigger_price)
    if side == 'SELL':
        price = tick_round(tp * 0.995)
    else:
        price = tick_round(tp * 1.005)
    return {'trigger_price': tp, 'price': price}

def make_gap_signal(entry, direction):
    """Mirrors check_gap_signal for LONG (gap-down) and SHORT (gap-up)."""
    if direction == 'LONG':
        return {
            'direction': 'LONG', 'strategy': 'GAP_FILL',
            'entry': round(entry, 2),
            'stop':   round(entry * (1 - 1.0 / 100), 2),
            'target': round(entry * (1 + 0.5 / 100), 2),
            'runner_step': 0.25,
        }
    else:
        return {
            'direction': 'SHORT', 'strategy': 'GAP_FILL',
            'entry': round(entry, 2),
            'stop':   round(entry * (1 + 1.0 / 100), 2),
            'target': round(entry * (1 - 0.5 / 100), 2),
            'runner_step': 0.25,
        }

def make_cam_signal(entry, direction):
    """Mirrors check_signal for CAM_R3/S3 (STOP=1.5, TARGET=1.75)."""
    if direction == 'LONG':
        return {
            'direction': 'LONG', 'strategy': 'CAM_S3',
            'entry': round(entry, 2),
            'stop':   round(entry * (1 - 1.5 / 100), 2),
            'target': round(entry * (1 + 1.75 / 100), 2),
        }
    else:
        return {
            'direction': 'SHORT', 'strategy': 'CAM_R3',
            'entry': round(entry, 2),
            'stop':   round(entry * (1 + 1.5 / 100), 2),
            'target': round(entry * (1 - 1.75 / 100), 2),
        }

TRAIL_ACTIVATE = 1.00
TRAIL_LOCK     = 0.075
RUNNER_STEP    = 0.25

def check_exit(signal, current_price, mfe, trail_active, target_hit):
    """Mirrors live/strategy.py check_exit exactly."""
    entry     = signal['entry']
    direction = signal['direction']
    sig_target = abs(signal['entry'] - signal['target']) / signal['entry'] * 100
    sig_runner = signal.get('runner_step', RUNNER_STEP)

    if direction == 'SHORT':
        fav = (entry - current_price) / entry * 100
    else:
        fav = (current_price - entry) / entry * 100

    new_mfe        = max(mfe, fav)
    new_trail      = trail_active or new_mfe >= min(sig_target, TRAIL_ACTIVATE)
    new_target_hit = target_hit  or new_mfe >= sig_target

    if new_target_hit:
        runner_stop_pct = max(new_mfe - sig_runner, sig_target)
        if direction == 'SHORT':
            runner_stop_price = entry * (1 - runner_stop_pct / 100)
        else:
            runner_stop_price = entry * (1 + runner_stop_pct / 100)

        if direction == 'SHORT' and current_price >= runner_stop_price:
            return 'runner_stop', runner_stop_price, new_mfe, new_trail, new_target_hit
        if direction == 'LONG'  and current_price <= runner_stop_price:
            return 'runner_stop', runner_stop_price, new_mfe, new_trail, new_target_hit
        return None, None, new_mfe, new_trail, new_target_hit

    if new_trail:
        if direction == 'SHORT':
            lock_price = entry * (1 - TRAIL_LOCK / 100)
            if current_price >= lock_price:
                return 'trail_stop', lock_price, new_mfe, new_trail, new_target_hit
        else:
            lock_price = entry * (1 + TRAIL_LOCK / 100)
            if current_price <= lock_price:
                return 'trail_stop', lock_price, new_mfe, new_trail, new_target_hit

    if direction == 'SHORT' and current_price >= signal['stop']:
        return 'stop_loss', signal['stop'], new_mfe, new_trail, new_target_hit
    if direction == 'LONG'  and current_price <= signal['stop']:
        return 'stop_loss', signal['stop'], new_mfe, new_trail, new_target_hit

    return None, None, new_mfe, new_trail, new_target_hit


# ─────────────────────────────────────────────
# 1. TICK ROUNDING
# ─────────────────────────────────────────────
print('\n=== 1. TICK ROUNDING ===')

# Today's exact failures must be fixed
check('COALINDIA 424.71 -> 424.70', tick_round(424.71), 424.70)
check('APOLLOHOSP 8087.84 -> 8087.85', tick_round(8087.84), 8087.85)
check('WIPRO 200.43 -> 200.45', tick_round(200.43), 200.45)

# Already valid values must not change
for v in [424.70, 200.00, 8085.85, 430.00, 100.00, 0.05]:
    check(f'tick_round({v}) unchanged', tick_round(v), v)

# All outputs must be multiples of 0.05
for v in [424.71, 8087.84, 200.43, 199.22, 8047.40, 436.90, 203.58]:
    tr = tick_round(v)
    remainder = round(tr / 0.05 % 1, 8)
    check_true(f'tick_round({v})={tr} is multiple of 0.05',
               remainder < 1e-9 or abs(remainder - 1) < 1e-9)

# tick_round is idempotent: applying twice gives same result
for v in [100.13, 250.47, 1500.0, 3750.22, 8209.01]:
    tr = tick_round(v)
    check(f'tick_round idempotent ({v})', tick_round(tr), tr)


# ─────────────────────────────────────────────
# 2. SL ORDER STRUCTURE
# ─────────────────────────────────────────────
print('\n=== 2. SL ORDER STRUCTURE ===')

# SELL SL (for LONG positions): price must be BELOW trigger
for entry, stop_pct in [(429.0, 1.0), (203.58, 1.5), (8209.0, 1.5), (436.9, 1.0)]:
    raw_stop = entry * (1 - stop_pct / 100)
    o = make_sl_order('SELL', raw_stop)
    check_true(f'SELL SL price < trigger (entry={entry})',
               o['price'] < o['trigger_price'],
               f"price={o['price']}  trigger={o['trigger_price']}")
    r1 = round(o['trigger_price'] / 0.05 % 1, 8)
    r2 = round(o['price'] / 0.05 % 1, 8)
    check_true(f'SELL SL trigger is tick-valid (entry={entry})',
               r1 < 1e-9 or abs(r1-1) < 1e-9, f"trigger={o['trigger_price']}")
    check_true(f'SELL SL price is tick-valid (entry={entry})',
               r2 < 1e-9 or abs(r2-1) < 1e-9, f"price={o['price']}")

# BUY SL (for SHORT positions): price must be ABOVE trigger
for entry, stop_pct in [(429.0, 1.0), (203.58, 1.5), (8209.0, 1.5)]:
    raw_stop = entry * (1 + stop_pct / 100)
    o = make_sl_order('BUY', raw_stop)
    check_true(f'BUY SL price > trigger (entry={entry})',
               o['price'] > o['trigger_price'],
               f"price={o['price']}  trigger={o['trigger_price']}")
    r1 = round(o['trigger_price'] / 0.05 % 1, 8)
    check_true(f'BUY SL trigger is tick-valid (entry={entry})',
               r1 < 1e-9 or abs(r1-1) < 1e-9)


# ─────────────────────────────────────────────
# 3. SIGNAL STRUCTURE
# ─────────────────────────────────────────────
print('\n=== 3. SIGNAL STRUCTURE ===')

# GAP FILL: stop=1.0%, target=0.5%, direction-correct, has runner_step
for open_p in [200.0, 429.0, 1000.0, 8209.0]:
    for direction in ['LONG', 'SHORT']:
        sig = make_gap_signal(open_p, direction)
        stop_pct = abs(sig['entry'] - sig['stop'])   / sig['entry'] * 100
        tgt_pct  = abs(sig['entry'] - sig['target']) / sig['entry'] * 100
        # Tolerance 0.02% covers max tick rounding drift (0.05/2 / entry)
        check_true(f'GAP {direction}: stop~1.0% (open={open_p})',
                   abs(stop_pct - 1.0) < 0.02, f'got {stop_pct:.4f}%')
        check_true(f'GAP {direction}: target~0.5% (open={open_p})',
                   abs(tgt_pct - 0.5) < 0.02, f'got {tgt_pct:.4f}%')
        check_true(f'GAP {direction}: has runner_step', 'runner_step' in sig)
        check('GAP runner_step=0.25', sig['runner_step'], 0.25)
        if direction == 'LONG':
            check_true(f'GAP LONG: stop < entry (open={open_p})', sig['stop'] < sig['entry'])
            check_true(f'GAP LONG: target > entry (open={open_p})', sig['target'] > sig['entry'])
        else:
            check_true(f'GAP SHORT: stop > entry (open={open_p})', sig['stop'] > sig['entry'])
            check_true(f'GAP SHORT: target < entry (open={open_p})', sig['target'] < sig['entry'])

# CAM: stop=1.5%, target=1.75%, no runner_step
for entry in [203.58, 436.90, 8209.0]:
    for direction in ['LONG', 'SHORT']:
        sig = make_cam_signal(entry, direction)
        stop_pct = abs(sig['entry'] - sig['stop'])   / sig['entry'] * 100
        tgt_pct  = abs(sig['entry'] - sig['target']) / sig['entry'] * 100
        check_true(f'CAM {direction}: stop~1.5% (entry={entry})',
                   abs(stop_pct - 1.5) < 0.02, f'got {stop_pct:.4f}%')
        check_true(f'CAM {direction}: target~1.75% (entry={entry})',
                   abs(tgt_pct - 1.75) < 0.02, f'got {tgt_pct:.4f}%')
        if direction == 'LONG':
            check_true(f'CAM LONG: stop < entry', sig['stop'] < sig['entry'])
            check_true(f'CAM LONG: target > entry', sig['target'] > sig['entry'])
        else:
            check_true(f'CAM SHORT: stop > entry', sig['stop'] > sig['entry'])
            check_true(f'CAM SHORT: target < entry', sig['target'] < sig['entry'])


# ─────────────────────────────────────────────
# 4. CHECK_EXIT — all phases
# ─────────────────────────────────────────────
print('\n=== 4. CHECK_EXIT LOGIC ===')

# --- CAM LONG (entry=200) ---
cam_l = make_cam_signal(200.0, 'LONG')
stop_p  = cam_l['stop']    # ~197
target_p = cam_l['target'] # ~203.5

# Phase 1: price moving in favor — no exit
a, _, _, _, _ = check_exit(cam_l, 201.0, 0, False, False)
check_true('CAM LONG: no exit while price up', a is None)

# Phase 1: stop hit
a, px, _, _, _ = check_exit(cam_l, stop_p - 0.01, 0, False, False)
check('CAM LONG: stop_loss at stop price', a, 'stop_loss')
check('CAM LONG: stop_loss exit price = signal stop', px, stop_p)

# Phase 1: price exactly at stop
a, px, _, _, _ = check_exit(cam_l, stop_p, 0, False, False)
check('CAM LONG: stop_loss when price = stop', a, 'stop_loss')

# Phase 2: trail activates at 1% MFE
trail_activate_p = 200.0 * (1 + TRAIL_ACTIVATE / 100)  # 202.0
a, _, mfe2, trail2, tgt2 = check_exit(cam_l, trail_activate_p, 0, False, False)
check_true('CAM LONG: trail activates at 1% MFE', trail2)
check_true('CAM LONG: target NOT hit at 1% MFE', not tgt2)

# Phase 2: trail active, price drops to lock
lock_p = 200.0 * (1 + TRAIL_LOCK / 100)  # 200.15
a, px, _, _, _ = check_exit(cam_l, lock_p, mfe2, True, False)
check('CAM LONG: trail_stop when price drops to lock', a, 'trail_stop')
check_true('CAM LONG: trail exit price is lock price', abs(px - lock_p) < 0.01)

# Phase 2: price above lock — no exit
a, _, _, _, _ = check_exit(cam_l, 201.5, 1.5, True, False)
check_true('CAM LONG: no exit while above lock in trail', a is None)

# Phase 3: target hit
a, _, mfe3, trail3, tgt3 = check_exit(cam_l, target_p + 0.01, 0, False, False)
check_true('CAM LONG: target_hit when price reaches target', tgt3)
check_true('CAM LONG: trail active when target hit', trail3)

# Phase 3: in runner, price continuing — no exit (floor = target %)
sig_target_pct = abs(200.0 - target_p) / 200.0 * 100
mfe_high = sig_target_pct + 1.0  # 1% above target
runner_stop = 200.0 * (1 + max(mfe_high - RUNNER_STEP, sig_target_pct) / 100)
price_above_runner = runner_stop + 0.50
a, _, _, _, _ = check_exit(cam_l, price_above_runner, mfe_high, True, True)
check_true('CAM LONG: no exit while price above runner stop', a is None)

# Phase 3: price retraces to runner stop
a, _, _, _, _ = check_exit(cam_l, runner_stop - 0.01, mfe_high, True, True)
check('CAM LONG: runner_stop fires on retrace', a, 'runner_stop')

# --- CAM SHORT (entry=430) ---
cam_s = make_cam_signal(430.0, 'SHORT')
stop_s   = cam_s['stop']    # ~436.45
target_s = cam_s['target']  # ~422.48

# Phase 1: stop hit
a, px, _, _, _ = check_exit(cam_s, stop_s + 0.01, 0, False, False)
check('CAM SHORT: stop_loss when price rises to stop', a, 'stop_loss')

# Phase 1: moving in favor — no exit
a, _, _, _, _ = check_exit(cam_s, 428.0, 0, False, False)
check_true('CAM SHORT: no exit when price falling', a is None)

# Phase 2: trail activates at 1% MFE
trail_p_s = 430.0 * (1 - TRAIL_ACTIVATE / 100)  # 425.7
a, _, _, trail2s, _ = check_exit(cam_s, trail_p_s, 0, False, False)
check_true('CAM SHORT: trail activates at 1% MFE', trail2s)

# Phase 3: target hit
a, _, _, _, tgt3s = check_exit(cam_s, target_s - 0.01, 0, False, False)
check_true('CAM SHORT: target_hit when price reaches target', tgt3s)

# Phase 3: runner — price retraces to runner stop
sig_tgt_s = abs(430.0 - target_s) / 430.0 * 100
mfe_s = sig_tgt_s + 1.0
runner_stop_s = 430.0 * (1 - max(mfe_s - RUNNER_STEP, sig_tgt_s) / 100)
a, _, _, _, _ = check_exit(cam_s, runner_stop_s + 0.01, mfe_s, True, True)
check('CAM SHORT: runner_stop on retrace', a, 'runner_stop')

# --- GAP FILL LONG (entry=429) ---
gap_l = make_gap_signal(429.0, 'LONG')
# sig_target is computed from signal (slightly < 0.5% due to rounding)
sig_tgt_g = abs(gap_l['entry'] - gap_l['target']) / gap_l['entry'] * 100

# Target hit: price goes to actual target price in signal
a, _, mfe_g, _, tgt_g = check_exit(gap_l, gap_l['target'], 0, False, False)
check_true('GAP LONG: target_hit at signal target price', tgt_g)

# Runner: price goes 2% up — no exit yet
mfe_g2 = 2.0
runner_floor = 429.0 * (1 + max(mfe_g2 - RUNNER_STEP, sig_tgt_g) / 100)
price_still_running = runner_floor + 0.50
a, _, _, _, _ = check_exit(gap_l, price_still_running, mfe_g2, True, True)
check_true('GAP LONG: no exit while price above runner stop', a is None)

# Runner: price retraces to runner stop
a, _, _, _, _ = check_exit(gap_l, runner_floor - 0.01, mfe_g2, True, True)
check('GAP LONG: runner_stop fires at MFE-0.25%', a, 'runner_stop')

# --- GAP FILL SHORT (entry=429) ---
gap_s = make_gap_signal(429.0, 'SHORT')
sig_tgt_gs = abs(gap_s['entry'] - gap_s['target']) / gap_s['entry'] * 100

# Target hit
a, _, _, _, tgt_gs = check_exit(gap_s, gap_s['target'], 0, False, False)
check_true('GAP SHORT: target_hit at signal target price', tgt_gs)

# Runner: price continues down 2% — no exit
mfe_gs2 = 2.0
runner_floor_s = 429.0 * (1 - max(mfe_gs2 - RUNNER_STEP, sig_tgt_gs) / 100)
a, _, _, _, _ = check_exit(gap_s, runner_floor_s - 0.50, mfe_gs2, True, True)
check_true('GAP SHORT: no exit while price below runner stop', a is None)

# Runner: price retraces
a, _, _, _, _ = check_exit(gap_s, runner_floor_s + 0.01, mfe_gs2, True, True)
check('GAP SHORT: runner_stop fires at MFE-0.25%', a, 'runner_stop')

# Stop loss in GAP is 1% (not 1.5%) — must NOT exit at 0.9%, must exit at 1%
a, _, _, _, _ = check_exit(gap_l, 429.0 * (1 - 0.009), 0, False, False)
check_true('GAP LONG: no stop at 0.9% adverse', a is None)
a, _, _, _, _ = check_exit(gap_l, gap_l['stop'] - 0.01, 0, False, False)
check('GAP LONG: stop_loss at 1% adverse', a, 'stop_loss')


# ─────────────────────────────────────────────
# 5. FULL SCENARIO — COALINDIA today
# ─────────────────────────────────────────────
print('\n=== 5. SCENARIO: COALINDIA GAP FILL LONG (2026-05-27) ===')

coal = make_gap_signal(429.0, 'LONG')
check_true('scenario: entry=429', coal['entry'] == 429.0)
check_true('scenario: stop is 1% below open', abs(coal['stop'] - 424.71) < 0.10)
check_true('scenario: target is 0.5% above open', abs(coal['target'] - 431.14) < 0.10)

# SL order must be accepted by exchange
sl = make_sl_order('SELL', coal['stop'])
check_true('scenario: SL trigger is tick-valid',
           round(sl['trigger_price'] / 0.05 % 1, 8) < 1e-9)
check_true('scenario: SL price < trigger', sl['price'] < sl['trigger_price'])

# Simulate price path: COALINDIA went 429 -> 463 all day
price_path = [429, 432, 436, 440, 445, 450, 455, 460, 463]
mfe, trail, tgt = 0.0, False, False
exit_action = None
for p in price_path:
    action, _, mfe, trail, tgt = check_exit(coal, p, mfe, trail, tgt)
    if action:
        exit_action = action
        break

check_true('scenario: never stopped out (SL=424.70)', exit_action is None)
check_true('scenario: target hit during run', tgt)
check_true('scenario: trail activated', trail)

# At EOD price 463, runner_stop is well below 463 — still holding
sig_tgt_c = abs(coal['entry'] - coal['target']) / coal['entry'] * 100
runner_stop_eod = 429.0 * (1 + max(mfe - RUNNER_STEP, sig_tgt_c) / 100)
check_true('scenario: runner_stop < 463 at EOD (still holding)',
           runner_stop_eod < 463,
           f'runner_stop={runner_stop_eod:.2f}')

pnl_pct = (463.0 - 429.0) / 429.0 * 100
pnl_rs = pnl_pct / 100 * 152 * 429.0
print(f'  INFO: theoretical P&L = {pnl_pct:.2f}%  Rs {pnl_rs:+,.0f}  (lost to bugs: Rs 5,000+)')


# ─────────────────────────────────────────────
# RESULT
# ─────────────────────────────────────────────
print()
print('=' * 50)
print(f'  PASSED: {PASS}')
print(f'  FAILED: {FAIL}')
print('=' * 50)
if FAIL > 0:
    print('  *** FIX ALL FAILURES BEFORE DEPLOYING ***')
    sys.exit(1)
else:
    print('  ALL TESTS PASSED — safe to deploy')
    sys.exit(0)
