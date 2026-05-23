"""
BOTTOM-UP v2 — OPTIMIZED + 40 indicators.
Pre-indexes data by date (eliminates O(n²) bottleneck).
Computes every indicator known to intraday trading.
Finds profitable combos bottom-up, then stacks to highest WR.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.mega_strategies import *
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent

# ─── DATA LOADING (pre-index by date) ───
data_dir = Path('data/5min')
all_data = {}
date_bars = defaultdict(dict)   # date_bars[date][sym] = [bars]
prev_bars = defaultdict(dict)   # prev_bars[date][sym] = [all bars before date]
all_dates_set = set()

print('Loading and indexing data...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    # Pre-index by date
    by_date = defaultdict(list)
    for b in bars:
        d = b['timestamp'][:10]
        by_date[d].append(b)
        all_dates_set.add(d)
    for d, bs in by_date.items():
        date_bars[d][sym] = bs

all_dates = sorted(all_dates_set)

# Build prev_close and prev_day_bars indices
prev_close = {}  # (date, sym) -> prev close
prev_day_high = {}
prev_day_low = {}
prev_5d_vol = {}  # (date, sym) -> avg volume over last 5 scan-bar periods

for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i == 0: continue
        prev_d = dates_for_sym[i-1]
        prev_d_bars = date_bars[prev_d].get(sym, [])
        if prev_d_bars:
            prev_close[(d, sym)] = prev_d_bars[-1]['close']
            prev_day_high[(d, sym)] = max(b['high'] for b in prev_d_bars)
            prev_day_low[(d, sym)] = min(b['low'] for b in prev_d_bars)
        # 5-day avg volume (first 7 bars each day)
        vols = []
        for j in range(max(0, i-5), i):
            pd = dates_for_sym[j]
            pd_bars = date_bars[pd].get(sym, [])
            if len(pd_bars) > 6:
                vols.append(sum(b['volume'] for b in pd_bars[:7]))
        if vols:
            prev_5d_vol[(d, sym)] = sum(vols) / len(vols)

# Build prev_bars_all for ATR calculation (last 30 bars before date)
prev_bars_for_atr = {}  # (date, sym) -> last 30 bars
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    cum_bars = []
    for d in dates_for_sym:
        prev_bars_for_atr[(d, sym)] = cum_bars[-30:] if len(cum_bars) >= 5 else []
        cum_bars.extend(date_bars[d].get(sym, []))

# Load macro
macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

# Load delivery
delivery = {}
for f in Path('data/delivery').glob('*.csv'):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh:
        delivery[sym] = {r['date']: r for r in csv.DictReader(fh)}

vol_agent = VolatilityAgent()
EXIT_BAR = 69
SCAN_BAR = 6

# ─── INDICATOR FUNCTIONS ───

def calc_rsi(closes, period=14):
    """RSI with configurable period"""
    if len(closes) < period + 1: return 50
    gains, losses = [], []
    for i in range(len(closes)-period, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    ag = sum(gains) / period
    al = sum(losses) / period
    return 100 - 100/(1 + ag/al) if al > 0 else (100 if ag > 0 else 50)

def calc_stochastic(highs, lows, closes, k_period=7):
    """Stochastic %K"""
    if len(closes) < k_period: return 50
    hh = max(highs[-k_period:])
    ll = min(lows[-k_period:])
    return (closes[-1] - ll) / (hh - ll) * 100 if hh != ll else 50

def calc_williams_r(highs, lows, closes, period=7):
    """Williams %R"""
    if len(closes) < period: return -50
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    return (hh - closes[-1]) / (hh - ll) * -100 if hh != ll else -50

def calc_cci(highs, lows, closes, period=7):
    """Commodity Channel Index"""
    if len(closes) < period: return 0
    tps = [(highs[i]+lows[i]+closes[i])/3 for i in range(len(closes)-period, len(closes))]
    mean_tp = sum(tps) / period
    mean_dev = sum(abs(tp - mean_tp) for tp in tps) / period
    return (tps[-1] - mean_tp) / (0.015 * mean_dev) if mean_dev > 0 else 0

def calc_macd(closes):
    """MACD line and signal (using SMA approximation for short bars)"""
    if len(closes) < 5: return 0, 0
    fast = sum(closes[-3:]) / 3
    slow = sum(closes[-min(5, len(closes)):]) / min(5, len(closes))
    macd_line = fast - slow
    # Signal is just smoothed MACD (we only have few bars)
    return macd_line, macd_line * 0.8  # approximation

def calc_adx(highs, lows, closes, period=7):
    """ADX (trend strength)"""
    if len(closes) < period + 1: return 25
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(len(closes)-period, len(closes)):
        h_diff = highs[i] - highs[i-1]
        l_diff = lows[i-1] - lows[i]
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        tr_list.append(tr)
    atr = sum(tr_list) / period
    if atr == 0: return 25
    plus_di = sum(plus_dm) / atr * 100 / period
    minus_di = sum(minus_dm) / atr * 100 / period
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx

def calc_obv_slope(bars):
    """OBV direction over last N bars"""
    if len(bars) < 3: return 0
    obv = 0
    obvs = [0]
    for i in range(1, len(bars)):
        if bars[i]['close'] > bars[i-1]['close']:
            obv += bars[i]['volume']
        elif bars[i]['close'] < bars[i-1]['close']:
            obv -= bars[i]['volume']
        obvs.append(obv)
    # Slope: positive = accumulation, negative = distribution
    if len(obvs) >= 3:
        return 1 if obvs[-1] > obvs[-3] else (-1 if obvs[-1] < obvs[-3] else 0)
    return 0

def calc_mfi(highs, lows, closes, volumes, period=7):
    """Money Flow Index"""
    if len(closes) < period + 1: return 50
    pos_flow, neg_flow = 0, 0
    for i in range(len(closes)-period, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tp_prev = (highs[i-1] + lows[i-1] + closes[i-1]) / 3
        mf = tp * volumes[i]
        if tp > tp_prev: pos_flow += mf
        else: neg_flow += mf
    if neg_flow == 0: return 100
    ratio = pos_flow / neg_flow
    return 100 - 100 / (1 + ratio)

def calc_vwap_bands(bars):
    """VWAP and standard deviation bands"""
    tp_vol = sum((b['high']+b['low']+b['close'])/3 * b['volume'] for b in bars)
    cum_vol = sum(b['volume'] for b in bars)
    if cum_vol == 0: return bars[-1]['close'], 0
    vwap = tp_vol / cum_vol
    # Variance
    tp2_vol = sum(((b['high']+b['low']+b['close'])/3)**2 * b['volume'] for b in bars)
    variance = tp2_vol / cum_vol - vwap**2
    std = math.sqrt(max(0, variance))
    return vwap, std

def calc_keltner_position(closes, highs, lows, period=7, mult=1.5):
    """Position within Keltner Channel (0 = lower, 1 = upper)"""
    if len(closes) < period: return 0.5
    mid = sum(closes[-period:]) / period
    atr = sum(highs[i]-lows[i] for i in range(len(closes)-period, len(closes))) / period
    upper = mid + mult * atr
    lower = mid - mult * atr
    if upper == lower: return 0.5
    return (closes[-1] - lower) / (upper - lower)

def calc_bb_position(closes, period=7, mult=2):
    """Position within Bollinger Bands (0=lower, 1=upper)"""
    if len(closes) < period: return 0.5
    sma = sum(closes[-period:]) / period
    std = math.sqrt(sum((c - sma)**2 for c in closes[-period:]) / period)
    upper = sma + mult * std
    lower = sma - mult * std
    if upper == lower: return 0.5
    return (closes[-1] - lower) / (upper - lower)

def calc_atr_pct(bars):
    """ATR as % of price"""
    if not bars: return 0
    atr = sum(b['high']-b['low'] for b in bars) / len(bars)
    return atr / bars[-1]['close'] * 100 if bars[-1]['close'] > 0 else 0

def calc_range_expansion(bars):
    """Current bar range vs average range"""
    if len(bars) < 3: return 1
    avg_range = sum(b['high']-b['low'] for b in bars[:-1]) / (len(bars)-1)
    curr_range = bars[-1]['high'] - bars[-1]['low']
    return curr_range / avg_range if avg_range > 0 else 1

def calc_candle_pattern(bar):
    """Candle pattern score: doji=0, hammer=1, shooting_star=-1, marubozu=2/-2"""
    body = abs(bar['close'] - bar['open'])
    rng = bar['high'] - bar['low']
    if rng == 0: return 0
    body_ratio = body / rng
    upper_wick = bar['high'] - max(bar['open'], bar['close'])
    lower_wick = min(bar['open'], bar['close']) - bar['low']

    if body_ratio < 0.1: return 0  # doji
    if body_ratio > 0.8:  # marubozu
        return 2 if bar['close'] > bar['open'] else -2
    if lower_wick > body * 2 and upper_wick < body * 0.5:
        return 1  # hammer
    if upper_wick > body * 2 and lower_wick < body * 0.5:
        return -1  # shooting star
    return 0

def calc_pivot_distance(price, prev_high, prev_low, prev_close_val):
    """Distance from pivot point (%) and whether above/below"""
    pivot = (prev_high + prev_low + prev_close_val) / 3
    return (price - pivot) / pivot * 100 if pivot > 0 else 0

def calc_price_acceleration(closes):
    """Rate of change acceleration (2nd derivative)"""
    if len(closes) < 4: return 0
    roc1 = closes[-1] / closes[-2] - 1 if closes[-2] != 0 else 0
    roc2 = closes[-2] / closes[-3] - 1 if closes[-3] != 0 else 0
    return roc1 - roc2

def calc_volume_trend(bars):
    """Volume increasing (1), decreasing (-1), or flat (0) over last 3+ bars"""
    if len(bars) < 3: return 0
    vols = [b['volume'] for b in bars[-4:]]
    increasing = all(vols[i] >= vols[i-1] for i in range(1, len(vols)))
    decreasing = all(vols[i] <= vols[i-1] for i in range(1, len(vols)))
    if increasing: return 1
    if decreasing: return -1
    return 0

def calc_bar_position_in_range(bars):
    """Where current close sits in the day's range so far (0=low, 1=high)"""
    if not bars: return 0.5
    day_high = max(b['high'] for b in bars)
    day_low = min(b['low'] for b in bars)
    if day_high == day_low: return 0.5
    return (bars[-1]['close'] - day_low) / (day_high - day_low)

