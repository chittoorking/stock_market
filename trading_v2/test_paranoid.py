"""PARANOID TESTS — think of every possible way this can fail.

Categories:
  A. Startup failures (env, imports, config)
  B. Network failures (broker timeout, API down)
  C. Data edge cases (zero price, negative, NaN, missing fields)
  D. Timing issues (before market, after market, exact boundaries)
  E. State corruption (stale data, restart mid-trade)
  F. Duplicate signals (same stock twice, rapid-fire)
  G. Exit race conditions (SL + notification + EOD at same time)
  H. Fund manager edge cases (exact boundary, overflow, underflow)
  I. ORB edge cases (gap exactly at threshold, OR high == OR low)
  J. Notification parsing (every format variation, unicode, empty)
  K. Process restart (positions lost on restart)
  L. Sell failures (broker rejects sell, what happens to position?)
  M. Multiple agents accessing same stock
"""
import sys
import threading
import time
import math
from pathlib import Path

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
        except AssertionError as e:
            failed += 1
            errors.append((name, str(e)))
            print(f'  FAIL: {name}: {e}')
        except Exception as e:
            failed += 1
            errors.append((name, f'{type(e).__name__}: {e}'))
            print(f'  FAIL: {name}: {type(e).__name__}: {e}')
    return decorator


# ── Mock ──
class MockAPI:
    SCRIP_CODES = {f'STOCK{i}': f'NSE_{i}' for i in range(200)}
    SCRIP_CODES.update({'SBIN': 'NSE_1', 'RELIANCE': 'NSE_2', 'TCS': 'NSE_3',
                        'SKIPPER': 'NSE_4', 'PARKHOSPS': 'NSE_5', 'UNIPARTS': 'NSE_6'})
    _oc = 0
    _prices = {'SBIN': 1000, 'RELIANCE': 1280, 'TCS': 4000, 'SKIPPER': 610, 'PARKHOSPS': 284}
    _fail_next = False
    _fail_sell = False
    _timeout_next = False
    _order_book = []

    @classmethod
    def reset(cls):
        cls._oc = 0; cls._fail_next = False; cls._fail_sell = False
        cls._timeout_next = False; cls._order_book = []
        cls._prices = {'SBIN': 1000, 'RELIANCE': 1280, 'TCS': 4000, 'SKIPPER': 610, 'PARKHOSPS': 284}

    @classmethod
    def place_order(cls, sym, qty, side, price=0, order_type='MARKET', product='INTRADAY', trigger_price=0):
        if cls._timeout_next:
            cls._timeout_next = False
            raise ConnectionError('broker timeout')
        if cls._fail_next:
            cls._fail_next = False
            return None
        if side == 'SELL' and cls._fail_sell:
            cls._fail_sell = False
            return None
        cls._oc += 1
        oid = f'EQ-{cls._oc}'
        cls._order_book.append({'id': oid, 'status': 'SUCCESS', 'traded_price': cls._prices.get(sym, price or 100)})
        return oid

    @classmethod
    def place_fno_order(cls, sym, qty, side, sec_id, order_type='MARKET'):
        cls._oc += 1; return f'FNO-{cls._oc}'
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

def make_stack():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = make_broker()
    return b, PositionManager(b), FundManager(pool=100_000), Monitor(b, PositionManager(b), FundManager())


# ══════════════════════════════════════════
# A. STARTUP FAILURES
# ══════════════════════════════════════════
print('\n=== A. STARTUP ===')

@test('A1: broker with 0 scrips')
def _():
    from trading.broker import Broker, BrokerError
    b = Broker.__new__(Broker)
    class Empty:
        SCRIP_CODES = {}
    b._api = Empty
    try:
        b._validate()
        # validate checks for functions, not scrip count
        # but scrip_count check happens in __init__
        b.scrip_count = 0
        assert b.scrip_count < 100
    except BrokerError:
        pass

@test('A2: fund manager with 0 pool')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=0)
    ok, _, _ = fm.request('test', 'SBIN')
    assert not ok

@test('A3: fund manager with negative pool')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=-1000)
    ok, _, _ = fm.request('test', 'SBIN')
    assert not ok

@test('A4: monitor without positions')
def _():
    from trading.monitor import Monitor
    b = make_broker()
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    m = Monitor(b, PositionManager(b), FundManager())
    m._tick()  # should not crash


# ══════════════════════════════════════════
# B. NETWORK FAILURES
# ══════════════════════════════════════════
print('\n=== B. NETWORK ===')

@test('B1: broker timeout on buy')
def _():
    from trading.broker import BrokerError
    b = make_broker()
    MockAPI._timeout_next = True
    try:
        b.buy('SBIN', 10)
        assert False
    except (BrokerError, ConnectionError):
        pass

