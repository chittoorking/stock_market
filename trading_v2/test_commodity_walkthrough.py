"""Walk through commodity agent — every scenario."""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

passed = 0
failed = 0
errors = []

def test(name):
    def decorator(fn):
        global passed, failed
        try:
            fn()
            passed += 1
            print(f'  PASS: {name}')
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f'  FAIL: {name}: {e}')
    return decorator


class MockAPI:
    SCRIP_CODES = {f'STOCK{i}': f'NSE_{i}' for i in range(200)}
    SCRIP_CODES.update({'GOLD': 'MCX_1', 'CRUDEOIL': 'MCX_2'})
    _oc = 0; _order_book = []; _fail_next = False
    _prices = {'GOLD': 72500, 'CRUDEOIL': 5800}
    _option_chains = {
        'GOLD': [
            {'strike_price': 72000, 'lot_size': 1, 'ce': {'security_id': 'MCX_CE_72000', 'last_price': 600}, 'pe': {'security_id': 'MCX_PE_72000', 'last_price': 200}},
            {'strike_price': 72500, 'lot_size': 1, 'ce': {'security_id': 'MCX_CE_72500', 'last_price': 350}, 'pe': {'security_id': 'MCX_PE_72500', 'last_price': 350}},
            {'strike_price': 73000, 'lot_size': 1, 'ce': {'security_id': 'MCX_CE_73000', 'last_price': 150}, 'pe': {'security_id': 'MCX_PE_73000', 'last_price': 600}},
        ],
        'CRUDEOIL': [
            {'strike_price': 5750, 'lot_size': 100, 'ce': {'security_id': 'MCX_CE_5750'}, 'pe': {'security_id': 'MCX_PE_5750'}},
            {'strike_price': 5800, 'lot_size': 100, 'ce': {'security_id': 'MCX_CE_5800'}, 'pe': {'security_id': 'MCX_PE_5800'}},
        ],
    }

    @classmethod
    def reset(cls):
        cls._oc = 0; cls._order_book = []; cls._fail_next = False

    @classmethod
    def place_order(cls, *a, **k): return None  # equity — shouldn't be called
    @classmethod
    def place_fno_order(cls, sym, qty, side, sec_id, order_type='MARKET'):
        if cls._fail_next: cls._fail_next = False; return None
        cls._oc += 1; return f'MCX-{cls._oc}'
    @classmethod
    def get_ltp(cls, syms): return {s: cls._prices.get(s, 0) for s in syms}
    @classmethod
    def get_order_book(cls): return cls._order_book
    @classmethod
    def cancel_order(cls, *a): pass
    @classmethod
    def get_funds(cls): return 100000
    @classmethod
    def get_option_chain(cls, sym): return cls._option_chains.get(sym, [])


def make_agent():
    MockAPI.reset()
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.commodity import CommodityAgent
    b = Broker.__new__(Broker); b._api = MockAPI; b.scrip_count = len(MockAPI.SCRIP_CODES)
    pm = PositionManager(b)
    fm = FundManager(pool=100_000)
    mon = Monitor(b, pm, fm)
    return CommodityAgent(b, pm, fm, mon), pm, fm


# ══════════════════════════
# 1. GEMINI RESPONSE PARSING
# ══════════════════════════
print('\n=== 1. PARSING ===')

@test('1a: TRADE response parsed correctly')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"TRADE","direction":"BUY","dominant_force":"Fed pause","confidence":8}'):
        result = a._analyze('prompt', 'GOLD')
    assert result is not None
    assert result['direction'] == 'BUY'
    assert result['confidence'] == 8

@test('1b: SKIP response returns None')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"SKIP","dominant_force":"competing forces","confidence":5}'):
        result = a._analyze('prompt', 'GOLD')
    assert result is None

@test('1c: low confidence returns None')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"TRADE","direction":"BUY","dominant_force":"weak signal","confidence":4}'):
        result = a._analyze('prompt', 'GOLD')
    assert result is None

@test('1d: Gemini returns empty')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe', return_value=''):
        result = a._analyze('prompt', 'GOLD')
    assert result is None

@test('1e: Gemini returns garbage')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe', return_value='not json at all'):
        result = a._analyze('prompt', 'GOLD')
    assert result is None

@test('1f: Gemini returns JSON with markdown fences')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe',
               return_value='```json\n{"decision":"TRADE","direction":"SELL","dominant_force":"war","confidence":9}\n```'):
        result = a._analyze('prompt', 'GOLD')
    assert result is not None
    assert result['direction'] == 'SELL'

