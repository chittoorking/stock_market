"""Walk through news_orb agent execution step by step.

Simulates Sep 9 2026 — the day everything broke.
Tests: does the new code handle every scenario correctly?
"""
import sys
import time
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
    SCRIP_CODES.update({'COFORGE': 'NSE_7', 'PARKHOSPS': 'NSE_5', 'INNOVISION': 'NSE_8'})
    _oc = 0
    _prices = {'COFORGE': 1812, 'PARKHOSPS': 284, 'INNOVISION': 273}
    _order_book = []
    _fail_next = False

    @classmethod
    def reset(cls):
        cls._oc = 0; cls._order_book = []; cls._fail_next = False
        cls._prices = {'COFORGE': 1812, 'PARKHOSPS': 284, 'INNOVISION': 273}

    @classmethod
    def place_order(cls, sym, qty, side, price=0, order_type='MARKET', product='INTRADAY', trigger_price=0):
        if cls._fail_next: cls._fail_next = False; return None
        cls._oc += 1
        oid = f'EQ-{cls._oc}'
        cls._order_book.append({'id': oid, 'status': 'PENDING', 'traded_price': 0})
        return oid

    @classmethod
    def fill_order(cls, oid, price):
        """Simulate exchange filling an order."""
        for o in cls._order_book:
            if o['id'] == oid:
                o['status'] = 'SUCCESS'
                o['traded_price'] = price

    @classmethod
    def place_fno_order(cls, *a, **k): cls._oc += 1; return f'FNO-{cls._oc}'
    @classmethod
    def get_ltp(cls, syms): return {s: cls._prices.get(s, 0) for s in syms}
    @classmethod
    def get_order_book(cls): return cls._order_book
    @classmethod
    def cancel_order(cls, *a): pass
    @classmethod
    def get_funds(cls): return 100000
    @classmethod
    def get_option_chain(cls, s): return []


def make_broker():
    MockAPI.reset()
    from trading.broker import Broker
    b = Broker.__new__(Broker)
    b._api = MockAPI
    b.scrip_count = len(MockAPI.SCRIP_CODES)
    return b

def make_full_stack():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.news_orb import NewsOrbAgent
    b = make_broker()
    pm = PositionManager(b, persist=False)
    fm = FundManager(pool=100_000)
    mon = Monitor(b, pm, fm)
    agent = NewsOrbAgent(b, pm, fm, mon)
    return b, pm, fm, mon, agent


# ══════════════════════════════════════════════
# SCENARIO 1: Sep 9 replay — COFORGE already gapped
# ══════════════════════════════════════════════
print('\n=== SCENARIO 1: COFORGE (gap consumed projection) ===')

@test('S1: COFORGE proj=-6%, gap=-6.3% -> remaining=-0.3% -> SKIP')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'COFORGE', 'nse_symbol': 'COFORGE', 'call': 'SELL', 'projection': -6.0}]
    or_data = {'COFORGE': {'high': 1842, 'low': 1782, 'open': 1812, 'prev_close': 1935}}
    result = apply_filters(trades, or_data)
    assert len(result) == 0, f'Should skip, got {len(result)}'


# ══════════════════════════════════════════════
# SCENARIO 2: INNOVISION already moved 16%
# ══════════════════════════════════════════════
print('\n=== SCENARIO 2: INNOVISION (entry move too big) ===')

@test('S2: INNOVISION proj=+7%, OR high=296, prev_close=255 -> entry_move=16% -> SKIP')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'INNOVISION', 'nse_symbol': 'INNOVISION', 'call': 'BUY', 'projection': 7.0}]
    or_data = {'INNOVISION': {'high': 296, 'low': 270, 'open': 273, 'prev_close': 255}}
    result = apply_filters(trades, or_data)
    assert len(result) == 0, f'Should skip, entry_move too big'


# ══════════════════════════════════════════════
# SCENARIO 3: PARKHOSPS — correct trade
# ══════════════════════════════════════════════
print('\n=== SCENARIO 3: PARKHOSPS (correct ORB trade) ===')

@test('S3a: PARKHOSPS passes filters')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'PARKHOSPS', 'nse_symbol': 'PARKHOSPS', 'call': 'BUY', 'projection': 5.0}]
    or_data = {'PARKHOSPS': {'high': 285.6, 'low': 281.8, 'open': 284, 'prev_close': 286.8}}
    result = apply_filters(trades, or_data)
    assert len(result) == 1
    assert 'entry_price' in result[0]
    # gap = |284-286.8|/286.8 = 0.98%, remaining = 5 - 0.98 = 4.02% > 1%
    # entry_move = (285.6*1.001 - 286.8)/286.8 = -0.33% (abs < 2%)