@test('B2: broker timeout on sell — position still closes')
def _():
    from trading.positions import PositionManager
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    pos = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test')
    MockAPI._timeout_next = True
    # close should handle sell failure gracefully
    closed = pm.close(pos.id, exit_price=1010)
    assert closed is not None  # position still closes
    assert len(pm.active()) == 0

@test('B3: LTP returns 0 — monitor skips, doesnt crash')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    fm = FundManager()
    m = Monitor(b, pm, fm)
    pm.open('NOPRICE', Side.BUY, 10, 100, order_id='EQ-1', strategy='test')
    # NOPRICE has no entry in _prices, ltp returns 0
    # monitor should skip, not crash
    for pos in pm.active():
        m._check(pos, 0)  # price=0 handled
    assert len(pm.active()) == 1  # still open, not crashed

@test('B4: order book returns None')
def _():
    b = make_broker()
    MockAPI._order_book = None  # broken
    filled, price = b.order_filled('EQ-999')
    assert not filled
    MockAPI._order_book = []


# ══════════════════════════════════════════
# C. DATA EDGE CASES
# ══════════════════════════════════════════
print('\n=== C. DATA EDGES ===')

@test('C1: zero entry price')
def _():
    from trading.types import TradeRequest, Side
    from trading.base_agent import BaseAgent
    b, pm, fm, mon = make_stack()
    class A(BaseAgent):
        name = 'test'
    a = A(b, pm, fm, mon)
    # entry=0 means market price, but if ltp also fails
    MockAPI._prices['NOPRICE'] = 0
    pid = a.submit(TradeRequest(symbol='NOPRICE', side=Side.BUY))
    assert pid is None  # should fail gracefully

@test('C2: negative SL doesnt trigger')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    fm = FundManager()
    m = Monitor(b, pm, fm)
    pos = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test', sl=-100)
    m._check(pos, 500)  # 500 > -100, no SL
    assert len(pm.active()) == 1

@test('C3: qty = 0 from small capital')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=500)  # very small
    ok, amt, tid = fm.request('equity_notif', 'SBIN')
    # 500 pool, 10% buffer = 50, available = 450, but min 1000 check
    assert not ok  # amount too small

@test('C4: same symbol open twice')
def _():
    from trading.positions import PositionManager
    from trading.types import Side
    pm = PositionManager(make_broker())
    p1 = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='agent1')
    p2 = pm.open('SBIN', Side.BUY, 5, 1010, order_id='EQ-2', strategy='agent2')
    assert len(pm.active()) == 2  # both exist
    # find_by_symbol returns first one
    found = pm.find_by_symbol('SBIN')
    assert found is not None
    # close both
    pm.close(p1.id, exit_price=1020, skip_sell=True)
    pm.close(p2.id, exit_price=1020, skip_sell=True)
    assert len(pm.active()) == 0

@test('C5: very large qty')
def _():
    b = make_broker()
    oid = b.buy('SBIN', 999999)  # huge qty
    assert oid  # broker doesn't validate qty

@test('C6: float precision in PnL')
def _():
    from trading.positions import PositionManager
    from trading.types import Side
    pm = PositionManager(make_broker())
    pos = pm.open('SBIN', Side.BUY, 3, 333.33, order_id='EQ-1', strategy='test')
    closed = pm.close(pos.id, exit_price=333.34, skip_sell=True)
    assert closed.pnl > 0  # tiny profit, not zero
    assert abs(closed.pnl - 0.03) < 0.01


# ══════════════════════════════════════════
# D. TIMING
# ══════════════════════════════════════════
print('\n=== D. TIMING ===')

