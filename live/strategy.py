"""Strategy logic — pure functions, no API calls. Same as backtest."""
import logging
from collections import defaultdict
from . import config

log = logging.getLogger('strategy')


def compute_daily_trend(daily_closes):
    """Compute 5-day trend from list of daily closes.
    Returns 'UP', 'DOWN', or 'SIDE'.
    """
    if len(daily_closes) < 5:
        return 'SIDE'
    last5 = daily_closes[-5:]
    up = sum(1 for j in range(1, len(last5)) if last5[j] > last5[j - 1])
    if up >= 4: return 'UP'
    if up <= 1: return 'DOWN'
    return 'SIDE'


def count_consec(daily_closes, direction):
    """Count consecutive days in given direction from most recent."""
    if len(daily_closes) < 2:
        return 0
    cd = 0
    for i in range(len(daily_closes) - 1, 0, -1):
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
        # SHORT filters
        cd = count_consec(daily_closes, 'DOWN')
        if cd > config.MAX_CONSEC_DOWN:
            return None
        if prev_stats['range_pct'] < config.MIN_YD_RANGE:
            return None
        if prev_stats['body_ratio'] < config.MIN_YD_BODY:
            return None

        # Check R3 touch
        r3 = pc + rng * 1.1 / 4
        for j in range(1, config.SCAN_BAR + 1):
            atr = sum(today_bars[k]['high'] - today_bars[k]['low']
                      for k in range(max(0, j - 3), j + 1)) / min(4, j + 1)
            if abs(today_bars[j]['high'] - r3) < atr * 0.3 and today_bars[j]['close'] < r3:
                entry = today_bars[config.SCAN_BAR]['close']
                return {
                    'sym': sym, 'direction': 'SHORT',
                    'entry': round(entry, 2),
                    'stop': round(entry * (1 + config.STOP / 100), 2),
                    'target': round(entry * (1 - config.TARGET / 100), 2),
                    'level': round(r3, 2),
                }

    else:  # UP trend
        # LONG: no CD/yesterday filter
        s3 = pc - rng * 1.1 / 4
        for j in range(1, config.SCAN_BAR + 1):
            atr = sum(today_bars[k]['high'] - today_bars[k]['low']
                      for k in range(max(0, j - 3), j + 1)) / min(4, j + 1)
            if abs(today_bars[j]['low'] - s3) < atr * 0.3 and today_bars[j]['close'] > s3:
                entry = today_bars[config.SCAN_BAR]['close']
                return {
                    'sym': sym, 'direction': 'LONG',
                    'entry': round(entry, 2),
                    'stop': round(entry * (1 - config.STOP / 100), 2),
                    'target': round(entry * (1 + config.TARGET / 100), 2),
                    'level': round(s3, 2),
                }

    return None


def check_exit(signal, current_price, mfe, trail_active, target_hit):
    """Check if we should exit. Returns (action, exit_price, new_state) or None.
    action: 'target_hit', 'runner_stop', 'trail_stop', 'stop_loss', None
    """
    entry = signal['entry']
    direction = signal['direction']

    if direction == 'SHORT':
        fav = (entry - current_price) / entry * 100
        adv = (current_price - entry) / entry * 100
    else:
        fav = (current_price - entry) / entry * 100
        adv = (entry - current_price) / entry * 100

    new_mfe = max(mfe, fav)
    new_trail = trail_active or new_mfe >= config.TRAIL_ACTIVATE
    new_target_hit = target_hit or new_mfe >= config.TARGET

    # Phase 3: Runner mode (after target hit)
    if new_target_hit:
        runner_stop_pct = new_mfe - config.RUNNER_STEP
        if runner_stop_pct > config.TARGET:
            runner_stop_pct = runner_stop_pct
        else:
            runner_stop_pct = config.TARGET

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