@test('S3b: PARKHOSPS limit order placed')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    filtered = [{'symbol': 'PARKHOSPS', 'nse_symbol': 'PARKHOSPS', 'call': 'BUY',
                 'projection': 5.0, 'entry_price': 285.9}]
    pending = agent._place_limit_orders(filtered)
    assert len(pending) == 1
    assert pending[0]['order_id'].startswith('EQ-')
    assert pending[0]['trade_id'].startswith('news_orb_')
    # FM capital deployed
    assert fm.available < 100_000

@test('S3c: PARKHOSPS fill detected via order book')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    filtered = [{'symbol': 'PARKHOSPS', 'nse_symbol': 'PARKHOSPS', 'call': 'BUY',
                 'projection': 5.0, 'entry_price': 285.9}]
    pending = agent._place_limit_orders(filtered)
    oid = pending[0]['order_id']

    # Simulate fill
    MockAPI.fill_order(oid, 283.9)

    # Mock time to be during market hours (not past deadline)
    with patch('trading.services.orb.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 9, 10, 0, 0)
        with patch('trading.services.orb.time.sleep'):
            agent._process_fills(pending)

    active = pm.active()
    assert len(active) == 1, f'Expected 1 position, got {len(active)}'
    pos = active[0]
    assert pos.symbol == 'PARKHOSPS', f'Expected PARKHOSPS, got {pos.symbol}'
    assert pos.entry_price == 283.9, f'Expected 283.9, got {pos.entry_price}'
    assert pos.sl > 0, f'Expected SL > 0, got {pos.sl}'

@test('S3d: PARKHOSPS fill NOT detected (not filled yet)')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    filtered = [{'symbol': 'PARKHOSPS', 'nse_symbol': 'PARKHOSPS', 'call': 'BUY',
                 'projection': 5.0, 'entry_price': 285.9}]
    pending = agent._place_limit_orders(filtered)
    # DON'T fill the order — it stays PENDING

    # Mock deadline to force cancellation
    with patch('trading.services.orb.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 9, 14, 45, 0)
        agent._process_fills(pending)

    # Position should NOT be created
    assert len(pm.active()) == 0
    # FM capital should be released
    assert fm.available == 100_000


# ══════════════════════════════════════════════
# SCENARIO 4: Broker failure mid-flow
# ══════════════════════════════════════════════
print('\n=== SCENARIO 4: BROKER FAILURE ===')

@test('S4: broker fails on limit order -> FM capital released')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    MockAPI._fail_next = True
    filtered = [{'symbol': 'PARKHOSPS', 'nse_symbol': 'PARKHOSPS', 'call': 'BUY',
                 'projection': 5.0, 'entry_price': 285.9}]
    pending = agent._place_limit_orders(filtered)
    assert len(pending) == 0  # no order placed
    assert fm.available == 100_000  # capital released


# ══════════════════════════════════════════════
# SCENARIO 5: SELL trade (COFORGE if it hadn't gapped)
# ══════════════════════════════════════════════
print('\n=== SCENARIO 5: SELL TRADE ===')

@test('S5: SELL limit order placed correctly')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    filtered = [{'symbol': 'COFORGE', 'nse_symbol': 'COFORGE', 'call': 'SELL',
                 'projection': -6.0, 'entry_price': 1780.0}]
    pending = agent._place_limit_orders(filtered)
    assert len(pending) == 1
    assert pending[0]['call'] == 'SELL'

@test('S5b: SELL position has correct SL direction')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    filtered = [{'symbol': 'COFORGE', 'nse_symbol': 'COFORGE', 'call': 'SELL',
                 'projection': -6.0, 'entry_price': 1780.0}]
    pending = agent._place_limit_orders(filtered)
    MockAPI.fill_order(pending[0]['order_id'], 1780.0)

    with patch('trading.services.orb.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 9, 10, 0, 0)
        with patch('trading.services.orb.time.sleep'):
            agent._process_fills(pending)

    active = pm.active()
    assert len(active) == 1, f'Expected 1 SELL position, got {len(active)}'
    pos = active[0]
    assert pos.side.value == 'SELL', f'Expected SELL, got {pos.side}'
    assert pos.sl > pos.entry_price, f'SL {pos.sl} should be > entry {pos.entry_price} for SELL'


# ══════════════════════════════════════════════
# SCENARIO 6: Dedup conflicts
# ══════════════════════════════════════════════
print('\n=== SCENARIO 6: DEDUP ===')

@test('S6: same stock in two batches, different calls -> keep first')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    trades = [
        {'symbol': 'SBIN', 'nse_symbol': 'SBIN', 'call': 'BUY', 'projection': 5.0},
        {'symbol': 'SBIN', 'nse_symbol': 'SBIN', 'call': 'SELL', 'projection': -4.0},
    ]
    result = agent._dedup(trades)
    assert len(result) == 1
    assert result[0]['call'] == 'BUY'  # first wins

@test('S6b: same stock same call -> dedup to one')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    trades = [
        {'symbol': 'SBIN', 'nse_symbol': 'SBIN', 'call': 'BUY', 'projection': 5.0},
        {'symbol': 'SBIN', 'nse_symbol': 'SBIN', 'call': 'BUY', 'projection': 6.0},
    ]
    result = agent._dedup(trades)
    assert len(result) == 1


# ══════════════════════════════════════════════
# SCENARIO 7: Late start (after 9:30)
# ══════════════════════════════════════════════
print('\n=== SCENARIO 7: LATE START ===')

@test('S7: start after 9:30 -> skip OR collection')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    with patch('trading.agents.news_orb.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 9, 9, 35, 0)
        result = agent._wait_for_or_window()
    assert result == False

@test('S7b: start at 9:10 -> wait then proceed')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    # Just test the logic, don't actually wait
    with patch('trading.agents.news_orb.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 9, 9, 25, 0)
        result = agent._wait_for_or_window()
    assert result == True  # between 9:15 and 9:30, proceed


# ══════════════════════════════════════════════
# SCENARIO 8: FM rejects (daily loss hit)
# ══════════════════════════════════════════════
print('\n=== SCENARIO 8: FM REJECTION ===')

@test('S8: FM daily loss -> all trades rejected')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    fm._loss_hit = True
    filtered = [
        {'symbol': 'PARKHOSPS', 'nse_symbol': 'PARKHOSPS', 'call': 'BUY',
         'projection': 5.0, 'entry_price': 285.9},
        {'symbol': 'COFORGE', 'nse_symbol': 'COFORGE', 'call': 'SELL',
         'projection': -6.0, 'entry_price': 1780.0},
    ]
    pending = agent._place_limit_orders(filtered)
    assert len(pending) == 0
    assert fm.available == 100_000


# ══════════════════════════════════════════════
# SCENARIO 9: Multiple fills at different times
# ══════════════════════════════════════════════
print('\n=== SCENARIO 9: MULTIPLE FILLS ===')

@test('S9: two orders, one fills, one cancelled at deadline')
def _():
    b, pm, fm, mon, agent = make_full_stack()
    MockAPI.SCRIP_CODES['STOCKA'] = 'NSE_A'
    MockAPI.SCRIP_CODES['STOCKB'] = 'NSE_B'
    MockAPI._prices['STOCKA'] = 100
    MockAPI._prices['STOCKB'] = 200

    filtered = [
        {'symbol': 'STOCKA', 'nse_symbol': 'STOCKA', 'call': 'BUY',
         'projection': 7.0, 'entry_price': 101.0},
        {'symbol': 'STOCKB', 'nse_symbol': 'STOCKB', 'call': 'BUY',
         'projection': 6.0, 'entry_price': 201.0},
    ]
    pending = agent._place_limit_orders(filtered)
    assert len(pending) == 2

    # Fill only STOCKA
    MockAPI.fill_order(pending[0]['order_id'], 101.0)
    # STOCKB stays PENDING

    # Process with deadline mock
    with patch('trading.services.orb.datetime') as mock_dt:
        # First check: not deadline yet
        call_count = [0]
        def fake_now():
            call_count[0] += 1
            if call_count[0] < 3:
                return datetime(2026, 9, 9, 10, 0, 0)  # 10 AM
            return datetime(2026, 9, 9, 14, 45, 0)  # deadline
        mock_dt.now = fake_now
        with patch('trading.services.orb.time.sleep'):
            agent._process_fills(pending)

    assert len(pm.active()) == 1  # only STOCKA
    assert pm.active()[0].symbol == 'STOCKA'


# ══════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════
print(f'\n{"=" * 60}')
print(f'NEWS ORB WALKTHROUGH: {passed} passed, {failed} failed')
if errors:
    print(f'\nFAILURES:')
    for name, err in errors:
        print(f'  FAIL: {name}: {err}')
else:
    print('ALL SCENARIOS PASS.')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