def calc_higher_lows(bars, direction):
    """Count of consecutive higher lows (LONG) or lower highs (SHORT)"""
    if len(bars) < 3: return 0
    count = 0
    for i in range(len(bars)-1, 1, -1):
        if direction == 'LONG':
            if bars[i]['low'] > bars[i-1]['low']: count += 1
            else: break
        else:
            if bars[i]['high'] < bars[i-1]['high']: count += 1
            else: break
    return count


# ─── MAIN SCAN ───
print(f'Scanning {len(all_dates)} days × {len(all_data)} stocks × 11 strategies...', flush=True)
results = []
t0 = time.time()
dates_done = 0

for date in all_dates:
    # Crowd count
    crowd = 0; total_syms = 0
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close.get((date, sym))
        if pc is None or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pc) / pc * 100
        if abs(gap) > 0.3: crowd += 1

    # Day of week
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: dow = -1

    # Macro
    mc = macro.get(date, {})
    vix = mc.get('india_vix', 0)
    us_ret = mc.get('sp500_overnight', 0)
    nifty_prev = mc.get('nifty_prev_return', 0)

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close.get((date, sym))
        if pc is None or len(db) <= SCAN_BAR: continue
        bsf = db[:SCAN_BAR+1]

        for scanner in [
            lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
            lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
            lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
            lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=4 else None,
            lambda: OpeningRangeBreakout.scan(sym, bsf, pc) if len(bsf)>=7 else None,
            lambda: PivotBreakout.scan(sym, bsf, pc) if len(bsf)>=4 else None,
            lambda: VWAPCrossSignal.scan(sym, bsf) if len(bsf)>=5 else None,
            lambda: EngulfingPattern.scan(sym, bsf) if len(bsf)>=3 else None,
            lambda: DayHighLowBreak.scan(sym, bsf) if len(bsf)>=5 else None,
            lambda: MomentumBurst.scan(sym, bsf) if len(bsf)>=6 else None,
            lambda: HammerShootingStar.scan(sym, bsf) if len(bsf)>=5 else None,
        ]:
            try: sig = scanner()
            except: continue
            if not sig: continue
            direction = sig.direction

            closes = [b['close'] for b in bsf]
            highs = [b['high'] for b in bsf]
            lows = [b['low'] for b in bsf]
            volumes = [b['volume'] for b in bsf]
            price = closes[-1]

            # ═══ 40 INDICATORS ═══

            # Momentum
            i_rsi = calc_rsi(closes)
            i_stoch = calc_stochastic(highs, lows, closes)
            i_williams = calc_williams_r(highs, lows, closes)
            i_cci = calc_cci(highs, lows, closes)
            macd_line, macd_signal = calc_macd(closes)
            i_macd = macd_line - macd_signal  # MACD histogram
            i_roc = (closes[-1] / closes[0] - 1) * 100 if closes[0] != 0 else 0  # Rate of change
            i_accel = calc_price_acceleration(closes)

            # Trend
            i_adx = calc_adx(highs, lows, closes)
            i_ema_spread = (sum(closes[-3:])/3 - sum(closes[-min(5,len(closes)):])/min(5,len(closes))) / (sum(closes[-min(5,len(closes)):])/min(5,len(closes))) * 100 if len(closes) >= 5 else 0
            i_higher_lows = calc_higher_lows(bsf, direction)

            # Volatility
            i_atr_pct = calc_atr_pct(bsf)
            i_bb_pos = calc_bb_position(closes)
            i_keltner = calc_keltner_position(closes, highs, lows)
            i_range_exp = calc_range_expansion(bsf)
            i_noise = sum(b['high']-b['low'] for b in bsf[-min(5,len(bsf)):]) / (abs(closes[-1]-closes[max(0,len(closes)-5)]) + 0.001)

            # Volume
            avg_vol = prev_5d_vol.get((date, sym), 0)
            today_vol = sum(volumes)
            i_rvol = today_vol / avg_vol if avg_vol > 0 else 1
            i_obv_slope = calc_obv_slope(bsf)
            i_mfi = calc_mfi(highs, lows, closes, volumes)
            i_vol_trend = calc_volume_trend(bsf)

            # Price Action
            i_body_ratio = abs(bsf[-1]['close']-bsf[-1]['open']) / (bsf[-1]['high']-bsf[-1]['low']) * 100 if bsf[-1]['high'] != bsf[-1]['low'] else 0
            i_candle = calc_candle_pattern(bsf[-1])
            i_bar_pos = calc_bar_position_in_range(bsf)
            vwap, vwap_std = calc_vwap_bands(bsf)
            i_vwap_dist = (price - vwap) / vwap * 100 if vwap > 0 else 0
            i_vwap_band = (price - vwap) / vwap_std if vwap_std > 0 else 0  # Distance in std devs

            # Gap & Open
            i_gap = (bsf[0]['open'] - pc) / pc * 100
            i_gap_aligned = 1 if (direction=='LONG' and i_gap > 0) or (direction=='SHORT' and i_gap < 0) else 0
            i_morning_move = (price - bsf[0]['open']) / bsf[0]['open'] * 100
            i_morning_aligned = 1 if (direction=='LONG' and i_morning_move > 0) or (direction=='SHORT' and i_morning_move < 0) else 0

            # Consecutive bars in direction
            consec = 0
            for k in range(len(bsf)-1, 0, -1):
                bar_green = bsf[k]['close'] > bsf[k]['open']
                if (direction=='LONG' and bar_green) or (direction=='SHORT' and not bar_green): consec += 1
                else: break
            i_consec = consec

            # Previous day context
            pdh = prev_day_high.get((date, sym), price)
            pdl = prev_day_low.get((date, sym), price)
            i_vs_pdh = (price - pdh) / pdh * 100
            i_vs_pdl = (price - pdl) / pdl * 100
            i_pivot_dist = calc_pivot_distance(price, pdh, pdl, pc)

            # Macro
            i_vix = vix
            i_us_ret = us_ret
            i_nifty_prev = nifty_prev
            i_dow = dow

            # Delivery
            del_data = delivery.get(sym, {})
            recent_del = sorted(d for d in del_data if d < date)
            i_del_pct = 0
            if recent_del:
                try: i_del_pct = float(del_data[recent_del[-1]].get('delivery_pct', 0) or 0)
                except: pass

            # Direction-adjusted indicators (positive = confirming direction)
            dir_mult = 1 if direction == 'LONG' else -1
            i_rsi_adj = (i_rsi - 50) * dir_mult  # +ve = RSI confirms direction
            i_macd_adj = i_macd * dir_mult
            i_vwap_adj = i_vwap_dist * dir_mult  # +ve = price on right side of VWAP
            i_gap_adj = i_gap * dir_mult
            i_morning_adj = i_morning_move * dir_mult

            # ═══ SIMULATE TRADE ═══
            entry = sig.suggested_entry
            atr_bars = prev_bars_for_atr.get((date, sym), [])
            atr_val = vol_agent.calculate_atr(atr_bars) if len(atr_bars) >= 5 else 0
            stop, _ = SmartStopCalculator.calculate(db, SCAN_BAR, entry, direction, atr_val)
            target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)
            exit_price = None
            for j in range(SCAN_BAR+1, min(len(db), EXIT_BAR+1)):
                if direction == 'LONG':
                    if db[j]['low'] <= stop: exit_price = stop; break
                    if db[j]['high'] >= target: exit_price = target; break
                else:
                    if db[j]['high'] >= stop: exit_price = stop; break
                    if db[j]['low'] <= target: exit_price = target; break
            if not exit_price:
                exit_price = db[min(EXIT_BAR, len(db)-1)]['close']
            pnl = (exit_price-entry)/entry*100 if direction == 'LONG' else (entry-exit_price)/entry*100

            results.append({
                'date': date, 'sym': sym, 'dir': direction, 'strat': sig.strategy_name,
                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                # Momentum (8)
                'rsi': round(i_rsi, 1), 'stoch': round(i_stoch, 1),
                'williams': round(i_williams, 1), 'cci': round(i_cci, 1),
                'macd': round(i_macd, 4), 'roc': round(i_roc, 3),
                'accel': round(i_accel, 5), 'mfi': round(i_mfi, 1),
                # Trend (3)
                'adx': round(i_adx, 1), 'ema_spread': round(i_ema_spread, 3),
                'higher_lows': i_higher_lows,
                # Volatility (5)
                'atr_pct': round(i_atr_pct, 3), 'bb_pos': round(i_bb_pos, 3),
                'keltner': round(i_keltner, 3), 'range_exp': round(i_range_exp, 2),
                'noise': round(i_noise, 2),
                # Volume (4)
                'rvol': round(i_rvol, 2), 'obv_slope': i_obv_slope,
                'vol_trend': i_vol_trend,
                # Price Action (5)
                'body_ratio': round(i_body_ratio, 1), 'candle': i_candle,
                'bar_pos': round(i_bar_pos, 3), 'vwap_dist': round(i_vwap_dist, 3),
                'vwap_band': round(i_vwap_band, 2),
                # Gap & Morning (5)
                'gap': round(i_gap, 3), 'gap_aligned': i_gap_aligned,
                'morning_move': round(i_morning_move, 3),
                'morning_aligned': i_morning_aligned, 'consec': i_consec,
                # Previous Day (3)
                'vs_pdh': round(i_vs_pdh, 2), 'vs_pdl': round(i_vs_pdl, 2),
                'pivot_dist': round(i_pivot_dist, 3),
                # Macro (4)
                'vix': round(i_vix, 1), 'us_ret': round(i_us_ret, 2),
                'nifty_prev': round(i_nifty_prev, 2), 'dow': i_dow,
                # Delivery (1)
                'del_pct': round(i_del_pct, 1),
                # Direction-adjusted (5)
                'rsi_adj': round(i_rsi_adj, 1), 'macd_adj': round(i_macd_adj, 4),
                'vwap_adj': round(i_vwap_adj, 3), 'gap_adj': round(i_gap_adj, 3),
                'morning_adj': round(i_morning_adj, 3),
            })

    dates_done += 1
    if dates_done % 10 == 0:
        elapsed = time.time() - t0
        rate = dates_done / elapsed
        remaining = (len(all_dates) - dates_done) / rate
        print(f'  {dates_done}/{len(all_dates)} days ({dates_done/len(all_dates)*100:.0f}%) | {len(results)} signals | ETA {remaining:.0f}s', flush=True)

