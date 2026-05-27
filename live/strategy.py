"""Strategy logic — pure functions, no API calls. Same as backtest."""
import logging
from collections import defaultdict
from . import config

log = logging.getLogger('strategy')


def compute_daily_trend(daily_closes):
    """Compute 5-day trend from list of daily closes.
    Uses last 5 COMPLETED days (excludes today if included).
    Returns 'UP', 'DOWN', or 'SIDE'.
    """
    if len(daily_closes) < 6:
        return 'SIDE'
    # Use 5 days BEFORE the last entry (last entry might be today/incomplete)
    last5 = daily_closes[-6:-1]
    up = sum(1 for j in range(1, len(last5)) if last5[j] > last5[j - 1])
    if up >= 4: return 'UP'
    if up <= 1: return 'DOWN'
    return 'SIDE'


def count_consec(daily_closes, direction):
    """Count consecutive days in given direction BEFORE today.
    daily_closes[-1] is today (or most recent) — skip it.
    Start counting from yesterday backwards.
    """
    if len(daily_closes) < 3:
        return 0
    cd = 0
    # Start from second-to-last (yesterday) going backwards
    for i in range(len(daily_closes) - 2, 0, -1):
        if direction == 'DOWN' and daily_closes[i] < daily_closes[i - 1]:
            cd += 1
        elif direction == 'UP' and daily_closes[i] > daily_closes[i - 1]:
            cd += 1
        else:
            break
    return cd


def compute_prev_day_stats(prev_day_bars):
    """Compute high, low, close, range, body ratio from previous day's bars."""
    if not prev_day_bars:
        return None
    ph = max(b['high'] for b in prev_day_bars)
    pl = min(b['low'] for b in prev_day_bars)
    pc = prev_day_bars[-1]['close']
    po = prev_day_bars[0]['open']
    rng = ph - pl
    if rng <= 0:
        return None
    return {
        'high': ph, 'low': pl, 'close': pc, 'open': po,
        'range': rng,
        'range_pct': rng / pc * 100,
        'body_ratio': abs(pc - po) / rng,
    }


def check_signal(sym, today_bars, prev_stats, trend, daily_closes):
    """Check if stock has a SHORT or LONG signal.
    Checks CAM first, then Pivot. Returns first match — no duplicates.
    Returns signal dict or None.
    """
    if len(today_bars) <= config.SCAN_BAR:
        return None
    if trend not in ('DOWN', 'UP'):
        return None
    if not prev_stats:
        return None

    rng = prev_stats['range']
    pc = prev_stats['close']

    if trend == 'DOWN':
        # SHORT filters (applied to both CAM and Pivot)
        cd = count_consec(daily_closes, 'DOWN')
        if cd > config.MAX_CONSEC_DOWN:
            return None
        if prev_stats['range_pct'] < config.MIN_YD_RANGE:
            return None
        if prev_stats['body_ratio'] < config.MIN_YD_BODY:
            return None

        # Level 1: Check CAM R3 first
        r3 = pc + rng * 1.1 / 4
        for j in range(1, config.SCAN_BAR + 1):
            atr = sum(today_bars[k]['high'] - today_bars[k]['low']
                      for k in range(max(0, j - 3), j + 1)) / min(4, j + 1)
            if abs(today_bars[j]['high'] - r3) < atr * 0.3 and today_bars[j]['close'] < r3:
                entry = today_bars[config.SCAN_BAR]['close']
                return {
                    'sym': sym, 'direction': 'SHORT', 'strategy': 'CAM_R3',
                    'entry': round(entry, 2),
                    'stop': round(entry * (1 + config.STOP / 100), 2),
                    'target': round(entry * (1 - config.TARGET / 100), 2),
                    'level': round(r3, 2),
                }

        # Level 2: No CAM signal → check Pivot R1
        pp = (prev_stats['high'] + prev_stats['low'] + pc) / 3
        r1 = 2 * pp - prev_stats['low']
        for j in range(1, config.SCAN_BAR + 1):
            atr = sum(today_bars[k]['high'] - today_bars[k]['low']
                      for k in range(max(0, j - 3), j + 1)) / min(4, j + 1)
            if abs(today_bars[j]['high'] - r1) < atr * 0.3 and today_bars[j]['close'] < r1:
                entry = today_bars[config.SCAN_BAR]['close']
                return {
                    'sym': sym, 'direction': 'SHORT', 'strategy': 'PIVOT_R1',
                    'entry': round(entry, 2),
                    'stop': round(entry * (1 + config.STOP / 100), 2),
                    'target': round(entry * (1 - 0.75 / 100), 2),  # Pivot uses 0.75% target
                    'level': round(r1, 2),
                }

    else:  # UP trend
        # LONG: apply yesterday filter
        if prev_stats['range_pct'] < config.MIN_YD_RANGE:
            return None
        if prev_stats['body_ratio'] < config.MIN_YD_BODY:
            return None

        # Level 1: Check CAM S3 first
        s3 = pc - rng * 1.1 / 4
        for j in range(1, config.SCAN_BAR + 1):
            atr = sum(today_bars[k]['high'] - today_bars[k]['low']
                      for k in range(max(0, j - 3), j + 1)) / min(4, j + 1)
            if abs(today_bars[j]['low'] - s3) < atr * 0.3 and today_bars[j]['close'] > s3:
                entry = today_bars[config.SCAN_BAR]['close']
                return {
                    'sym': sym, 'direction': 'LONG', 'strategy': 'CAM_S3',
                    'entry': round(entry, 2),
                    'stop': round(entry * (1 - config.STOP / 100), 2),
                    'target': round(entry * (1 + config.TARGET / 100), 2),
                    'level': round(s3, 2),
                }

        # Level 2: No CAM signal → check Pivot S1
        pp = (prev_stats['high'] + prev_stats['low'] + pc) / 3
        s1 = 2 * pp - prev_stats['high']
        for j in range(1, config.SCAN_BAR + 1):
            atr = sum(today_bars[k]['high'] - today_bars[k]['low']
                      for k in range(max(0, j - 3), j + 1)) / min(4, j + 1)
            if abs(today_bars[j]['low'] - s1) < atr * 0.3 and today_bars[j]['close'] > s1:
                entry = today_bars[config.SCAN_BAR]['close']
                return {
                    'sym': sym, 'direction': 'LONG', 'strategy': 'PIVOT_S1',
                    'entry': round(entry, 2),
                    'stop': round(entry * (1 - config.STOP / 100), 2),
                    'target': round(entry * (1 + 0.75 / 100), 2),  # Pivot uses 0.75% target
                    'level': round(s1, 2),
                }

    return None