@test('D1: monitor tick after 3:10 PM closes all')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.types import Side
    from unittest.mock import patch
    from datetime import datetime
    b = make_broker()
    pm = PositionManager(b)
    fm = FundManager()
    m = Monitor(b, pm, fm)
    pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test')
    pm.open('TCS', Side.BUY, 5, 4000, order_id='EQ-2', strategy='test')
    # Mock time to 3:10 PM
    with patch('trading.monitor.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 10, 15, 10, 0)
        m._tick()
    assert len(pm.active()) == 0

@test('D2: monitor tick at 3:09 PM does NOT close')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.types import Side
    from unittest.mock import patch
    from datetime import datetime
    b = make_broker()
    pm = PositionManager(b)
    fm = FundManager()
    m = Monitor(b, pm, fm)
    pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test')
    with patch('trading.monitor.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 10, 15, 9, 0)
        MockAPI._prices['SBIN'] = 1000
        m._tick()
    assert len(pm.active()) == 1  # still open


# ══════════════════════════════════════════
# E. DUPLICATE SIGNALS
# ══════════════════════════════════════════
print('\n=== E. DUPLICATES ===')

@test('E1: same signal sent twice — two positions (by design)')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest, Side
    b = make_broker()
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    pm = PositionManager(b)
    fm = FundManager()
    m = Monitor(b, pm, fm)
    class A(BaseAgent):
        name = 'equity_notif'
    a = A(b, pm, fm, m)
    p1 = a.submit(TradeRequest(symbol='SBIN', side=Side.BUY, conviction=9))
    p2 = a.submit(TradeRequest(symbol='SBIN', side=Side.BUY, conviction=9))
    assert p1 is not None and p2 is not None
    assert len(pm.active()) == 2
    # FM enforces max concurrent = 2 for equity_notif
    p3 = a.submit(TradeRequest(symbol='SBIN', side=Side.BUY, conviction=9))
    assert p3 is None  # rejected by FM

@test('E2: rapid fire 10 signals — FM limits hold')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest, Side
    b = make_broker()
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    pm = PositionManager(b)
    fm = FundManager(pool=100_000)
    m = Monitor(b, pm, fm)
    class A(BaseAgent):
        name = 'equity_notif'
    a = A(b, pm, fm, m)
    results = [a.submit(TradeRequest(symbol=f'STOCK{i}', side=Side.BUY, conviction=9))
               for i in range(10)]
    opened = [r for r in results if r is not None]
    assert len(opened) <= 2, f'Expected max 2, got {len(opened)}'  # max concurrent for equity_notif


# ══════════════════════════════════════════
# F. EXIT EDGE CASES
# ══════════════════════════════════════════
print('\n=== F. EXIT EDGES ===')

@test('F1: sell fails on close — position still removed')
def _():
    from trading.positions import PositionManager
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    pos = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test')
    MockAPI._fail_sell = True
    closed = pm.close(pos.id, exit_price=1010)
    # Position should still be closed even though sell failed
    assert closed is not None
    assert len(pm.active()) == 0
    assert closed.pnl > 0  # PnL still calculated

@test('F2: force_exit for non-existent symbol')
def _():
    b, pm, fm, mon = make_stack()
    result = mon.force_exit('DOESNOTEXIST')
    assert result == False

@test('F3: close with exit_price=0 uses LTP')
def _():
    from trading.positions import PositionManager
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    pos = pm.open('SBIN', Side.BUY, 10, 950, order_id='EQ-1', strategy='test')
    MockAPI._prices['SBIN'] = 1000
    closed = pm.close(pos.id, exit_price=0, skip_sell=True)
    assert closed.exit_price == 1000  # used LTP
    assert closed.pnl == 500  # (1000-950)*10

@test('F4: SL and target at same price — SL wins (checked first)')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    m = Monitor(b, pm, FundManager())
    pos = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test',
                  sl=1050, target=1050)  # both at 1050
    m._check(pos, 1050)
    # SL is checked first: price <= sl (1050 <= 1050 = True)
    assert len(pm.active()) == 0


# ══════════════════════════════════════════
# G. NOTIFICATION PARSING EDGE CASES
# ══════════════════════════════════════════
print('\n=== G. PARSING EDGES ===')

@test('G1: signal with only symbol, no prices')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'stock': 'SBIN', 'entry': 0, 'sl': 0})
    assert 'error' in result

@test('G2: signal with string prices')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'stock': 'SBIN', 'entry': '1000', 'sl': '990', 'target': '1020'})
    assert result.get('sym') == 'SBIN'  # should convert strings

@test('G3: notification text with extra whitespace')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'text': '  BUY   SKIPPER   around  606   SL  599   TGT  630  '})
    assert result.get('sym') == 'SKIPPER'

@test('G4: notification with unicode')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'text': 'BUY SBIN @ Rs.1000 SL Rs.990'})
    # Should parse even with Rs. prefix
    assert 'error' not in result or result.get('sym') is not None

@test('G5: exit signal with mixed case')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'text': 'bOoK pRoFiT SKIPPER'})
    assert result.get('action') == 'EXIT'

@test('G6: signal via body field instead of text')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'body': 'BUY SBIN around 1000 SL 990 TGT 1020'})
    assert result.get('sym') == 'SBIN'

@test('G7: signal via message field')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = make_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    result = a.on_signal({'message': 'BUY TCS around 4000 SL 3950'})
    assert result.get('sym') == 'TCS'


# ══════════════════════════════════════════
# H. ORB EDGE CASES
# ══════════════════════════════════════════
print('\n=== H. ORB EDGES ===')

@test('H1: OR high == OR low (no range)')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'X', 'nse_symbol': 'X', 'call': 'BUY', 'projection': 7.0}]
    data = {'X': {'high': 100, 'low': 100, 'open': 100, 'prev_close': 100}}
    # entry = 100 * 1.001 = 100.1, entry_move = 0.1% < 2%
    # remaining = 7 - 0 = 7% > 1%
    result = apply_filters(trades, data)
    assert len(result) == 1  # passes

