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

def score_futures(signal: dict, fd: dict) -> int:
    """89% WR at >= 5 on 166 trades. Walk-forward: 90% -> 92% -> 83%."""
    score = 0
    entry_price = float(fd.get('entryPrice', signal.get('entry_price', 0)) or 0)
    sl = float(fd.get('stopLoss', fd.get('sl', 0)) or 0)
    target = float(fd.get('target', fd.get('targetPrice', 0)) or 0)
    action = str(fd.get('action', signal.get('action', 'BUY'))).upper()

    # 1. SL DISTANCE (0-4)
    sl_pct = abs(entry_price - sl) / entry_price * 100 if entry_price > 0 and sl > 0 else 0
    if sl_pct >= 5.0: score += 4
    elif sl_pct >= 2.0: score += 3
    elif sl_pct >= 1.0: score += 2
    elif sl_pct >= 0.5: score += 1

    # 2. DAY OF WEEK (0-3)
    dow = datetime.now().weekday()
    if dow == 4: score += 3
    elif dow in (1, 2): score += 2
    elif dow == 3: score += 1

    # 3. ENTRY HOUR (0-2)
    hour = datetime.now().hour
    if hour == 14: score += 2
    elif 9 <= hour <= 13: score += 1

    # 4. DIRECTION (0-1)
    if action == 'SELL': score += 1

    # 5. TARGET % (0-2)
    tgt_pct = abs(target - entry_price) / entry_price * 100 if target > 0 and entry_price > 0 else 0
    if 2.0 <= tgt_pct <= 5.0: score += 2
    elif 0.5 <= tgt_pct < 2.0: score += 1

    # 6. PRICE LEVEL (0-1)
    if 500 <= entry_price <= 2000: score += 1

    log.info(f'FUT {signal.get("symbol","")}: conv={score} '
             f'(sl={sl_pct:.1f}% d={dow} h={hour} {action} tgt={tgt_pct:.1f}% p={entry_price:.0f})')
    return score