total_w = sum(r['win'] for r in results)
print(f'\nTotal: {len(results)} signals, {total_w} winners ({total_w/len(results)*100:.0f}% WR)')
print(f'Time: {time.time()-t0:.1f}s')

# ─── ANALYSIS 1: Single indicator ranges ───
print('\n' + '='*80)
print('SINGLE INDICATOR ANALYSIS: Which ranges produce highest WR?')
print('='*80)

# All indicator names (excluding metadata)
skip = {'date','sym','dir','strat','pnl','win'}
ind_names = [k for k in results[0].keys() if k not in skip]

best_single_filters = []

for ind in ind_names:
    vals = sorted(set(r[ind] for r in results))
    if len(vals) < 3: continue  # binary or constant

    # For continuous: split into 5 equal-frequency buckets
    all_vals = sorted(r[ind] for r in results)
    n = len(all_vals)
    boundaries = [all_vals[int(n*i/5)] for i in range(5)] + [all_vals[-1] + 0.001]

    print(f'\n{ind}:')
    for i in range(5):
        lo, hi = boundaries[i], boundaries[i+1]
        bucket = [r for r in results if lo <= r[ind] < hi]
        if len(bucket) < 10: continue
        w = sum(r['win'] for r in bucket)
        wr = w / len(bucket) * 100
        avg = sum(r['pnl'] for r in bucket) / len(bucket)
        marker = ' <<<' if wr >= 50 else ''
        print(f'  [{lo:>8.2f} - {hi:>8.2f}): {len(bucket):>5} sigs, WR={wr:>4.0f}%, avg={avg:>+7.3f}%{marker}')
        if wr >= 50:
            best_single_filters.append({'ind': ind, 'lo': lo, 'hi': hi, 'n': len(bucket), 'wr': wr, 'avg': avg})