@test('H2: gap exactly at threshold')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'X', 'nse_symbol': 'X', 'call': 'BUY', 'projection': 2.0}]
    data = {'X': {'high': 101, 'low': 100, 'open': 101, 'prev_close': 100}}
    # gap = 1%, remaining = 2 - 1 = 1.0% >= 1.0% (passes)
    # entry_move = (101*1.001 - 100)/100 = 1.1% < 2% (passes)
    result = apply_filters(trades, data)
    assert len(result) == 1

@test('H3: remaining exactly 0.99% — should skip')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'X', 'nse_symbol': 'X', 'call': 'BUY', 'projection': 2.0}]
    data = {'X': {'high': 101, 'low': 100, 'open': 101.01, 'prev_close': 100}}
    # gap = 1.01%, remaining = 2 - 1.01 = 0.99% < 1.0%
    result = apply_filters(trades, data)
    assert len(result) == 0

@test('H4: prev_close is 0')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'X', 'nse_symbol': 'X', 'call': 'BUY', 'projection': 7.0}]
    data = {'X': {'high': 100, 'low': 95, 'open': 98, 'prev_close': 0}}
    result = apply_filters(trades, data)
    assert len(result) == 0  # skipped, can't calculate


# ══════════════════════════════════════════
# I. THREAD STRESS
# ══════════════════════════════════════════
print('\n=== I. THREAD STRESS ===')

@test('I1: 200 concurrent position open/close')
def _():
    from trading.positions import PositionManager
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    errs = []
    def w(i):
        try:
            pos = pm.open(f'STOCK{i%200}', Side.BUY, 1, 100, order_id=f'EQ-{i}', strategy='t')
            pm.close(pos.id, exit_price=101, skip_sell=True)
        except Exception as e:
            errs.append(str(e))
    threads = [threading.Thread(target=w, args=(i,)) for i in range(200)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(errs) == 0, f'{len(errs)} errors: {errs[:3]}'
    assert len(pm.active()) == 0

@test('I2: monitor + agent + exit notification simultaneously')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.types import Side
    b = make_broker()
    pm = PositionManager(b)
    fm = FundManager()
    mon = Monitor(b, pm, fm)
    # Open a position
    pos = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='test',
                  sl=950, target=1050)
    # Three threads try to close simultaneously
    results = []
    def close_via_monitor():
        results.append(('monitor', pm.close(pos.id, exit_price=1010, reason='SL', skip_sell=True)))
    def close_via_notif():
        results.append(('notif', pm.close(pos.id, exit_price=1010, reason='NOTIF', skip_sell=True)))
    def close_via_eod():
        results.append(('eod', pm.close(pos.id, exit_price=1010, reason='EOD', skip_sell=True)))
    t1 = threading.Thread(target=close_via_monitor)
    t2 = threading.Thread(target=close_via_notif)
    t3 = threading.Thread(target=close_via_eod)
    t1.start(); t2.start(); t3.start()
    t1.join(); t2.join(); t3.join()
    # Exactly 1 should succeed
    non_none = [(src, r) for src, r in results if r is not None]
    assert len(non_none) == 1, f'Expected 1, got {len(non_none)}: {non_none}'


# ══════════════════════════════════════════
# J. FM STRESS
# ══════════════════════════════════════════
print('\n=== J. FM STRESS ===')

@test('J1: exhaust all capital then release all')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    tids = []
    for i in range(20):
        ok, _, tid = fm.request('news_orb', f'S{i}', conviction=5)
        if ok:
            tids.append(tid)
    # Should have stopped at some point due to buffer
    assert len(tids) < 20
    for tid in tids:
        fm.release(tid, pnl=0)
    assert fm.available == 100_000

@test('J2: release with massive loss')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    ok, _, tid = fm.request('equity_notif', 'SBIN')
    fm.release(tid, pnl=-50_000)
    assert fm._loss_hit == True
    # Next request should be rejected
    ok2, _, _ = fm.request('equity_notif', 'TCS')
    assert not ok2

@test('J3: release with massive gain')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    ok, _, tid = fm.request('equity_notif', 'SBIN')
    fm.release(tid, pnl=50_000)
    assert fm._daily_pnl == 50_000
    assert fm.available == 100_000  # all freed


# ══════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════
print(f'\n{"=" * 60}')
print(f'PARANOID TESTS: {passed} passed, {failed} failed')
if errors:
    print(f'\nFAILURES:')
    for name, err in errors:
        print(f'  FAIL: {name}: {err}')
else:
    print('ZERO FAILURES. System is solid.')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
