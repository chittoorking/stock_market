"""Strategy logic — pure functions, no API calls. Same as backtest."""
import logging
from collections import defaultdict
from . import config

log = logging.getLogger('strategy')


def compute_daily_trend(daily_closes):
    """Compute 5-day trend from list of daily closes.
    daily_closes is guaranteed to contain only completed days (today excluded by trader).
    Uses last 5 entries — same as the proven backtest (dc[-5:]).
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
    """Double convergence: price MA + volume MA both converging + volume spike.
    Proven setup: 65% WR, profitable after charges over 4 years.
    Filters:
      1) Price spread (SMA20 vs EMA20) < 0.3%
      2) Volume spread (vol SMA20 vs vol EMA20) < 15%
      3) Yesterday volume 1.5-2.5x above 20-day avg
      4) Price within 0.5% of MA zone
    Target: 0.5% | Stop: 0.75%
    Returns signal dict or None.
    """
    if not today_bars or len(daily_closes) < 20 or len(daily_volumes) < 20:
        return None
    if trend not in ('DOWN', 'UP'):
        return None

    # Price convergence: SMA20 ~ EMA20
    sma20 = sum(daily_closes[-20:]) / 20
    ema20 = _ema(daily_closes, 20)
    if not ema20:
        return None
    price_spread = abs(sma20 - ema20) / sma20 * 100
    if price_spread >= 0.3:
        return None

    # Volume convergence: vol SMA20 ~ vol EMA20
    vol_sma20 = sum(daily_volumes[-20:]) / 20
    vol_ema20 = _ema(daily_volumes, 20)
    if not vol_ema20 or vol_sma20 <= 0:
        return None
    vol_spread = abs(vol_sma20 - vol_ema20) / vol_sma20 * 100
    if vol_spread >= 15:
        return None

    # Yesterday volume spike: 1.5-2.5x above average
    vol_yd = daily_volumes[-1]
    vol_ratio = vol_yd / max(1, vol_sma20)
    if not (1.5 <= vol_ratio < 2.5):
        return None

    zone = (sma20 + ema20) / 2

    for j in range(bar_start, min(bar_end, len(today_bars))):
        price = today_bars[j]['close']
        if abs(price - zone) / price * 100 > 0.5:
            continue

        if trend == 'DOWN' and price > zone:
            return {
                'sym': sym, 'direction': 'SHORT', 'strategy': 'MA_DCONV',
                'entry': round(price, 2),
                'stop': round(price * (1 + 0.75 / 100), 2),
                'target': round(price * (1 - 0.5 / 100), 2),
                'level': round(zone, 2),
            }
        elif trend == 'UP' and price < zone:
            return {
                'sym': sym, 'direction': 'LONG', 'strategy': 'MA_DCONV',
                'entry': round(price, 2),
                'stop': round(price * (1 - 0.75 / 100), 2),
                'target': round(price * (1 + 0.5 / 100), 2),
                'level': round(zone, 2),
            }

    return None


def check_gap_signal(sym, today_bars, prev_close, prev_high=None, prev_low=None):
    """Check if stock has a gap/range fill signal.
    Two triggers (either one fires):
      A) GAP FILL: open 1%+ from prev close + first bar reverses 0.5%+
      B) OUTSIDE RANGE: open above yd high or below yd low + first bar reverses 0.3%+
    Both have ~100% WR over 4 years. Combined: 4391 trades, Rs 41L.
    Returns signal dict or None.
    """
    if not today_bars:
        return None

    today_open = today_bars[0]['open']
    if today_open <= 0:
        return None

    fb_ret = (today_bars[0]['close'] - today_bars[0]['open']) / today_bars[0]['open'] * 100

    # Check A: GAP FILL (gap 1%+ from prev close)
    gap = (today_open - prev_close) / prev_close * 100 if prev_close else 0
    is_gap = abs(gap) >= 1.0

    # Check B: OUTSIDE RANGE (open beyond yesterday's high/low)
    is_outside_high = prev_high and today_open > prev_high
    is_outside_low = prev_low and today_open < prev_low

    # Need at least one trigger
    if not is_gap and not is_outside_high and not is_outside_low:
        return None

    # SHORT: gapped up OR opened above yesterday's high
    if (is_gap and gap > 0) or is_outside_high:
        # Require first bar reversal (RED candle)
        fb_threshold = -0.5 if is_gap else -0.3
        if fb_ret >= fb_threshold:
            return None
        return {
            'sym': sym, 'direction': 'SHORT', 'strategy': 'RANGE_FILL',
            'entry': round(today_open, 2),
            'stop': round(today_open * (1 + 1.0/100), 2),
            'target': round(today_open * (1 - 0.5/100), 2),
            'runner_step': 0.10,
            'trail_trigger': 0.25,
            'trail_lock': 0.10,
            'level': round(prev_close or prev_high, 2),
            'gap': round(gap, 2),
        }

    # LONG: gapped down OR opened below yesterday's low
    if (is_gap and gap < 0) or is_outside_low:
        fb_threshold = 0.5 if is_gap else 0.3
        if fb_ret <= fb_threshold:
            return None
        return {
            'sym': sym, 'direction': 'LONG', 'strategy': 'RANGE_FILL',
            'entry': round(today_open, 2),
            'stop': round(today_open * (1 - 1.0/100), 2),
            'target': round(today_open * (1 + 0.5/100), 2),
            'runner_step': 0.10,
            'trail_trigger': 0.25,
            'trail_lock': 0.10,
            'level': round(prev_close or prev_low, 2),
            'gap': round(gap, 2),
        }

    return None


def check_lunch_gap(sym, today_bars, lunch_bar=45, reopen_bar=48):
    """Lunch Gap Fill — mini gap forms during lunch, fills at 1:15 PM.
    100% WR at perfect entry, 98.6% at 60s slippage over 4 years.
    Rules:
      1. Gap 0.3%+ between bar 45 close and bar 48 open
      2. First bar reversal >= 0.2% (strong reversal = 100% WR)
      3. Target 0.2%, Stop 0.3%, Runner step 0.05%
    """
    if not today_bars or len(today_bars) <= reopen_bar:
        return None

    lunch_close = today_bars[lunch_bar]['close']
    reopen_price = today_bars[reopen_bar]['open']
    if lunch_close <= 0 or reopen_price <= 0:
        return None

    mini_gap = (reopen_price - lunch_close) / lunch_close * 100
    if abs(mini_gap) < 0.3:
        return None

    fb = (today_bars[reopen_bar]['close'] - today_bars[reopen_bar]['open']) / today_bars[reopen_bar]['open'] * 100

    if mini_gap > 0 and fb < -0.2:
        return {
            'sym': sym, 'direction': 'SHORT', 'strategy': 'LUNCH_GAP',
            'entry': round(reopen_price, 2),
            'stop': round(reopen_price * (1 + 0.3/100), 2),
            'target': round(reopen_price * (1 - 0.2/100), 2),
            'runner_step': 0.05,
            'trail_trigger': 0.10,
            'trail_lock': 0.10,
            'level': round(lunch_close, 2),
            'gap': round(mini_gap, 2),
        }
    elif mini_gap < 0 and fb > 0.2:
        return {
            'sym': sym, 'direction': 'LONG', 'strategy': 'LUNCH_GAP',
            'entry': round(reopen_price, 2),
            'stop': round(reopen_price * (1 - 0.3/100), 2),
            'target': round(reopen_price * (1 + 0.2/100), 2),
            'runner_step': 0.05,
            'trail_trigger': 0.10,
            'trail_lock': 0.10,
            'level': round(lunch_close, 2),
            'gap': round(mini_gap, 2),
        }

    return None


def check_chain_gap_ltp(sym, prev_bar_close, ltp_at_open, ltp_after_10s):
    """10-second chain entry — LTP-based, no bar close needed.
    1. Gap: prev 5-min bar close vs LTP at bar open (>= 0.3%)
    2. Reversal: LTP at +10s moved 0.05%+ back toward the gap close
    3. Entry: LTP at +10s (real price, achievable)
    Returns signal dict or None.
    """
    if prev_bar_close <= 0 or ltp_at_open <= 0 or ltp_after_10s <= 0:
        return None

    mini_gap = (ltp_at_open - prev_bar_close) / prev_bar_close * 100
    if abs(mini_gap) < 0.3:
        return None

    # Reversal check: did LTP move back toward prev_bar_close?
    fb = (ltp_after_10s - ltp_at_open) / ltp_at_open * 100

    if mini_gap > 0 and fb <= -0.05:
        # Gap UP, price dropping back = SHORT
        entry = round(ltp_after_10s, 2)
        return {
            'sym': sym, 'direction': 'SHORT', 'strategy': 'CHAIN_10S',
            'entry': entry,
            'stop': round(entry * (1 + 0.3/100), 2),
            'target': round(entry * (1 - 0.2/100), 2),
            'runner_step': 0.05,
            'trail_trigger': 0.10,
            'trail_lock': 0.10,
            'level': round(prev_bar_close, 2),
            'gap': round(mini_gap, 2),
        }
    elif mini_gap < 0 and fb >= 0.05:
        # Gap DOWN, price rising back = LONG
        entry = round(ltp_after_10s, 2)
        return {
            'sym': sym, 'direction': 'LONG', 'strategy': 'CHAIN_10S',
            'entry': entry,
            'stop': round(entry * (1 - 0.3/100), 2),
            'target': round(entry * (1 + 0.2/100), 2),
            'runner_step': 0.05,
            'trail_trigger': 0.10,
            'trail_lock': 0.10,
            'level': round(prev_bar_close, 2),
            'gap': round(mini_gap, 2),
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
    sig_trail_trigger = signal.get('trail_trigger', config.TRAIL_ACTIVATE)
    sig_trail_lock = signal.get('trail_lock', config.TRAIL_LOCK)

    if direction == 'SHORT':
        fav = (entry - current_price) / entry * 100
        adv = (current_price - entry) / entry * 100
    else:
        fav = (current_price - entry) / entry * 100
        adv = (entry - current_price) / entry * 100

    new_mfe = max(mfe, fav)
    new_trail = trail_active or new_mfe >= sig_trail_trigger
    new_target_hit = target_hit or new_mfe >= sig_target

    # Phase 3: Runner mode (after target hit)
    if new_target_hit:
        runner_stop_pct = max(new_mfe - sig_runner, sig_target)

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
            lock_price = entry * (1 - sig_trail_lock / 100)
            if current_price >= lock_price:
                return 'trail_stop', lock_price, new_mfe, new_trail, new_target_hit
        else:
            lock_price = entry * (1 + sig_trail_lock / 100)
            if current_price <= lock_price:
                return 'trail_stop', lock_price, new_mfe, new_trail, new_target_hit

    # Phase 1: Normal stop
    if direction == 'SHORT' and current_price >= signal['stop']:
        return 'stop_loss', signal['stop'], new_mfe, new_trail, new_target_hit
    if direction == 'LONG' and current_price <= signal['stop']:
        return 'stop_loss', signal['stop'], new_mfe, new_trail, new_target_hit

    return None, None, new_mfe, new_trail, new_target_hit