print(f'\n\nBest single-indicator filters (WR >= 50%):')
best_single_filters.sort(key=lambda x: (-x['wr'], -x['n']))
for f in best_single_filters[:20]:
    print(f"  {f['ind']:>15} [{f['lo']:>8.2f} - {f['hi']:>8.2f}): {f['n']:>4} trades, WR={f['wr']:.0f}%, avg={f['avg']:+.3f}%")

# ─── ANALYSIS 2: Best 2-indicator combos ───
print('\n' + '='*80)
print('2-INDICATOR COMBOS (WR >= 50%, min 15 trades):')
print('='*80)

# Use the best single filters to build combos
top_filters = [f for f in best_single_filters if f['wr'] >= 48]  # slightly looser for combos

best_combos = []
for i in range(len(top_filters)):
    for j in range(i+1, len(top_filters)):
        f1, f2 = top_filters[i], top_filters[j]
        if f1['ind'] == f2['ind']: continue
        sub = [r for r in results if f1['lo'] <= r[f1['ind']] < f1['hi'] and f2['lo'] <= r[f2['ind']] < f2['hi']]
        if len(sub) < 15: continue
        w = sum(r['win'] for r in sub)
        wr = w / len(sub) * 100
        if wr < 50: continue
        avg = sum(r['pnl'] for r in sub) / len(sub)
        total = sum(r['pnl'] for r in sub)
        days = len(set(r['date'] for r in sub))
        best_combos.append({
            'name': f"{f1['ind']}[{f1['lo']:.1f}-{f1['hi']:.1f}) + {f2['ind']}[{f2['lo']:.1f}-{f2['hi']:.1f})",
            'n': len(sub), 'w': w, 'wr': wr, 'avg': avg, 'total': total, 'days': days
        })