def _ema(values, period):
    """Calculate EMA from a list of values."""
    if len(values) < period:
        return None
    mult = 2 / (period + 1)
    val = sum(values[:period]) / period
    for v in values[period:]:
        val = v * mult + val * (1 - mult)
    return val


def check_ma_convergence(sym, daily_closes, daily_volumes, today_bars, trend, bar_start, bar_end):
    """Check if SMA20 and EMA20 converge + volume condition → enter at MA zone.
    Two setups:
      A) MA spread < 0.2% + yesterday vol above avg (1.0-1.5x)
      B) MA spread < 0.5% + yesterday vol dry (< 0.6x)
    Target: 0.5% | Stop: 1.0%
    Returns signal dict or None.
    """
    if not today_bars or len(daily_closes) < 20 or len(daily_volumes) < 20:
        return None
    if trend not in ('DOWN', 'UP'):
        return None

    sma20 = sum(daily_closes[-20:]) / 20
    ema20 = _ema(daily_closes, 20)
    if not ema20:
        return None

    spread = abs(sma20 - ema20) / sma20 * 100
    vol_avg = sum(daily_volumes[-20:]) / 20
    vol_yd = daily_volumes[-1]
    vol_ratio = vol_yd / max(1, vol_avg)

    # Check setup A or B
    setup = None
    if spread < 0.2 and 1.0 <= vol_ratio < 1.5:
        setup = 'MA_CONV_A'
    elif spread < 0.5 and vol_ratio < 0.6:
        setup = 'MA_CONV_B'

    if not setup:
        return None

    zone = (sma20 + ema20) / 2

    for j in range(bar_start, min(bar_end, len(today_bars))):
        price = today_bars[j]['close']
        if abs(price - zone) / price * 100 > 1.0:
            continue

        if trend == 'DOWN' and price > zone:
            return {
                'sym': sym, 'direction': 'SHORT', 'strategy': setup,
                'entry': round(price, 2),
                'stop': round(price * (1 + 1.0 / 100), 2),
                'target': round(price * (1 - 0.5 / 100), 2),
                'level': round(zone, 2),
            }
        elif trend == 'UP' and price < zone:
            return {
                'sym': sym, 'direction': 'LONG', 'strategy': setup,
                'entry': round(price, 2),
                'stop': round(price * (1 - 1.0 / 100), 2),
                'target': round(price * (1 + 0.5 / 100), 2),
                'level': round(zone, 2),
            }

    return None


