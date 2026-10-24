import re
"""Multyfi signal scoring — PE (12-dim), CE (11-dim), Futures (6-dim).

Each function takes a signal dict and returns an integer score.
No trading logic — just conviction calculation.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from trading.logger import get_logger

log = get_logger('multyfi_scoring')


# ── Market data helpers ─────────────────────────────────────────────

def _clean_symbol(sym: str) -> str:
    """Strip option/futures suffix to get base stock symbol.

    TORNTPHARM26SEP4950CE -> TORNTPHARM
    NIFTY2692223450CE -> NIFTY
    SBIN26SEPFUT -> SBIN
    NIFTY26SEP23250PE -> NIFTY
    """
    s = sym.upper().strip()
    # Pattern 1: SYMBOL + 2-digit year + 3-letter month + strike + CE/PE
    s = re.sub(r'\d{2}[A-Z]{3}\d*[CP]E$', '', s).strip()
    # Pattern 2: SYMBOL + YYMM or YYMMDD + strike + CE/PE (e.g. NIFTY2692223450CE)
    s = re.sub(r'\d{4,}[CP]E$', '', s).strip()
    # Pattern 3: futures suffix
    s = re.sub(r'\d{2}[A-Z]{3}FUT$', '', s).strip()
    return s or sym


def get_prev_data(sym: str) -> dict:
    """Previous day: ATR, RSI, BB, momentum."""
    try:
        sym = _clean_symbol(sym)
        ticker = sym + '.NS' if not sym.endswith('.NS') else sym
        clean = sym.replace('.NS', '')
        if clean in ('NIFTY', 'NIFTY 50', 'BANKNIFTY', 'SENSEX'):
            ticker = '^NSEI' if 'NIFTY' in clean else '^BSESN'

        df = yf.download(ticker, period='60d', progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 20:
            return None

        c = df['Close'].values.astype(float)
        h = df['High'].values.astype(float)
        l = df['Low'].values.astype(float)
        o = df['Open'].values.astype(float)
        i = -2 if datetime.now().hour >= 10 else -1

        tr = [max(h[j]-l[j], abs(h[j]-c[j-1]), abs(l[j]-c[j-1])) for j in range(1, len(c))]
        atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))

        delta = pd.Series(c).diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss_s = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss_s.replace(0, np.nan)
        rsi_s = 100 - (100 / (1 + rs))
        rsi = float(rsi_s.iloc[i]) if not np.isnan(rsi_s.iloc[i]) else None

        sma20 = pd.Series(c).rolling(20).mean().values
        std20 = pd.Series(c).rolling(20).std().values
        bb = (c[i] - (sma20[i] - 2*std20[i])) / (4*std20[i]) if std20[i] > 0 else 0.5

        today_open = float(o[-1]) if datetime.now().hour >= 9 else None

        return {
            'prev_close': float(c[i]), 'prev_ret': (c[i]-c[i-1])/c[i-1]*100,
            'atr': atr, 'rsi': rsi, 'bb': bb,
            'two_red': 1 if (c[i] < o[i] and c[i-1] < o[i-1]) else 0,
            'open_below': (1 if today_open and today_open < c[i] else 0) if today_open else None,
            'open_above': (1 if today_open and today_open > c[i] else 0) if today_open else None,
            'prev5_ret': (c[i]-c[i-5])/c[i-5]*100 if abs(i) <= len(c)-5 else None,
        }
    except Exception as e:
        log.warning(f'prev_data error {sym}: {e}')
        return None


def get_vix() -> dict:
    """India VIX level and change."""
    try:
        df = yf.download('^INDIAVIX', period='5d', progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 2:
            return None
        c = df['Close'].values.astype(float)
        return {'vix': float(c[-2]), 'vix_chg': (c[-2]-c[-3])/c[-3]*100}
    except Exception:
        return None


# ── PE scoring (12 dimensions, 0-30) ────────────────────────────────

def score_pe(signal: dict) -> int:
    """Walk-forward: 88.5% WR at >= 20."""
    score = 0
    sym = signal['symbol']
    entry_price = signal.get('entry_price', 0)
    target_pct = 0
    try:
        target_pct = float(str(signal.get('target_pct', 0)).replace('%', ''))
    except Exception:
        pass

    is_index = sym in ('NIFTY', 'NIFTY 50', 'SENSEX', 'BANKNIFTY', 'BANK NIFTY')
    hour = datetime.now().hour
    dow = datetime.now().weekday()
    prev = get_prev_data(sym)
    vix = get_vix()

    # 1. PREMIUM/ATR (0-5)
    if prev and prev['atr'] > 0 and entry_price > 0:
        pa = entry_price / prev['atr']
        if pa < 0.1: pass
        elif pa < 0.2: score += 2
        elif pa < 0.3: score += 3
        elif pa < 0.5: score += 4
        else: score += 5

    # 2. TARGET % (0-4)
    if target_pct <= 20: score += 4
    elif target_pct <= 30: score += 3
    elif target_pct <= 40: score += 2
    elif target_pct <= 50: score += 1

    # 3. DTE (0-3)
    expiry = signal.get('expiry', '')
    if expiry:
        try:
            exp = datetime.strptime(str(expiry)[:10], '%Y-%m-%d')
            dte = (exp - datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).days
            if dte == 0: pass
            elif dte <= 2: score += 2
            else: score += 3
        except Exception:
            score += 1
    else:
        score += 1

    # 4. HOUR (0-3)
    if 10 <= hour <= 12: score += 3
    elif hour == 13: score += 2
    elif hour == 9: score += 1

    # 5. MOMENTUM (0-6)
    if prev:
        pr = prev['prev_ret']
        if pr < -1: score += 4
        elif pr < -0.3: score += 3
        elif pr < 0.3: score += 2
        elif pr < 1: score += 1
        if prev.get('two_red'): score += 1
        if prev.get('open_below') == 1: score += 1

    # 6. VIX (0-3, can be -1)
    if vix:
        vx = vix['vix']
        if 14 <= vx <= 18: score += 3
        elif 12 <= vx < 14: score += 2
        elif 18 < vx <= 22: score += 1
        if vix['vix_chg'] < -5: score -= 1
    else:
        score += 1

    # 7. RR RATIO (0-2)
    sl = signal.get('sl', 0)
    if sl and entry_price and target_pct:
        risk = entry_price - sl if sl < entry_price else entry_price * 0.2
        reward = entry_price * target_pct / 100
        rr = reward / risk if risk > 0 else 0
        if 1 <= rr <= 2: score += 2
        elif 0.5 <= rr < 1: score += 1
    else:
        score += 1

    # 8. RSI (0-2)
    if prev and prev.get('rsi'):
        if prev['rsi'] < 40: score += 2
        else: score += 1

    # 9. BB (0-1)
    if prev and prev.get('bb') is not None and prev['bb'] < 0.3:
        score += 1

    # 10. DAY (0-1)
    if dow in (0, 2, 4): score += 1

    # 11. STOCK vs INDEX (0-1)
    if not is_index: score += 1

    # 12. 5-DAY TREND (0-1)
    if prev and prev.get('prev5_ret') is not None and prev['prev5_ret'] < -1:
        score += 1

    log.info(f'PE {sym}: conv={score} (prem/atr, tgt, dte, h{hour}, mom, vix, rr, rsi, bb, day, type, 5d)')
    return score


# ── CE scoring (11 dimensions, 0-28) ────────────────────────────────

def score_ce(signal: dict) -> int:
    """Bounce/breakout logic. Conv >= 19 = 84% WR."""
    score = 0
    sym = signal['symbol']
    entry_price = signal.get('entry_price', 0)
    target_pct = 0
    try:
        target_pct = float(str(signal.get('target_pct', 0)).replace('%', ''))
    except Exception:
        pass

    is_index = sym in ('NIFTY', 'NIFTY 50', 'SENSEX', 'BANKNIFTY', 'BANK NIFTY')
    hour = datetime.now().hour
    dow = datetime.now().weekday()
    prev = get_prev_data(sym)
    vix = get_vix()

    # 1. BB POSITION (0-5)
    if prev and prev.get('bb') is not None:
        bb = prev['bb']
        if bb >= 0.9: score += 5
        elif bb >= 0.7: score += 4
        elif bb >= 0.5: score += 2
        elif bb >= 0.3: score += 1

    # 2. PREV DAY RETURN (0-4)
    if prev:
        pr = prev['prev_ret']
        if pr < -1: score += 4
        elif pr < -0.3: score += 2
        elif pr < 0.3: score += 1
        elif pr < 1: score += 2

    # 3. RSI (0-3)
    if prev and prev.get('rsi'):
        rsi = prev['rsi']
        if rsi < 30: score += 3
        elif 50 <= rsi < 70: score += 2
        else: score += 1

    # 4. VIX (0-3)
    if vix:
        vx = vix['vix']
        if vx >= 18: score += 3
        elif vx >= 14: score += 2
        elif vx >= 12: score += 1
    else:
        score += 1

    # 5. HOUR (0-3)
    if hour == 11: score += 3
    elif hour in (10, 12): score += 2
    elif hour == 9: score += 1

    # 6. PREMIUM/ATR (0-3)
    if prev and prev['atr'] > 0 and entry_price > 0:
        pa = entry_price / prev['atr']
        if pa < 0.1: pass
        elif pa < 0.3: score += 2
        else: score += 3
    else:
        score += 1

    # 7. TARGET (0-3)
    if 25 <= target_pct <= 50: score += 3
    elif 15 <= target_pct < 25: score += 2
    elif target_pct < 15: score += 1

    # 8. DAY (0-2)
    if dow in (0, 2): score += 2
    elif dow == 4: score += 1

    # 9. OPEN ABOVE (0-1)
    if prev and prev.get('open_above') == 1: score += 1

    # 10. 5-DAY TREND (0-2)
    if prev and prev.get('prev5_ret') is not None:
        p5 = prev['prev5_ret']
        if p5 > 1: score += 2
        elif p5 < -2: score += 2
        elif p5 > 0: score += 1

    # 11. STOCK (0-1)
    if not is_index: score += 1

    log.info(f'CE {sym}: conv={score}')
    return score


# ── Futures scoring (6 dimensions, 0-13) ─────────────────────────────

def _get_index_candles_1h(sym: str, n_bars: int = 10):
    """Get last N 1-hour candles for index (NIFTY/BANKNIFTY).
    Uses NIFTYBEES/BANKBEES as proxy via INDmoney candle API.
    Falls back to yfinance if INDmoney fails."""
    import time as _time
    proxy = {'NIFTY': 'NIFTYBEES', 'BANKNIFTY': 'BANKBEES'}.get(sym, sym)
    now_ms = int(_time.time() * 1000)
    start_ms = now_ms - 86400000 * 5  # 5 days
    try:
        from live.indmoney_client import get_historical_candles
        candles = get_historical_candles(proxy, '1hour', start_ms, now_ms)
        if candles and len(candles) >= n_bars:
            # candles = [[ts, o, h, l, c, v], ...]
            return [{'o': c[1], 'h': c[2], 'l': c[3], 'c': c[4]} for c in candles[-n_bars:]]
    except Exception as e:
        log.warning(f'INDmoney candles failed for {proxy}: {e}')
    # Fallback: yfinance
    try:
        ticker = '^NSEI' if 'NIFTY' in sym and 'BANK' not in sym else '^NSEBANK'
        df = yf.download(ticker, period='5d', interval='1h', progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) >= n_bars:
            return [{'o': r['Open'], 'h': r['High'], 'l': r['Low'], 'c': r['Close']}
                    for _, r in df.tail(n_bars).iterrows()]
    except Exception as e:
        log.warning(f'yfinance candles failed for {sym}: {e}')
    return []


def _check_consec_ha(candles: list, action: str) -> tuple:
    """Check consecutive candles + Heikin Ashi alignment.
    Returns (consec_aligned: bool, ha_aligned: bool, consec_count: int)"""
    if len(candles) < 3:
        return False, False, 0
    is_sell = action == 'SELL'

    # Consecutive candles (last 2+ same direction)
    consec = 0
    for i in range(len(candles)-1, 0, -1):
        bull = candles[i]['c'] > candles[i]['o']
        if (is_sell and not bull) or (not is_sell and bull):
            consec += 1
        else:
            break
    consec_aligned = consec >= 2

    # Heikin Ashi on last candle
    # HA close = (O+H+L+C)/4, HA open = (prev_HA_open + prev_HA_close)/2
    ha_open = candles[0]['o']
    for c in candles:
        ha_close = (c['o'] + c['h'] + c['l'] + c['c']) / 4
        ha_open = (ha_open + ha_close) / 2
    ha_bull = ha_close > ha_open
    ha_aligned = (is_sell and not ha_bull) or (not is_sell and ha_bull)

    return consec_aligned, ha_aligned, consec


def score_futures(signal: dict, fd: dict) -> int:
    """consec+ha+noMonAM: 97% WR on 116 trades (2 years).
    Checks 1H candles for consecutive direction + Heikin Ashi confirmation.
    Blocks Monday morning (h<=11) signals."""
    import re as _re
    action = str(fd.get('action', signal.get('action', 'BUY'))).upper()
    sym = str(signal.get('symbol', '')).upper()

    # Extract underlying
    clean = _re.sub(r'\d{2}[A-Z]{3}', '', _re.sub(r'FUT$', '', sym)).strip()
    if clean in ('NIFTY', 'NIFTY50', 'BANKNIFTY', 'BANK NIFTY'):
        underlying = 'BANKNIFTY' if 'BANK' in clean else 'NIFTY'
    else:
        underlying = clean

    dow = datetime.now().weekday()
    hour = datetime.now().hour

    # Block Monday morning (h<=11) — all 6 Monday losers were 9-11 AM
    if dow == 0 and hour <= 11:
        log.info(f'FUT {sym}: SKIP Monday morning (h={hour})')
        return 0

    # Block weekends
    if dow >= 5:
        log.info(f'FUT {sym}: SKIP weekend')
        return 0

    # Get 1H candles and check consec + HA
    candles = _get_index_candles_1h(underlying, 10)
    if not candles:
        log.warning(f'FUT {sym}: no candle data, skip')
        return 0

    consec_al, ha_al, consec_n = _check_consec_ha(candles, action)

    score = 0
    if consec_al: score += 3  # 2+ consecutive candles aligned
    if ha_al: score += 3      # Heikin Ashi confirms

    log.info(f'FUT {sym}: conv={score} consec={consec_al}({consec_n}) ha={ha_al} '
             f'd={dow} h={hour} {action} underlying={underlying}')
    return score


# ── MCX Commodity scoring (5 dimensions, 0-10) ──────────────────────

def score_mcx_commodity(signal: dict, fd: dict) -> int:
    """Silver+Gold MCX futures. 90% WR at >= 4 on 289 trades."""
    score = 0
    entry_price = float(fd.get('entryPrice', signal.get('entry_price', 0)) or 0)
    sl = float(fd.get('stopLoss', fd.get('sl', 0)) or 0)
    sym = str(signal.get('symbol', '')).upper()
    target_pct = 0
    try:
        target_pct = float(str(fd.get('target_pct', fd.get('targetPercent', signal.get('target_pct', 0)))).replace('%', ''))
    except Exception:
        pass

    # 1. SL DISTANCE (0-3) — sweet spot is 0.3-2.0%
    sl_pct = abs(entry_price - sl) / entry_price * 100 if entry_price > 0 and sl > 0 else 0
    if 0.3 <= sl_pct <= 1.0:
        score += 3  # 92% WR on Silver
    elif 1.0 < sl_pct <= 2.0:
        score += 2  # 88% WR
    elif 0.2 <= sl_pct < 0.3:
        score += 1  # 82% WR
    # < 0.2% or > 2.0% = 0 (losers)

    # 2. COMMODITY TYPE (0-4) — Silver only, reject everything else
    if 'SILVER' in sym:
        score += 4  # Silver with trail = 90% WR
    # Gold/Crude/NatGas = 0 (will fail threshold)

    # 3. TARGET (0-2)
    if 0 < target_pct <= 1.0:
        score += 2  # 91% WR, quick targets
    elif 1.0 < target_pct <= 2.0:
        score += 1  # 85% WR

    # 4. ENTRY HOUR (0-2)
    hour = datetime.now().hour
    if 16 <= hour <= 19:
        score += 2  # evening session 81-88% WR, biggest profits
    elif 11 <= hour <= 15:
        score += 1  # day session 77-81% WR

    # 5. ACTION ALIGNMENT (0-1)
    action = str(fd.get('action', signal.get('action', 'BUY'))).upper()
    if 'SILVER' in sym and action == 'BUY':
        score += 1  # Silver BUY = 90% WR vs SELL 74%

    log.info(f'MCX {sym}: conv={score} (sl={sl_pct:.2f}% tgt={target_pct:.1f}% h={hour} {action})')
    return score
