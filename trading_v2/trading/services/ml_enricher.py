"""ML Enricher — fetches Market Lens 94 fields on demand.
Caches the last poll (60s TTL). Used by trading system to log ML data with every signal.

Usage:
    from trading.services.ml_enricher import get_ml_snapshot
    data = get_ml_snapshot('TATASTEEL')  # returns dict with all 94 fields or {}
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

_cache = {}       # ticker -> {fields}
_cache_time = 0   # last poll timestamp
_CACHE_TTL = 60   # refresh every 60s
_session = None
_action = None
_nse_session = None
_market_ctx = {}
_market_ctx_time = 0

# Resolve to news-trading/live/vwap_logs regardless of where this file lives
_this_dir = Path(__file__).resolve().parent
_project_root = _this_dir
while _project_root.name != 'news-trading' and _project_root != _project_root.parent:
    _project_root = _project_root.parent
LOG_DIR = _project_root / 'live' / 'vwap_logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
def _get_signal_log():
    return LOG_DIR / f'ml_signals_{datetime.now().strftime("%Y%m%d")}.jsonl'



def _init():
    global _session, _action
    try:
        from curl_cffi import requests as cf
    except ImportError:
        return False

    BASE = "https://marketlens.nseindia.com"
    _session = cf.Session(impersonate="chrome")
    _session.request("GET", BASE, timeout=30)
    _session.headers.update({"Referer": BASE + "/screener"})
    html = _session.request("GET", BASE + "/screener", timeout=30).text
    for src in dict.fromkeys(re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html)):
        u = urljoin(BASE, src)
        if "/_next/static/" not in u:
            continue
        try:
            js = _session.request("GET", u, timeout=30).text
        except:
            continue
        am = re.search(r'createServerReference\)\("([0-9a-f]+)"[^;]{0,400}?"runStockQuery"', js)
        if am:
            _action = am.group(1)
            break
    return _action is not None


def _parse(content):
    records = {}
    pos = 0
    while pos < len(content):
        if content[pos:pos+1] in (b"\n", b"\r"):
            pos += 1
            continue
        m = re.match(rb"([0-9a-f]+):", content[pos:])
        if not m:
            break
        ident = m.group(1).decode()
        pos += m.end()
        if content[pos:pos+1] == b"T":
            m2 = re.match(rb"T([0-9a-f]+),", content[pos:])
            if not m2:
                break
            pos += m2.end()
            end = pos + int(m2.group(1), 16)
            records[ident] = content[pos:end].decode("utf-8")
            pos = end
        else:
            end = content.find(b"\n", pos)
            if end < 0:
                end = len(content)
            try:
                records[ident] = json.loads(content[pos:end])
            except:
                pass
            pos = end + 1

    def resolve(v, seen=frozenset()):
        if isinstance(v, dict):
            return {k: resolve(val, seen) for k, val in v.items()}
        if isinstance(v, list):
            return [resolve(val, seen) for val in v]
        if isinstance(v, str) and v.startswith("$"):
            if v.startswith("$$"):
                return v[1:]
            ref = v.removeprefix("$").removeprefix("@")
            if ref not in records or ref in seen:
                return v
            t = records[ref]
            return t if isinstance(t, str) else resolve(t, seen | {ref})
        return v

    for val in records.values():
        if isinstance(val, dict) and "success" in val:
            return resolve(val)
    return {"success": False}


def _poll():
    global _cache, _cache_time
    if _session is None or _action is None:
        if not _init():
            return

    BASE = "https://marketlens.nseindia.com"
    try:
        r = _session.request("POST", BASE + "/screener", headers={
            "Next-Action": _action, "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8", "Origin": BASE,
        }, data=json.dumps(["Market Cap > 0"]), timeout=60)
        p = _parse(r.content)
        if p.get("success"):
            stocks = p.get("data", {}).get("stocks", [])
            _cache = {}
            for s in stocks:
                ticker = s.get("ticker", "")
                if ticker:
                    _cache[ticker] = {k: v for k, v in s.items()
                                      if v is not None and v != ""}
            _cache_time = time.time()
    except Exception:
        pass


def _fetch_single(ticker: str) -> dict:
    """Fetch a single stock from ML by querying with its name."""
    if _session is None or _action is None:
        if not _init():
            return {}
    BASE = "https://marketlens.nseindia.com"
    try:
        r = _session.request("POST", BASE + "/screener", headers={
            "Next-Action": _action, "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8", "Origin": BASE,
        }, data=json.dumps([f"Ticker = {ticker}"]), timeout=30)
        p = _parse(r.content)
        if p.get("success"):
            stocks = p.get("data", {}).get("stocks", [])
            for s in stocks:
                if s.get("ticker", "").upper() == ticker.upper():
                    data = {k: v for k, v in s.items() if v is not None and v != ""}
                    _cache[ticker] = data
                    return data
    except:
        pass
    return {}


def get_ml_snapshot(ticker: str) -> dict:
    """Get all 94 ML fields for a ticker. Returns {} if unavailable."""
    if time.time() - _cache_time > _CACHE_TTL:
        _poll()
    result = _cache.get(ticker, {})
    if not result:
        result = _fetch_single(ticker)
    return result


def _extract_underlying(symbol: str) -> str:
    """Extract underlying stock/index from FnO symbol.
    DLF26OCT660CE -> DLF
    NIFTY26SEP25000PE -> NIFTY
    SBIN26SEPFUT -> SBIN
    TATASTEEL26OCT140CE -> TATASTEEL
    BANKNIFTY26SEP52000CE -> BANKNIFTY
    """
    # Only parse if it looks like an FnO symbol (has expiry pattern like 26OCT, 26SEP)
    m = re.match(r'^([A-Z]+?)(\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*(?:\d+)?(?:CE|PE|FUT))$', symbol, re.IGNORECASE)
    if m:
        return m.group(1)
    return symbol


def _get_market_context() -> dict:
    """Fetch NIFTY, VIX, advance/decline from NSE. Cached 60s."""
    global _nse_session, _market_ctx, _market_ctx_time
    if time.time() - _market_ctx_time < 60 and _market_ctx:
        return _market_ctx
    try:
        from curl_cffi import requests as cf
        if _nse_session is None:
            _nse_session = cf.Session(impersonate="chrome")
            _nse_session.get("https://www.nseindia.com", timeout=10)
        r = _nse_session.get("https://www.nseindia.com/api/allIndices", timeout=10)
        if r.status_code != 200:
            _nse_session = cf.Session(impersonate="chrome")
            _nse_session.get("https://www.nseindia.com", timeout=10)
            r = _nse_session.get("https://www.nseindia.com/api/allIndices", timeout=10)
        if r.status_code == 200:
            ctx = {}
            for idx in r.json().get('data', []):
                name = idx.get('index', '')
                if name == 'NIFTY 50':
                    ctx['nifty'] = idx.get('last', 0)
                    ctx['nifty_chg'] = idx.get('percentChange', 0)
                    ctx['nifty_adv'] = idx.get('advances', 0)
                    ctx['nifty_dec'] = idx.get('declines', 0)
                elif name == 'NIFTY BANK':
                    ctx['banknifty'] = idx.get('last', 0)
                    ctx['banknifty_chg'] = idx.get('percentChange', 0)
                elif name == 'INDIA VIX':
                    ctx['vix'] = idx.get('last', 0)
            _market_ctx = ctx
            _market_ctx_time = time.time()
    except:
        pass
    return _market_ctx


def _get_broker_data(ticker: str) -> dict:
    """Fetch full quote + market depth + order book from INDmoney for a stock."""
    try:
        import sys
        _proj = Path(__file__).resolve().parent
        while _proj.name != 'news-trading' and _proj != _proj.parent:
            _proj = _proj.parent
        sys.path.insert(0, str(_proj))
        from live.indmoney_client import get_full_quote, get_market_depth
    except:
        return {}

    result = {}
    try:
        # Full quote: price, OHLC, volume, circuits, 5-level depth
        fq = get_full_quote([ticker])
        if ticker in fq:
            q = fq[ticker]
            result['broker_ltp'] = q.get('last_price', 0)
            result['broker_open'] = q.get('open', 0)
            result['broker_high'] = q.get('high', 0)
            result['broker_low'] = q.get('low', 0)
            result['broker_prev_close'] = q.get('prev_close', 0)
            result['broker_volume'] = q.get('volume', 0)
            result['broker_upper_circuit'] = q.get('upper_circuit', 0)
            result['broker_lower_circuit'] = q.get('lower_circuit', 0)

            # Parse 5-level depth
            md = q.get('market_depth', {})
            for scrip_key, scrip_data in md.items():
                agg = scrip_data.get('aggregate', {})
                result['depth_total_buy'] = _parse_num(agg.get('total_buy', 0))
                result['depth_total_sell'] = _parse_num(agg.get('total_sell', 0))
                result['depth_buy_pct'] = agg.get('buy_percentage', 50)
                result['depth_sell_pct'] = agg.get('sell_percentage', 50)

                levels = scrip_data.get('depth', [])
                for i, level in enumerate(levels[:5]):
                    buy = level.get('buy', {})
                    sell = level.get('sell', {})
                    result[f'bid{i}_qty'] = _parse_num(buy.get('quantity', 0))
                    result[f'bid{i}_price'] = _parse_num(buy.get('price', 0))
                    result[f'ask{i}_qty'] = _parse_num(sell.get('quantity', 0))
                    result[f'ask{i}_price'] = _parse_num(sell.get('price', 0))

                # Spread
                if result.get('bid0_price', 0) > 0 and result.get('ask0_price', 0) > 0:
                    result['spread_pct'] = (result['ask0_price'] - result['bid0_price']) / result['bid0_price'] * 100
                break
    except:
        pass
    return result


def _parse_num(val):
    """Parse '15,226' or '1,182' to int/float."""
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        try:
            return float(val.replace(',', ''))
        except:
            return 0
    return 0


def log_signal_with_ml(component: str, action: str, symbol: str, **kw):
    """Log a signal with full ML data + market context + broker depth.
    For FnO symbols, fetches the underlying stock's ML data.
    For stocks not in ML bulk scan, does a targeted single-stock query."""
    underlying = _extract_underlying(symbol)
    ml = get_ml_snapshot(underlying)
    if not ml and underlying != symbol:
        ml = get_ml_snapshot(symbol)
    mkt = _get_market_context()
    broker = _get_broker_data(underlying)
    record = {
        'ts': datetime.now().isoformat(),
        'component': component,
        'action': action,
        'symbol': symbol,
        'underlying': underlying,
        'ml_fields': ml,
        'market': mkt,
        'broker': broker,
        **kw,
    }
    try:
        with open(_get_signal_log(), 'a') as f:
            f.write(json.dumps(record, default=str) + '\n')
    except:
        pass
    return ml