@test('1g: invalid direction ignored')
def _():
    a, _, _ = make_agent()
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"TRADE","direction":"HOLD","confidence":8}'):
        result = a._analyze('prompt', 'GOLD')
    assert result is None


# ══════════════════════════
# 2. TRADE EXECUTION
# ══════════════════════════
print('\n=== 2. TRADE EXECUTION ===')

@test('2a: GOLD BUY -> CE option bought')
def _():
    a, pm, fm = make_agent()
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'Fed pause'}
    a._trade('GOLD', signal)
    assert len(pm.active()) == 1
    pos = pm.active()[0]
    assert pos.symbol == 'GOLD'
    assert pos.fno_sec_id == 'MCX_CE_72500'  # ATM strike
    assert pos.trail_activate_pct == 20.0
    assert pos.trail_pct == 10.0

@test('2b: GOLD SELL -> PE option bought')
def _():
    a, pm, fm = make_agent()
    signal = {'direction': 'SELL', 'confidence': 9, 'force': 'dollar rally'}
    a._trade('GOLD', signal)
    pos = pm.active()[0]
    assert pos.fno_sec_id == 'MCX_PE_72500'

@test('2c: SL set at -30% of premium')
def _():
    a, pm, fm = make_agent()
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'test'}
    a._trade('GOLD', signal)
    pos = pm.active()[0]
    # ATM CE at 72500, premium = 350, SL = 350 * 0.7 = 245
    assert pos.sl > 0
    assert abs(pos.sl - 245) < 1

@test('2d: no option chain -> no trade')
def _():
    a, pm, _ = make_agent()
    MockAPI._option_chains['GOLD'] = []
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'test'}
    a._trade('GOLD', signal)
    assert len(pm.active()) == 0
    MockAPI._option_chains['GOLD'] = [  # restore
        {'strike_price': 72500, 'lot_size': 1, 'ce': {'security_id': 'MCX_CE_72500', 'last_price': 350}, 'pe': {'security_id': 'MCX_PE_72500', 'last_price': 350}},
    ]

@test('2e: no LTP -> no trade')
def _():
    a, pm, _ = make_agent()
    MockAPI._prices['GOLD'] = 0
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'test'}
    a._trade('GOLD', signal)
    assert len(pm.active()) == 0
    MockAPI._prices['GOLD'] = 72500  # restore

@test('2f: broker FnO fails -> no position, FM released')
def _():
    a, pm, fm = make_agent()
    MockAPI._fail_next = True
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'test'}
    a._trade('GOLD', signal)
    assert len(pm.active()) == 0
    assert fm.available == 100_000


# ══════════════════════════
# 3. DUPLICATE PREVENTION
# ══════════════════════════
print('\n=== 3. DUPLICATES ===')

@test('3a: already in GOLD -> skip second scan')
def _():
    a, pm, fm = make_agent()
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'test'}
    a._trade('GOLD', signal)
    assert len(pm.active()) == 1
    # Second scan should skip
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"TRADE","direction":"BUY","confidence":9}'):
        a._scan('GOLD', 'prompt')
    assert len(pm.active()) == 1  # still 1, not 2

@test('3b: after GOLD position closed, can trade again')
def _():
    a, pm, fm = make_agent()
    signal = {'direction': 'BUY', 'confidence': 8, 'force': 'test'}
    a._trade('GOLD', signal)
    pos = pm.active()[0]
    pm.close(pos.id, exit_price=400, skip_sell=True)
    assert len(pm.active()) == 0
    # Should be able to trade again
    a._active_commodities.discard('GOLD')  # _scan does this
    a._trade('GOLD', signal)
    assert len(pm.active()) == 1

@test('3c: GOLD and CRUDE can trade simultaneously')
def _():
    a, pm, fm = make_agent()
    a._trade('GOLD', {'direction': 'BUY', 'confidence': 8, 'force': 'gold'})
    a._trade('CRUDEOIL', {'direction': 'SELL', 'confidence': 9, 'force': 'crude'})
    assert len(pm.active()) == 2


# ══════════════════════════
# SUMMARY
# ══════════════════════════
print(f'\n{"=" * 60}')
print(f'COMMODITY WALKTHROUGH: {passed} passed, {failed} failed')
if errors:
    print(f'\nFAILURES:')
    for name, err in errors:
        print(f'  FAIL: {name}: {err}')
else:
    print('ALL SCENARIOS PASS.')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