best_combos.sort(key=lambda x: (-x['wr'], -x['n']))
print(f'Found {len(best_combos)} combos with WR >= 50%')
print(f"\n{'Combo':>60} {'N':>4} {'W':>3} {'WR':>5} {'Days':>5} {'Avg':>7} {'Total':>8}")
print('-'*100)
for c in best_combos[:40]:
    marker = ' ***' if c['wr'] >= 70 else (' <<' if c['wr'] >= 60 else '')
    print(f"{c['name']:>60} {c['n']:>4} {c['w']:>3} {c['wr']:>4.0f}% {c['days']:>5} {c['avg']:>+6.3f}% {c['total']:>+7.2f}%{marker}")

# ─── ANALYSIS 3: Stack 3 best filters ───
print('\n' + '='*80)
print('3-INDICATOR STACKS (WR >= 60%, min 10 trades):')
print('='*80)

# Take top 15 single filters for 3-way combos
top15 = [f for f in best_single_filters[:15]]
best_3 = []
for i in range(len(top15)):
    for j in range(i+1, len(top15)):
        for k in range(j+1, len(top15)):
            f1, f2, f3 = top15[i], top15[j], top15[k]
            if len(set([f1['ind'], f2['ind'], f3['ind']])) < 3: continue
            sub = [r for r in results
                   if f1['lo'] <= r[f1['ind']] < f1['hi']
                   and f2['lo'] <= r[f2['ind']] < f2['hi']
                   and f3['lo'] <= r[f3['ind']] < f3['hi']]
            if len(sub) < 10: continue
            w = sum(r['win'] for r in sub)
            wr = w / len(sub) * 100
            if wr < 60: continue
            avg = sum(r['pnl'] for r in sub) / len(sub)
            total = sum(r['pnl'] for r in sub)
            days = len(set(r['date'] for r in sub))
            best_3.append({
                'name': f"{f1['ind']}+{f2['ind']}+{f3['ind']}",
                'ranges': f"[{f1['lo']:.1f}-{f1['hi']:.1f})+[{f2['lo']:.1f}-{f2['hi']:.1f})+[{f3['lo']:.1f}-{f3['hi']:.1f})",
                'n': len(sub), 'w': w, 'wr': wr, 'avg': avg, 'total': total, 'days': days
            })