def check_gap_signal(sym, today_bars, prev_close):
    """Check if stock has a gap fill signal.
    Rules:
      1. Gap 2%+ from prev close
      2. First bar reverses by 0.5%+
      3. SHORT gap-up, LONG gap-down
      4. Target: 0.5% (then runner with 0.25% step)
      5. Stop: 1.0%
    Returns signal dict or None.
    """
    if not today_bars or not prev_close:
        return None

    today_open = today_bars[0]['open']
    gap = (today_open - prev_close) / prev_close * 100

    if abs(gap) < 2.0:
        return None

    # First bar reversal check
    fb_ret = (today_bars[0]['close'] - today_bars[0]['open']) / today_bars[0]['open'] * 100

    # Gap UP + first bar RED = SHORT
    if gap > 0 and fb_ret < -0.5:
        return {
            'sym': sym, 'direction': 'SHORT', 'strategy': 'GAP_FILL',
            'entry': round(today_open, 2),
            'stop': round(today_open * (1 + 1.0/100), 2),
            'target': round(today_open * (1 - 0.5/100), 2),
            'runner_step': 0.25,  # Trail with 0.25% step after target
            'level': round(prev_close, 2),
            'gap': round(gap, 2),
        }

    # Gap DOWN + first bar GREEN = LONG
    if gap < 0 and fb_ret > 0.5:
        return {
            'sym': sym, 'direction': 'LONG', 'strategy': 'GAP_FILL',
            'entry': round(today_open, 2),
            'stop': round(today_open * (1 - 1.0/100), 2),
            'target': round(today_open * (1 + 0.5/100), 2),
            'runner_step': 0.25,  # Trail with 0.25% step after target
            'level': round(prev_close, 2),
            'gap': round(gap, 2),
        }

    return None


def check_exit(signal, current_price, mfe, trail_active, target_hit):
    """Check if we should exit. Returns (action, exit_price, new_state) or None.
    action: 'target_hit', 'runner_stop', 'trail_stop', 'stop_loss', None
    """
    entry = signal['entry']
    direction = signal['direction']

    # Per-signal target and runner step (GAP uses 0.5%/0.25%, CAM uses 1.75%/0.25%)
    sig_target = abs(signal['entry'] - signal['target']) / signal['entry'] * 100
    sig_runner = signal.get('runner_step', config.RUNNER_STEP)

    if direction == 'SHORT':
        fav = (entry - current_price) / entry * 100
        adv = (current_price - entry) / entry * 100
    else:
        fav = (current_price - entry) / entry * 100
        adv = (entry - current_price) / entry * 100

    new_mfe = max(mfe, fav)
    new_trail = trail_active or new_mfe >= min(sig_target, config.TRAIL_ACTIVATE)
    new_target_hit = target_hit or new_mfe >= sig_target

    # Phase 3: Runner mode (after target hit)
    if new_target_hit:
        runner_stop_pct = new_mfe - sig_runner
        if runner_stop_pct > sig_target:
            runner_stop_pct = runner_stop_pct
        else:
            runner_stop_pct = sig_target

        if direction == 'SHORT':
            runner_stop_price = entry * (1 - runner_stop_pct / 100)
        else:
            runner_stop_price = entry * (1 + runner_stop_pct / 100)

        # Check if runner stop hit
        if direction == 'SHORT' and current_price >= runner_stop_price:
            return 'runner_stop', runner_stop_price, new_mfe, new_trail, new_target_hit
        if direction == 'LONG' and current_price <= runner_stop_price:
            return 'runner_stop', runner_stop_price, new_mfe, new_trail, new_target_hit

        return None, None, new_mfe, new_trail, new_target_hit

    # Phase 2: Trail active (before target)
    if new_trail:
        if direction == 'SHORT':
            lock_price = entry * (1 - config.TRAIL_LOCK / 100)
            if current_price >= lock_price:
                return 'trail_stop', lock_price, new_mfe, new_trail, new_target_hit
        else:
            lock_price = entry * (1 + config.TRAIL_LOCK / 100)
            if current_price <= lock_price:
                return 'trail_stop', lock_price, new_mfe, new_trail, new_target_hit

    # Phase 1: Normal stop
    if direction == 'SHORT' and current_price >= signal['stop']:
        return 'stop_loss', signal['stop'], new_mfe, new_trail, new_target_hit
    if direction == 'LONG' and current_price <= signal['stop']:
        return 'stop_loss', signal['stop'], new_mfe, new_trail, new_target_hit

    return None, None, new_mfe, new_trail, new_target_hit
