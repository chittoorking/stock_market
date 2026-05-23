"""
EXHAUSTIVE SCANNER — leave no stone unturned.
Tests EVERY combination of:
- 5 scan bars (3, 6, 10, 15, 20)
- 5 R:R ratios (1.0, 1.5, 2.0, 2.5, 3.0)
- All 15 strategies (was only using 11)
- Multiple exit times (1PM, 2PM, 2:30PM, 3PM, 3:15PM)
- Per-stock predictability
- Multi-strategy confirmation
- 50+ indicators including Ichimoku, Parabolic SAR, Donchian, Fibonacci
- Exhaustive raw indicator grid search

Phase 1: Generate ALL signals with ALL indicators across ALL scan bars
Phase 2: Test every exit/R:R combo
Phase 3: Find best filter stacks
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.mega_strategies import *
from app.agents.coded_moe import score_volume, score_price, score_momentum
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer

# ─── DATA LOADING ───
data_dir = Path('data/5min')
all_data = {}
date_bars = defaultdict(dict)

print('Loading and indexing data...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items():
        date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
print(f'{len(all_data)} stocks, {len(all_dates)} days')

# Pre-compute
prev_close_map = {}
prev_day_high_map = {}
prev_day_low_map = {}
prev_5d_vol = {}
prev_bars_for_atr = {}

for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    cum_bars = []
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            prev_d = dates_for_sym[i-1]
            prev_d_bars = date_bars[prev_d].get(sym, [])
            if prev_d_bars:
                prev_close_map[(d, sym)] = prev_d_bars[-1]['close']
                prev_day_high_map[(d, sym)] = max(b['high'] for b in prev_d_bars)
                prev_day_low_map[(d, sym)] = min(b['low'] for b in prev_d_bars)
            vols = []
            for j in range(max(0, i-5), i):
                pd = dates_for_sym[j]
                pd_bars = date_bars[pd].get(sym, [])
                if len(pd_bars) > 6:
                    vols.append(sum(b['volume'] for b in pd_bars[:7]))
            if vols:
                prev_5d_vol[(d, sym)] = sum(vols)/len(vols)
        prev_bars_for_atr[(d, sym)] = cum_bars[-30:] if len(cum_bars) >= 5 else []
        cum_bars.extend(date_bars[d].get(sym, []))

# Pre-compute sector/market/macro per date per scan_bar
macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

delivery = {}
for f in Path('data/delivery').glob('*.csv'):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh:
        delivery[sym] = {r['date']: r for r in csv.DictReader(fh)}

vol_agent = VolatilityAgent()

# Pre-compute sector data per (date, scan_bar)
SCAN_BARS = [3, 6, 10, 15, 20]
sector_cache = {}
market_cache = {}
print('Pre-computing sector/market for all scan bars...', flush=True)
for scan_bar in SCAN_BARS:
    for date in all_dates:
        sector_cache[(date, scan_bar)] = SectorAnalyzer.compute(all_data, date, scan_bar)
        # Market breadth
        up = 0; down = 0; total = 0
        for sym in date_bars[date]:
            db = date_bars[date][sym]
            if len(db) <= scan_bar: continue
            total += 1
            move = (db[scan_bar]['close'] - db[0]['open']) / db[0]['open'] * 100
            if move > 0.15: up += 1
            elif move < -0.15: down += 1
        market_cache[(date, scan_bar)] = (up, down, total)
print('Pre-computation done.', flush=True)


def fast_score_sector(symbol, date, scan_bar, direction):
    score = 5.0
    sector = get_sector(symbol)
    sec_info = SECTOR_MAP.get(sector, {})
    if not sec_info: return 4.0
    sec = sector_cache[(date, scan_bar)].get(sector, {})
    if not sec: return 4.0
    leader_chg = sec.get('leader_change_pct', 0)
    strength = sec.get('strength', 0)
    aligned = (direction == "LONG" and leader_chg > 0.3) or (direction == "SHORT" and leader_chg < -0.3)
    against = (direction == "LONG" and leader_chg < -0.3) or (direction == "SHORT" and leader_chg > 0.3)
    if aligned and abs(leader_chg) > 1.0: score += 2.5
    elif aligned: score += 1.5
    elif against: score -= 2.0
    if strength >= 0.75: score += 1.0
    elif strength <= 0.25: score -= 1.5
    return max(0, min(10, score))


def fast_score_market(date, scan_bar, direction):
    score = 5.0
    up, down, total = market_cache[(date, scan_bar)]
    if total == 0: return 5.0
    up_pct = up / total * 100
    down_pct = down / total * 100
    if direction == "LONG" and up_pct > 65: score += 2.0
    elif direction == "LONG" and up_pct > 55: score += 1.0
    elif direction == "LONG" and up_pct < 35: score -= 2.0
    elif direction == "SHORT" and down_pct > 65: score += 2.0
    elif direction == "SHORT" and down_pct > 55: score += 1.0
    elif direction == "SHORT" and down_pct < 35: score -= 2.0
    flat = total - up - down
    if flat > total * 0.4: score -= 1.0
    return max(0, min(10, score))


def fast_score_macro(date, direction):
    score = 5.0
    ctx = macro.get(date, {})
    if not ctx: return 5.0
    vix = ctx.get('india_vix', 0)
    sp = ctx.get('sp500_overnight', 0)
    nq = ctx.get('nasdaq_overnight', 0)
    nifty_prev = ctx.get('nifty_prev_return', 0)
    if vix > 22: score -= 1.5
    elif vix > 18: score -= 0.5
    elif vix < 13: score += 1.0
    us_avg = (sp + nq) / 2
    if direction == "LONG" and us_avg > 0.5: score += 1.5
    elif direction == "LONG" and us_avg < -0.5: score -= 1.5
    elif direction == "SHORT" and us_avg < -0.5: score += 1.5
    elif direction == "SHORT" and us_avg > 0.5: score -= 1.5
    if direction == "LONG" and 0 < nifty_prev < 1.0: score += 0.5
    elif direction == "LONG" and nifty_prev > 1.5: score -= 0.5
    elif direction == "SHORT" and -1.0 < nifty_prev < 0: score += 0.5
    elif direction == "SHORT" and nifty_prev < -1.5: score -= 0.5
    return max(0, min(10, score))


def get_scanners(sym, bsf, pc, crowd, total_syms, scan_bar):
    """All 15 strategies"""
    scanners = [
        lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
        lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
        lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
        lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=4 else None,
        lambda: OpeningRangeBreakout.scan(sym, bsf, pc) if len(bsf)>=7 else None,
        lambda: PivotBreakout.scan(sym, bsf, pc) if len(bsf)>=4 else None,
        lambda: VWAPCrossSignal.scan(sym, bsf) if len(bsf)>=5 else None,
        lambda: MomentumBurst.scan(sym, bsf) if len(bsf)>=6 else None,
        lambda: EngulfingPattern.scan(sym, bsf) if len(bsf)>=3 else None,
        lambda: DayHighLowBreak.scan(sym, bsf) if len(bsf)>=5 else None,
        lambda: VolumeSpike.scan(sym, bsf) if len(bsf)>=6 else None,
        lambda: HammerShootingStar.scan(sym, bsf) if len(bsf)>=5 else None,
    ]
    # These need more bars
    if len(bsf) >= 22:
        scanners.append(lambda: EMA9_21Cross.scan(sym, bsf))
    if len(bsf) >= 12:
        scanners.append(lambda: SuperTrendSignal.scan(sym, bsf))
    if len(bsf) >= 15:
        scanners.append(lambda: RSIExtreme.scan(sym, bsf))
    if len(bsf) >= 20:
        scanners.append(lambda: BollingerBounce.scan(sym, bsf))
    if len(bsf) >= 3:
        scanners.append(lambda: InsideBarBreakout.scan(sym, bsf))
    return scanners


def compute_indicators(bsf, db, sym, date, scan_bar, direction, pc_val):
    """Compute 50+ indicators"""
    closes = [b['close'] for b in bsf]
    highs = [b['high'] for b in bsf]
    lows = [b['low'] for b in bsf]
    volumes = [b['volume'] for b in bsf]
    price = closes[-1]
    dir_mult = 1 if direction == 'LONG' else -1
    n = len(closes)

    # ═══ MoE SCORES ═══
    V = score_volume(sym, db[:scan_bar+1], all_data, date, scan_bar)
    S = fast_score_sector(sym, date, scan_bar, direction)
    P = score_price(sym, db[:scan_bar+1], scan_bar, pc_val, direction)
    M = score_momentum(sym, db[:scan_bar+1], scan_bar, direction)
    Mkt = fast_score_market(date, scan_bar, direction)
    Mac = fast_score_macro(date, direction)
    moe = V*1.5 + M*1.3 + P*0.8 + S*0.5 + Mkt*0.5 + Mac*0.5

    # ═══ MOMENTUM ═══
    # RSI
    if n >= 7:
        gains = [max(0, closes[i]-closes[i-1]) for i in range(1, n)]
        loss_l = [max(0, closes[i-1]-closes[i]) for i in range(1, n)]
        ag = sum(gains[-6:])/6; al = sum(loss_l[-6:])/6
        rsi = 100 - 100/(1+ag/al) if al > 0 else 50
    else: rsi = 50

    # Stochastic
    hh = max(highs[-min(7,n):]); ll = min(lows[-min(7,n):])
    stoch = (price - ll)/(hh - ll)*100 if hh != ll else 50

    # Williams %R
    williams = (hh - price)/(hh - ll)*-100 if hh != ll else -50

    # CCI
    if n >= 5:
        tps = [(highs[i]+lows[i]+closes[i])/3 for i in range(n-min(5,n), n)]
        mean_tp = sum(tps)/len(tps)
        mean_dev = sum(abs(tp-mean_tp) for tp in tps)/len(tps)
        cci = (tps[-1]-mean_tp)/(0.015*mean_dev) if mean_dev > 0 else 0
    else: cci = 0

    # ROC
    roc = (closes[-1]/closes[0]-1)*100 if closes[0] != 0 else 0

    # MACD approx
    fast_ema = sum(closes[-3:])/3 if n >= 3 else price
    slow_ema = sum(closes[-min(5,n):])/min(5,n)
    macd = fast_ema - slow_ema

    # ═══ TREND ═══
    # ADX approx
    if n >= 5:
        plus_dm = sum(max(0, highs[i]-highs[i-1]) for i in range(n-4, n))/4
        minus_dm = sum(max(0, lows[i-1]-lows[i]) for i in range(n-4, n))/4
        tr_sum = sum(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(n-4, n))/4
        adx = abs(plus_dm-minus_dm)/(plus_dm+minus_dm)*100 if (plus_dm+minus_dm) > 0 else 0
    else: adx = 25

    # EMA spread
    ema_spread = (fast_ema-slow_ema)/slow_ema*100 if slow_ema != 0 else 0

    # Higher lows / lower highs count
    hl_count = 0
    for i in range(n-1, 1, -1):
        if direction=='LONG' and lows[i] > lows[i-1]: hl_count += 1
        elif direction=='SHORT' and highs[i] < highs[i-1]: hl_count += 1
        else: break

    # ═══ VOLATILITY ═══
    atr_pct = sum(highs[i]-lows[i] for i in range(n))/n/price*100 if price > 0 else 0

    # Bollinger position
    sma = sum(closes)/n
    std = math.sqrt(sum((c-sma)**2 for c in closes)/n) if n > 1 else 0
    bb_upper = sma + 2*std; bb_lower = sma - 2*std
    bb_pos = (price-bb_lower)/(bb_upper-bb_lower) if bb_upper != bb_lower else 0.5

    # Keltner position
    atr = sum(highs[i]-lows[i] for i in range(n))/n
    kelt_upper = sma + 1.5*atr; kelt_lower = sma - 1.5*atr
    kelt_pos = (price-kelt_lower)/(kelt_upper-kelt_lower) if kelt_upper != kelt_lower else 0.5

    # Range expansion
    if n >= 3:
        avg_range = sum(highs[i]-lows[i] for i in range(n-1))/(n-1)
        range_exp = (highs[-1]-lows[-1])/avg_range if avg_range > 0 else 1
    else: range_exp = 1

    # Noise ratio
    ranges_sum = sum(highs[i]-lows[i] for i in range(max(0,n-5), n))
    move = abs(closes[-1]-closes[max(0,n-5)])
    noise = ranges_sum/(move+0.001)

    # Donchian position (price vs N-bar high/low)
    don_hi = max(highs); don_lo = min(lows)
    don_pos = (price-don_lo)/(don_hi-don_lo) if don_hi != don_lo else 0.5

    # ═══ VOLUME ═══
    avg_vol = prev_5d_vol.get((date, sym), 0)
    rvol = sum(volumes)/avg_vol if avg_vol > 0 else 1

    # OBV slope
    obv = 0; obv_start = 0
    for i in range(1, n):
        if closes[i] > closes[i-1]: obv += volumes[i]
        elif closes[i] < closes[i-1]: obv -= volumes[i]
        if i == max(1, n-3): obv_start = obv
    obv_slope = 1 if obv > obv_start else (-1 if obv < obv_start else 0)

    # MFI
    if n >= 5:
        pos_flow = neg_flow = 0
        for i in range(max(1,n-5), n):
            tp = (highs[i]+lows[i]+closes[i])/3
            tp_prev = (highs[i-1]+lows[i-1]+closes[i-1])/3
            mf = tp * volumes[i]
            if tp > tp_prev: pos_flow += mf
            else: neg_flow += mf
        mfi = 100 - 100/(1+pos_flow/neg_flow) if neg_flow > 0 else (100 if pos_flow > 0 else 50)
    else: mfi = 50

    # Volume trend
    if n >= 3:
        vol_increasing = all(volumes[i] >= volumes[i-1] for i in range(max(1,n-3), n))
        vol_decreasing = all(volumes[i] <= volumes[i-1] for i in range(max(1,n-3), n))
        vol_trend = 1 if vol_increasing else (-1 if vol_decreasing else 0)
    else: vol_trend = 0

    # ═══ PRICE ACTION ═══
    # Candle pattern
    body = abs(bsf[-1]['close']-bsf[-1]['open'])
    rng = bsf[-1]['high']-bsf[-1]['low']
    body_ratio = body/rng*100 if rng > 0 else 0
    upper_wick = bsf[-1]['high'] - max(bsf[-1]['open'], bsf[-1]['close'])
    lower_wick = min(bsf[-1]['open'], bsf[-1]['close']) - bsf[-1]['low']
    candle = 0
    if rng > 0:
        br = body/rng
        if br > 0.8: candle = 2 if bsf[-1]['close'] > bsf[-1]['open'] else -2
        elif lower_wick > body*2 and upper_wick < body*0.5: candle = 1
        elif upper_wick > body*2 and lower_wick < body*0.5: candle = -1

    # VWAP
    tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
    cum_vol = sum(b['volume'] for b in bsf)
    vwap = tp_vol/cum_vol if cum_vol > 0 else price
    vwap_dist = (price-vwap)/vwap*100

    # Bar position in day range
    day_high = max(highs); day_low = min(lows)
    bar_pos = (price-day_low)/(day_high-day_low) if day_high != day_low else 0.5

    # Gap
    gap = (bsf[0]['open']-pc_val)/pc_val*100
    morning_move = (price-bsf[0]['open'])/bsf[0]['open']*100

    # Consecutive bars
    consec = 0
    for k in range(n-1, 0, -1):
        bar_green = bsf[k]['close'] > bsf[k]['open']
        if (direction=='LONG' and bar_green) or (direction=='SHORT' and not bar_green): consec += 1
        else: break

    # ═══ ICHIMOKU (simplified for short bars) ═══
    if n >= 9:
        tenkan = (max(highs[-9:])+min(lows[-9:]))/2
        kijun = (max(highs[-min(n,9):])+min(lows[-min(n,9):]))/2 if n >= 9 else tenkan
        ichi_signal = 1 if price > tenkan > kijun else (-1 if price < tenkan < kijun else 0)
        ichi_dist = (price-tenkan)/price*100
    else:
        ichi_signal = 0; ichi_dist = 0

    # ═══ PARABOLIC SAR (simplified) ═══
    # Approximate: if price is making higher highs, SAR is below = bullish
    if n >= 4:
        recent_trend = closes[-1] > closes[-3]  # up trend
        psar_bullish = 1 if recent_trend else -1
    else: psar_bullish = 0

    # ═══ FIBONACCI LEVELS ═══
    # Use day's range: is price at 38.2%, 50%, or 61.8% retracement?
    if day_high != day_low:
        fib_382 = day_high - 0.382*(day_high-day_low)
        fib_500 = day_high - 0.500*(day_high-day_low)
        fib_618 = day_high - 0.618*(day_high-day_low)
        # Distance to nearest fib level
        fib_nearest = min(abs(price-fib_382), abs(price-fib_500), abs(price-fib_618))
        fib_pct = fib_nearest/price*100
        # Is price near a fib level (within 0.1%)?
        near_fib = 1 if fib_pct < 0.1 else 0
    else:
        fib_pct = 0; near_fib = 0

    # ═══ PREVIOUS DAY CONTEXT ═══
    pdh = prev_day_high_map.get((date, sym), price)
    pdl = prev_day_low_map.get((date, sym), price)
    pivot = (pdh+pdl+pc_val)/3
    pivot_dist = (price-pivot)/pivot*100
    vs_pdh = (price-pdh)/pdh*100
    vs_pdl = (price-pdl)/pdl*100

    # ═══ MACRO ═══
    mc = macro.get(date, {})
    vix = mc.get('india_vix', 0)
    us_ret = mc.get('sp500_overnight', 0)
    nifty_prev = mc.get('nifty_prev_return', 0)

    # Delivery
    del_data = delivery.get(sym, {})
    recent_del = sorted(d for d in del_data if d < date)
    del_pct = 0
    if recent_del:
        try: del_pct = float(del_data[recent_del[-1]].get('delivery_pct', 0) or 0)
        except: pass

    # Day of week
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: dow = -1

    # Direction-adjusted
    gap_adj = gap * dir_mult
    morning_adj = morning_move * dir_mult
    vwap_adj = vwap_dist * dir_mult
    macd_adj = macd * dir_mult
    rsi_adj = (rsi-50) * dir_mult

    # ═══ MULTI-STRATEGY COUNT ═══
    # How many strategies fire on this sym+date+direction?
    # (computed outside, passed in)

    return {
        'V': V, 'S': S, 'P': P, 'M': M, 'Mkt': Mkt, 'Mac': Mac, 'moe': round(moe,1),
        'rsi': round(rsi,1), 'stoch': round(stoch,1), 'williams': round(williams,1),
        'cci': round(cci,1), 'roc': round(roc,3), 'macd': round(macd,4),
        'adx': round(adx,1), 'ema_spread': round(ema_spread,3), 'hl_count': hl_count,
        'atr_pct': round(atr_pct,3), 'bb_pos': round(bb_pos,3), 'kelt_pos': round(kelt_pos,3),
        'range_exp': round(range_exp,2), 'noise': round(noise,2), 'don_pos': round(don_pos,3),
        'rvol': round(rvol,2), 'obv_slope': obv_slope, 'mfi': round(mfi,1), 'vol_trend': vol_trend,
        'body_ratio': round(body_ratio,1), 'candle': candle, 'vwap_dist': round(vwap_dist,3),
        'bar_pos': round(bar_pos,3), 'gap': round(gap,3), 'morning_move': round(morning_move,3),
        'consec': consec,
        'ichi_signal': ichi_signal, 'ichi_dist': round(ichi_dist,3),
        'psar': psar_bullish, 'fib_pct': round(fib_pct,3), 'near_fib': near_fib,
        'pivot_dist': round(pivot_dist,3), 'vs_pdh': round(vs_pdh,2), 'vs_pdl': round(vs_pdl,2),
        'vix': round(vix,1), 'us_ret': round(us_ret,2), 'nifty_prev': round(nifty_prev,2),
        'del_pct': round(del_pct,1), 'dow': dow,
        'gap_adj': round(gap_adj,3), 'morning_adj': round(morning_adj,3),
        'vwap_adj': round(vwap_adj,3), 'macd_adj': round(macd_adj,4), 'rsi_adj': round(rsi_adj,1),
    }


def simulate_trade(db, scan_bar, entry, direction, atr_val, rr, exit_bar):
    """Simulate with given R:R and exit bar"""
    stop, _ = SmartStopCalculator.calculate(db, scan_bar, entry, direction, atr_val)
    target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, rr)
    exit_price = None
    for j in range(scan_bar+1, min(len(db), exit_bar+1)):
        if direction == 'LONG':
            if db[j]['low'] <= stop: exit_price = stop; break
            if db[j]['high'] >= target: exit_price = target; break
        else:
            if db[j]['high'] >= stop: exit_price = stop; break
            if db[j]['low'] <= target: exit_price = target; break
    if not exit_price:
        exit_price = db[min(exit_bar, len(db)-1)]['close']
    pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100
    return pnl


# ═══ PHASE 1: GENERATE ALL SIGNALS ═══
EXIT_BARS = {
    '1PM': 36, '2PM': 48, '230PM': 54, '3PM': 69, '315PM': 72
}
RR_RATIOS = [1.0, 1.5, 2.0, 2.5, 3.0]

print(f'\n{"="*80}')
print(f'PHASE 1: Generating signals across {len(SCAN_BARS)} scan bars, {len(RR_RATIOS)} R:R, {len(EXIT_BARS)} exits')
print(f'{"="*80}', flush=True)

all_signals = []
t0 = time.time()

for scan_bar in SCAN_BARS:
    for di, date in enumerate(all_dates):
        # Crowd count
        crowd = 0; total_syms = 0
        for sym in date_bars[date]:
            db = date_bars[date][sym]
            pc = prev_close_map.get((date, sym))
            if pc is None or not db: continue
            total_syms += 1
            g = (db[0]['open']-pc)/pc*100
            if abs(g) > 0.3: crowd += 1

        for sym in date_bars[date]:
            db = date_bars[date][sym]
            pc_val = prev_close_map.get((date, sym))
            if pc_val is None or len(db) <= scan_bar: continue
            bsf = db[:scan_bar+1]

            # Count strategies that fire on this sym
            strat_names = []
            for scanner in get_scanners(sym, bsf, pc_val, crowd, total_syms, scan_bar):
                try:
                    sig = scanner()
                except: continue
                if not sig: continue
                strat_names.append(sig.strategy_name)
                direction = sig.direction
                entry = sig.suggested_entry

                # Compute indicators once
                inds = compute_indicators(bsf, db, sym, date, scan_bar, direction, pc_val)

                # ATR for stops
                atr_bars = prev_bars_for_atr.get((date, sym), [])
                atr_val = vol_agent.calculate_atr(atr_bars) if len(atr_bars) >= 5 else 0

                # Simulate across all R:R and exit times
                pnls = {}
                for rr in RR_RATIOS:
                    for exit_name, exit_bar in EXIT_BARS.items():
                        if exit_bar <= scan_bar: continue
                        pnl = simulate_trade(db, scan_bar, entry, direction, atr_val, rr, exit_bar)
                        pnls[f'rr{rr}_{exit_name}'] = round(pnl, 4)

                sig_data = {
                    'date': date, 'sym': sym, 'dir': direction, 'strat': sig.strategy_name,
                    'scan_bar': scan_bar, 'entry': round(entry, 2),
                    **inds, **pnls,
                }
                all_signals.append(sig_data)

    elapsed = time.time() - t0
    print(f'  scan_bar={scan_bar}: {len(all_signals)} signals total ({elapsed:.1f}s)', flush=True)

# Multi-strategy confirmation count
print('Computing multi-strategy confirmation...', flush=True)
from collections import Counter
sig_key_counts = Counter()
for s in all_signals:
    key = (s['date'], s['sym'], s['dir'], s['scan_bar'])
    sig_key_counts[key] += 1
for s in all_signals:
    key = (s['date'], s['sym'], s['dir'], s['scan_bar'])
    s['multi_strat'] = sig_key_counts[key]

print(f'\nTotal: {len(all_signals)} signals generated in {time.time()-t0:.1f}s')

# ═══ PHASE 2: FIND BEST SETUP ACROSS ALL COMBINATIONS ═══
print(f'\n{"="*80}')
print('PHASE 2: Finding best setups across all scan_bar × R:R × exit combos')
print(f'{"="*80}', flush=True)

# For each (scan_bar, rr, exit) combo, find best filter
pnl_cols = [c for c in all_signals[0] if c.startswith('rr')]
best_setups = []

for pnl_col in pnl_cols:
    rr_str, exit_name = pnl_col.split('_', 1)

    for scan_bar in SCAN_BARS:
        subset = [s for s in all_signals if s['scan_bar'] == scan_bar and pnl_col in s]
        if not subset: continue

        # Test filter combos
        for p_min in [6, 7, 8]:
            for m_min in [7, 8, 9]:
                # With candle filter
                for candle_max in [99, 0, -1]:
                    for noise_max in [99, 3, 2.5]:
                        filt = [s for s in subset
                                if s['P'] >= p_min and s['M'] >= m_min
                                and s['candle'] <= candle_max and s['noise'] <= noise_max]
                        if len(filt) < 5: continue
                        # Dedup by date+sym
                        seen = {}
                        for s in sorted(filt, key=lambda x: -x['moe']):
                            key = (s['date'], s['sym'])
                            if key not in seen: seen[key] = s
                        unique = list(seen.values())
                        if len(unique) < 5: continue

                        w = sum(1 for s in unique if s[pnl_col] > 0)
                        wr = w/len(unique)*100
                        total_pnl = sum(s[pnl_col] for s in unique)
                        days = len(set(s['date'] for s in unique))

                        if wr >= 60:
                            best_setups.append({
                                'scan': scan_bar, 'rr': rr_str, 'exit': exit_name,
                                'filter': f'P>={p_min}+M>={m_min}+c<={candle_max}+n<={noise_max}',
                                'n': len(unique), 'w': w, 'wr': wr,
                                'pnl': total_pnl, 'days': days,
                                'avg': total_pnl/len(unique)
                            })

                # With multi-strat filter
                for multi_min in [2, 3]:
                    filt = [s for s in subset
                            if s['P'] >= p_min and s['M'] >= m_min and s['multi_strat'] >= multi_min]
                    if len(filt) < 5: continue
                    seen = {}
                    for s in sorted(filt, key=lambda x: -x['moe']):
                        key = (s['date'], s['sym'])
                        if key not in seen: seen[key] = s
                    unique = list(seen.values())
                    if len(unique) < 5: continue
                    w = sum(1 for s in unique if s[pnl_col] > 0)
                    wr = w/len(unique)*100
                    total_pnl = sum(s[pnl_col] for s in unique)
                    days = len(set(s['date'] for s in unique))
                    if wr >= 60:
                        best_setups.append({
                            'scan': scan_bar, 'rr': rr_str, 'exit': exit_name,
                            'filter': f'P>={p_min}+M>={m_min}+multi>={multi_min}',
                            'n': len(unique), 'w': w, 'wr': wr,
                            'pnl': total_pnl, 'days': days,
                            'avg': total_pnl/len(unique)
                        })

                # With morning/gap/vwap
                for morning_min in [0.5, 1.0]:
                    filt = [s for s in subset
                            if s['P'] >= p_min and s['M'] >= m_min and s['morning_adj'] >= morning_min]
                    if len(filt) < 5: continue
                    seen = {}
                    for s in sorted(filt, key=lambda x: -x['moe']):
                        key = (s['date'], s['sym'])
                        if key not in seen: seen[key] = s
                    unique = list(seen.values())
                    if len(unique) < 5: continue
                    w = sum(1 for s in unique if s[pnl_col] > 0)
                    wr = w/len(unique)*100
                    total_pnl = sum(s[pnl_col] for s in unique)
                    days = len(set(s['date'] for s in unique))
                    if wr >= 60:
                        best_setups.append({
                            'scan': scan_bar, 'rr': rr_str, 'exit': exit_name,
                            'filter': f'P>={p_min}+M>={m_min}+morn>={morning_min}',
                            'n': len(unique), 'w': w, 'wr': wr,
                            'pnl': total_pnl, 'days': days,
                            'avg': total_pnl/len(unique)
                        })

best_setups.sort(key=lambda x: (-x['wr'], -x['n'], -x['pnl']))

# Deduplicate
seen_keys = set()
unique_setups = []
for s in best_setups:
    key = (s['scan'], s['rr'], s['exit'], s['filter'])
    if key in seen_keys: continue
    seen_keys.add(key)
    unique_setups.append(s)

print(f'\nFound {len(unique_setups)} setups with WR >= 60%')
print(f"\n{'Scan':>4} {'R:R':>5} {'Exit':>6} {'Filter':>40} {'N':>4} {'W':>3} {'WR':>5} {'Days':>5} {'P&L':>8} {'Avg':>7}")
print('-'*95)
for s in unique_setups[:60]:
    marker = ' ***' if s['wr'] >= 90 else (' <<' if s['wr'] >= 80 else '')
    print(f"{s['scan']:>4} {s['rr']:>5} {s['exit']:>6} {s['filter']:>40} {s['n']:>4} {s['w']:>3} {s['wr']:>4.0f}% {s['days']:>5} {s['pnl']:>+7.2f}% {s['avg']:>+6.3f}%{marker}")


# ═══ PHASE 3: PER-STOCK ANALYSIS ═══
print(f'\n{"="*80}')
print('PHASE 3: Per-stock predictability (which stocks are easiest to trade?)')
print(f'{"="*80}')

# Use the default rr2.5_3PM
pnl_default = 'rr2.5_3PM'
for sym in sorted(all_data.keys()):
    sym_sigs = [s for s in all_signals if s['sym'] == sym and s['scan_bar'] == 6 and pnl_default in s]
    if len(sym_sigs) < 10: continue
    w = sum(1 for s in sym_sigs if s[pnl_default] > 0)
    wr = w/len(sym_sigs)*100
    total_pnl = sum(s[pnl_default] for s in sym_sigs)
    # With P>=7+M>=8
    filtered = [s for s in sym_sigs if s['P']>=7 and s['M']>=8]
    if filtered:
        fw = sum(1 for s in filtered if s[pnl_default] > 0)
        fwr = fw/len(filtered)*100
        fpnl = sum(s[pnl_default] for s in filtered)
        marker = ' <<<' if fwr >= 60 else ''
        print(f"  {sym:>12}: raw={len(sym_sigs):>3} WR={wr:.0f}% P&L={total_pnl:+.1f}% | filtered={len(filtered):>2} WR={fwr:.0f}% P&L={fpnl:+.1f}%{marker}")
    else:
        print(f"  {sym:>12}: raw={len(sym_sigs):>3} WR={wr:.0f}% P&L={total_pnl:+.1f}%")


# ═══ PHASE 4: MULTI-STRATEGY CONFIRMATION ═══
print(f'\n{"="*80}')
print('PHASE 4: Multi-strategy confirmation (>1 strategy fires = stronger?)')
print(f'{"="*80}')
for multi_min in [1, 2, 3, 4, 5]:
    subset = [s for s in all_signals if s['scan_bar'] == 6 and s['multi_strat'] >= multi_min and pnl_default in s]
    if len(subset) < 5: continue
    # Dedup
    seen = {}
    for s in sorted(subset, key=lambda x: -x['moe']):
        key = (s['date'], s['sym'])
        if key not in seen: seen[key] = s
    unique = list(seen.values())
    w = sum(1 for s in unique if s[pnl_default] > 0)
    wr = w/len(unique)*100
    total_pnl = sum(s[pnl_default] for s in unique)
    days = len(set(s['date'] for s in unique))
    print(f"  multi_strat >= {multi_min}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={total_pnl:+.2f}%")


# Save
with open('data/exhaustive_results.json', 'w') as f:
    json.dump(all_signals, f)
print(f'\nSaved {len(all_signals)} signals to data/exhaustive_results.json')
print(f'Total time: {time.time()-t0:.1f}s')
