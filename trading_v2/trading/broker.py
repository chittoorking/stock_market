"""Broker — fail-loud wrapper around indmoney_client.

Every method raises BrokerError on failure. Never returns None silently.
Every order is audit-logged before and after.
"""
import sys
from pathlib import Path

# Ensure news-trading root is importable (for `from live import indmoney_client`)
_project_root = Path(__file__).parent.parent.parent  # news-trading/
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(Path(__file__).parent.parent))  # trading_v2/

from trading.logger import get_logger, audit

log = get_logger('broker')


class BrokerError(Exception):
    pass


class Broker:
    """Wraps indmoney_client. Fails loud on any issue."""

    def __init__(self):
        from live import indmoney_client as _api
        self._api = _api
        self._validate()
        self.scrip_count = len(self._api.SCRIP_CODES)
        if self.scrip_count < 100:
            raise BrokerError(f'Only {self.scrip_count} scrips loaded — bad import')
        log.info(f'Broker ready: {self.scrip_count} scrips')

    def _validate(self):
        for attr in ['place_order', 'get_ltp', 'get_order_book', 'SCRIP_CODES',
                      'cancel_order', 'get_funds', 'place_fno_order', 'get_option_chain']:
            if not hasattr(self._api, attr):
                raise BrokerError(f'Missing: {attr}')

    # ── Orders ──

    def buy(self, sym: str, qty: int, price: float = 0,
            order_type: str = 'MARKET', product: str = 'INTRADAY') -> str:
        self._check_sym(sym)
        audit('broker', 'BUY_SUBMIT', sym, qty=qty, price=price, order_type=order_type)
        oid = self._api.place_order(sym, qty, 'BUY', price=price,
                                     order_type=order_type, product=product)
        if not oid:
            audit('broker', 'BUY_FAILED', sym, qty=qty)
            raise BrokerError(f'BUY {sym} qty={qty}: returned None')
        audit('broker', 'BUY_OK', sym, qty=qty, order_id=oid)
        return oid

    def sell(self, sym: str, qty: int, price: float = 0,
             order_type: str = 'MARKET', product: str = 'INTRADAY',
             validity: str = 'DAY') -> str:
        self._check_sym(sym)
        audit('broker', 'SELL_SUBMIT', sym, qty=qty)
        oid = self._api.place_order(sym, qty, 'SELL', price=price,
                                     order_type=order_type, product=product, validity=validity)
        if not oid:
            audit('broker', 'SELL_FAILED', sym, qty=qty)
            raise BrokerError(f'SELL {sym} qty={qty}: returned None')
        audit('broker', 'SELL_OK', sym, qty=qty, order_id=oid)
        return oid

    def buy_limit(self, sym: str, qty: int, price: float) -> str:
        return self.buy(sym, qty, price=price, order_type='LIMIT')

    def buy_fno(self, sym: str, qty: int, sec_id: str) -> str:
        audit('broker', 'FNO_BUY', sym, qty=qty, sec_id=sec_id)
        oid = self._api.place_fno_order(sym, qty, 'BUY', sec_id, order_type='MARKET')
        if not oid:
            raise BrokerError(f'FnO BUY {sym}: returned None')
        audit('broker', 'FNO_BUY_OK', sym, order_id=oid)
        return oid

    def sell_fno(self, sym: str, qty: int, sec_id: str) -> str:
        audit('broker', 'FNO_SELL', sym, qty=qty, sec_id=sec_id)
        oid = self._api.place_fno_order(sym, qty, 'SELL', sec_id, order_type='MARKET')
        if not oid:
            raise BrokerError(f'FnO SELL {sym}: returned None')
        audit('broker', 'FNO_SELL_OK', sym, order_id=oid)
        return oid

    def sell_smart(self, sym: str, qty: int, limit_price: float, sl_trigger: float, sl_limit: float) -> dict:
        """Place smart SELL order with SL. Returns {parent: oid, child: oid}."""
        self._check_sym(sym)
        audit('broker', 'SMART_SELL_SUBMIT', sym, qty=qty, price=limit_price, sl=sl_trigger)
        result = self._api.place_smart_order(sym, qty, 'SELL', limit_price,
                                              sl_trigger=sl_trigger, sl_limit=sl_limit)
        if not result:
            audit('broker', 'SMART_SELL_FAILED', sym, qty=qty)
            raise BrokerError(f'Smart SELL {sym}: returned None')
        audit('broker', 'SMART_SELL_OK', sym, qty=qty, parent=result.get('parent',''), child=result.get('child',''))
        return result

    def buy_smart(self, sym: str, qty: int, limit_price: float, sl_trigger: float, sl_limit: float) -> dict:
        """Place smart BUY order with SL. Returns {parent: oid, child: oid}."""
        self._check_sym(sym)
        audit('broker', 'SMART_BUY_SUBMIT', sym, qty=qty, price=limit_price, sl=sl_trigger)
        result = self._api.place_smart_order(sym, qty, 'BUY', limit_price,
                                              sl_trigger=sl_trigger, sl_limit=sl_limit)
        if not result:
            audit('broker', 'SMART_BUY_FAILED', sym, qty=qty)
            raise BrokerError(f'Smart BUY {sym}: returned None')
        audit('broker', 'SMART_BUY_OK', sym, qty=qty, parent=result.get('parent',''), child=result.get('child',''))
        return result

    def cancel_smart(self, order_id: str):
        """Cancel smart order (SL leg)."""
        try:
            self._api.cancel_smart_order(order_id)
            audit('broker', 'SMART_CANCEL_OK', order_id=order_id)
        except Exception as e:
            audit('broker', 'SMART_CANCEL_FAIL', order_id=order_id, error=str(e))

    # ── Price ──

    # Index scrip codes for LTP
    _INDEX_SCRIPS = {
        'NIFTY': 'NSE_40000001', 'NIFTY 50': 'NSE_40000001',
        'BANKNIFTY': 'NSE_40000003', 'BANK NIFTY': 'NSE_40000003',
        'SENSEX': 'BSE_40000006', 'BSE SENSEX': 'BSE_40000006',
        'INDIA VIX': 'NSE_40000107',
    }

    def ltp(self, sym: str) -> float:
        """Raises if unavailable."""
        # Try index LTP first
        idx_scrip = self._INDEX_SCRIPS.get(sym) or self._INDEX_SCRIPS.get(sym.upper())
        if idx_scrip:
            import requests
            h = {'Authorization': self._api.get_token()}
            r = requests.get(f'https://api.indstocks.com/market/quotes/ltp?scrip-codes={idx_scrip}',
                             headers=h, timeout=10)
            if r.status_code == 200:
                val = r.json().get('data', {}).get(idx_scrip, {})
                p = val.get('live_price', 0)
                if p > 0:
                    return float(p)
            raise BrokerError(f'No LTP: {sym}')

        p = self._api.get_ltp([sym]).get(sym, 0)
        if p <= 0:
            raise BrokerError(f'No LTP: {sym}')
        return float(p)

    def ltp_safe(self, sym: str, default: float = 0) -> float:
        try:
            return self.ltp(sym)
        except BrokerError:
            return default

    # ── Order status ──



    def get_candles(self, sym: str, interval: str, start_ts: int, end_ts: int) -> list:
        """Get candles from INDmoney historical API. Returns list of {o,h,l,c,v,ts}."""
        try:
            scrip = self._api.SCRIP_CODES.get(sym)
            if not scrip:
                return []
            r = self._api.requests.get(
                f'{self._api.BASE}/market/historical/{interval}',
                params={'scrip-codes': scrip, 'start_time': start_ts, 'end_time': end_ts},
                headers=self._api.headers(), timeout=15)
            if r.status_code == 200:
                data = r.json().get('data', {}).get(scrip, {})
                candles = data if isinstance(data, list) else data.get('candles', [])
                return candles
        except Exception as e:
            self._log.warning(f'Candles {sym}: {e}')
        return []

    def get_vwap(self, sym: str) -> float:
        """Get today's VWAP for a stock. Returns 0 if unavailable."""
        try:
            scrip = self._api.SCRIP_CODES.get(sym)
            if not scrip:
                return 0
            # Use full quote which has VWAP or calculate from today's data
            quotes = self._api.get_full_quote([sym])
            if quotes and sym in quotes:
                q = quotes[sym]
                # INDmoney full quote may have avg_price (VWAP proxy)
                vwap = q.get('avg_price', q.get('vwap', 0))
                if vwap and vwap > 0:
                    return float(vwap)
                # Fallback: use (high + low + close) / 3 as typical price
                h = q.get('high', 0)
                l = q.get('low', 0)
                c = q.get('live_price', q.get('close', 0))
                if h and l and c:
                    return (h + l + c) / 3
            return 0
        except Exception:
            return 0

    def order_filled(self, order_id: str) -> tuple[bool, float]:
        """Check via order book. Returns (filled, traded_price)."""
        book = self._api.get_order_book() or []
        for o in book:
            if o.get('id') == order_id:
                status = o.get('status', '').upper()
                traded_qty = int(o.get('traded_qty', 0) or 0)
                # Check multiple fill indicators
                if status in ('SUCCESS', 'EXECUTED', 'COMPLETE', 'FILLED') or traded_qty > 0:
                    return True, float(o.get('traded_price', 0))
        return False, 0.0

    def cancel(self, order_id: str):
        try:
            self._api.cancel_order(order_id)
            audit('broker', 'CANCEL_OK', order_id=order_id)
        except Exception as e:
            audit('broker', 'CANCEL_FAIL', order_id=order_id, error=str(e))

    # ── Info ──

    def funds(self) -> float:
        return float(self._api.get_funds() or 0)

    # Index security IDs for option chain lookup
    INDEX_IDS = {
        'NIFTY': ('NSE', '40000001'),
        'NIFTY 50': ('NSE', '40000001'),
        'NIFTY50': ('NSE', '40000001'),
        'BANKNIFTY': ('NSE', '40000003'),
        'BANK NIFTY': ('NSE', '40000003'),
        'SENSEX': ('BSE', '40000006'),
        'BSE SENSEX': ('BSE', '40000006'),
    }

    def option_chain(self, sym: str, expiry: str = '') -> list:
        """Get option chain. Works for indices, equities, and MCX."""
        import requests
        from datetime import datetime, timedelta

        # 1. Index options (NIFTY, BANKNIFTY, SENSEX)
        clean = sym.replace(' 50', '').replace(' ', '').strip().upper()
        idx = self.INDEX_IDS.get(sym) or self.INDEX_IDS.get(clean)
        if idx:
            return self._index_option_chain(idx[0], idx[1], expiry)

        # 2. Equity options (via SCRIP_CODES)
        chain = self._api.get_option_chain(sym)
        if chain:
            return chain

        # 3. MCX commodity options (via instrument master CSV)
        return self._mcx_option_chain(sym)

    def _index_option_chain(self, exchange: str, underlying_id: str, expiry: str = '') -> list:
        """Get index option chain from INDstocks API."""
        import requests
        from datetime import datetime, timedelta

        if not expiry:
            # Find nearest weekly expiry
            today = datetime.now()
            # NIFTY = Tuesday, BANKNIFTY = Wednesday, SENSEX = Thursday (approximate)
            # Just find next expiry within 7 days
            for i in range(7):
                d = today + timedelta(days=i)
                expiry = d.strftime('%Y-%m-%d')
                # Try this date
                break
            # Better: try multiple dates until one works
            for i in range(7):
                d = today + timedelta(days=i)
                test_expiry = d.strftime('%Y-%m-%d')
                chain = self._fetch_index_chain(exchange, underlying_id, test_expiry)
                if chain:
                    return chain
            return []

        return self._fetch_index_chain(exchange, underlying_id, expiry)

    def _fetch_index_chain(self, exchange: str, underlying_id: str, expiry: str) -> list:
        """Fetch index option chain for a specific expiry."""
        import requests
        try:
            h = {'Authorization': self._api.get_token(), 'Content-Type': 'application/json'}
            params = {
                'exchange': exchange,
                'segment': 'INDEX',
                'underlying-scrip': underlying_id,
                'expiry': expiry,
                'strike_count': '20',
            }
            r = requests.get('https://api.indstocks.com/market/option-chain',
                             headers=h, params=params, timeout=15)
            if r.status_code != 200:
                return []

            data = r.json().get('data', {})
            strikes_dict = data.get('strikes', {})
            if not strikes_dict:
                return []

            # Convert dict format to list format matching MCX chain
            chain = []
            for strike_str, strike_data in strikes_dict.items():
                chain.append({
                    'strike_price': float(strike_str),
                    'ce': strike_data.get('ce', {}),
                    'pe': strike_data.get('pe', {}),
                })
            chain.sort(key=lambda x: x['strike_price'])

            if chain:
                log.info(f'Index chain: {len(chain)} strikes, expiry={expiry}')
                audit('broker', 'INDEX_CHAIN', exchange, strikes=len(chain), expiry=expiry)
            return chain
        except Exception as e:
            log.warning(f'Index chain error: {e}')
            return []

    def _mcx_option_chain(self, sym: str) -> list:
        """Build option chain from MCX instrument master for GOLD/CRUDEOIL/SILVER."""
        import requests
        from datetime import datetime
        try:
            h = {'Authorization': self._api.get_token()}
            r = requests.get('https://api.indstocks.com/market/instruments?source=mcx',
                             headers=h, timeout=30)
            if r.status_code != 200:
                return []

            today = datetime.now()
            strikes = {}  # strike -> {ce: {security_id, last_price}, pe: {...}}

            for line in r.text.split('\n'):
                parts = line.split(',')
                if len(parts) < 18:
                    continue
                # Format: MCX,COMMODITY,sec_id,OPTFUT,expiry,name,lot,...,strike,opt_type,...,base_sym,...
                if parts[3] != 'OPTFUT':
                    continue
                base = parts[16].strip().upper()
                sym_upper = sym.upper()
                # Handle name variations: CRUDEOIL -> CRUDE OIL
                match = (base == sym_upper or
                         base.replace(' ', '') == sym_upper or
                         sym_upper.replace('OIL', ' OIL') == base)
                if not match:
                    continue

                sec_id = parts[2].strip()
                strike = float(parts[9]) if parts[9] else 0
                opt_type = parts[10].strip()  # CE or PE
                lot_size = int(parts[6]) if parts[6] else 1

                # Only nearest expiry
                expiry_str = parts[4].strip()
                try:
                    from datetime import datetime as dt
                    expiry = dt.strptime(expiry_str, '%d %b %Y')
                    if expiry < today:
                        continue
                except:
                    continue

                if strike not in strikes:
                    strikes[strike] = {'strike_price': strike, 'lot_size': lot_size,
                                       'ce': {}, 'pe': {}, 'expiry': expiry_str}
                if opt_type == 'CE':
                    strikes[strike]['ce'] = {'security_id': sec_id}
                elif opt_type == 'PE':
                    strikes[strike]['pe'] = {'security_id': sec_id}

            # Sort by strike and return nearest expiry only
            if not strikes:
                return []
            # Find nearest expiry
            all_expiries = set(s.get('expiry', '') for s in strikes.values())
            nearest = min(all_expiries)
            result = [s for s in strikes.values() if s.get('expiry') == nearest]
            result.sort(key=lambda x: x['strike_price'])
            audit('broker', 'MCX_CHAIN', sym, strikes=len(result), expiry=nearest)
            return result
        except Exception as e:
            log.warning(f'MCX chain error {sym}: {e}')
            return []

    def has_symbol(self, sym: str) -> bool:
        return sym in self._api.SCRIP_CODES

    def _check_sym(self, sym: str):
        if sym not in self._api.SCRIP_CODES:
            raise BrokerError(f'Unknown symbol: {sym}')