best_3.sort(key=lambda x: (-x['wr'], -x['n']))
print(f'Found {len(best_3)} 3-indicator stacks with WR >= 60%')
for c in best_3[:25]:
    marker = ' ***' if c['wr'] >= 80 else (' <<' if c['wr'] >= 70 else '')
    print(f"  {c['name']:>35} {c['ranges']:>50} | {c['n']:>3} trades, WR={c['wr']:.0f}%, days={c['days']}, avg={c['avg']:+.3f}%{marker}")

# ─── ANALYSIS 4: What do the WINNERS share? ───
print('\n' + '='*80)
print('WINNER vs LOSER PROFILE: Mean values')
print('='*80)
winners = [r for r in results if r['win']]
losers = [r for r in results if not r['win']]
print(f"{'Indicator':>15} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Edge':>6}")
print('-'*55)
for ind in ind_names:
    w_mean = sum(r[ind] for r in winners) / len(winners) if winners else 0
    l_mean = sum(r[ind] for r in losers) / len(losers) if losers else 0
    delta = w_mean - l_mean
    # Normalize delta by range
    all_v = [r[ind] for r in results]
    rng = max(all_v) - min(all_v)
    edge = delta / rng * 100 if rng > 0 else 0
    if abs(edge) > 5:  # Only show meaningful differences
        print(f"  {ind:>15} {w_mean:>10.3f} {l_mean:>10.3f} {delta:>+10.3f} {edge:>+5.1f}%")

# Save raw results for further analysis
out_path = Path('data/bottom_up_results.json')
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2)
print(f'\nSaved {len(results)} signals with 40 indicators to {out_path}')
