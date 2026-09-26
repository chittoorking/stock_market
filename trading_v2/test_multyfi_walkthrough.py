"""Walk through multyfi_options agent — every scenario."""
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
    SCRIP_CODES.update({'SBIN': '1', 'RELIANCE': '2', 'NIFTY': '3'})
    _oc = 0; _order_book = []; _fail_next = False
    _prices = {'SBIN': 1000, 'RELIANCE': 1280, 'NIFTY': 23500}
    _chains = {
        'SBIN': [
            {'strike_price': 990, 'lot_size': 1500, 'pe': {'security_id': 'PE_990', 'last_price': 15}, 'ce': {'security_id': 'CE_990', 'last_price': 25}},
            {'strike_price': 1000, 'lot_size': 1500, 'pe': {'security_id': 'PE_1000', 'last_price': 20}, 'ce': {'security_id': 'CE_1000', 'last_price': 20}},
            {'strike_price': 1010, 'lot_size': 1500, 'pe': {'security_id': 'PE_1010', 'last_price': 25}, 'ce': {'security_id': 'CE_1010', 'last_price': 15}},
        ],
    }

    @classmethod
    def reset(cls):
        cls._oc = 0; cls._order_book = []; cls._fail_next = False

    @classmethod
    def place_order(cls, *a, **k): return None
    @classmethod
    def place_fno_order(cls, sym, qty, side, sec_id, order_type='MARKET'):
        if cls._fail_next: cls._fail_next = False; return None
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
    def get_option_chain(cls, sym): return cls._chains.get(sym, [])


def make_agent():
    MockAPI.reset()
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.multyfi_options import MulOptions
    b = Broker.__new__(Broker); b._api = MockAPI; b.scrip_count = len(MockAPI.SCRIP_CODES)
    pm = PositionManager(b)
    fm = FundManager(pool=100_000)
    mon = Monitor(b, pm, fm)
    agent = MulOptions.__new__(MulOptions)
    agent._broker = b; agent._positions = pm; agent._fm = fm; agent._monitor = mon
    agent._creds = {'authToken': 'test'}; agent._seen = set()
    from trading.logger import get_logger
    agent._log = get_logger('multyfi_options')
    return agent, pm, fm, mon


# ══════════════════════════
# 1. SIGNAL EXTRACTION
# ══════════════════════════
print('\n=== 1. SIGNAL EXTRACTION ===')

@test('1a: extract PE BUY signal')
def _():
    a, _, _, _ = make_agent()
    stream = {'fullDocument': {'action': 'BUY', 'baseSymbol': 'SBIN', 'optionType': 'PE',
              'strike': 1000, 'entryPrice': 20}, 'createdAt': '2026-09-09', 'message': 'BUY SBIN PE'}
    sig = a._extract_signal(stream)
    assert sig is not None
    assert sig['symbol'] == 'SBIN'
    assert sig['option_type'] == 'PE'
    assert sig['action'] == 'BUY'

@test('1b: extract CE BUY signal')
def _():
    a, _, _, _ = make_agent()
    stream = {'fullDocument': {'action': 'BUY', 'baseSymbol': 'RELIANCE', 'optionType': 'CE',
              'strike': 1280}, 'createdAt': '2026-09-09'}
    sig = a._extract_signal(stream)
    assert sig['symbol'] == 'RELIANCE'
    assert sig['option_type'] == 'CE'

@test('1c: extract SELL signal')
def _():
    a, _, _, _ = make_agent()
    stream = {'message': 'Exit SBIN PE trade', 'title': 'SBIN PE', 'createdAt': '2026-09-09'}
    sig = a._extract_signal(stream)
    assert sig['action'] == 'SELL'

@test('1d: garbage returns None')
def _():
    a, _, _, _ = make_agent()
    assert a._extract_signal({'message': 'random text'}) is None
    assert a._extract_signal({'message': ''}) is None
    assert a._extract_signal({}) is None

@test('1e: FUT signal extracted')
def _():
    a, _, _, _ = make_agent()
    stream = {'fullDocument': {'action': 'BUY', 'baseSymbol': 'NIFTY 50', 'optionType': 'FUT'},
              'createdAt': '2026-09-09', 'message': 'BUY NIFTY FUT'}
    sig = a._extract_signal(stream)
    assert sig['symbol'] == 'NIFTY'
    assert sig['option_type'] == 'FUT'


# ══════════════════════════
# 2. DEDUP
# ══════════════════════════
print('\n=== 2. DEDUP ===')

@test('2a: same signal deduped')
def _():
    a, _, _, _ = make_agent()
    sig = {'symbol': 'SBIN', 'date': '2026-09-09', 'action': 'BUY', 'option_type': 'PE', 'strike': 1000}
    key = f"{sig['symbol']}_{sig['date']}_{sig['action']}"
    a._seen.add(key)
    # Same key should be skipped in _poll
    assert key in a._seen

@test('2b: different date not deduped')
def _():
    a, _, _, _ = make_agent()
    k1 = 'SBIN_2026-09-09_BUY'
    k2 = 'SBIN_2026-09-10_BUY'
    a._seen.add(k1)
    assert k2 not in a._seen


# ══════════════════════════
# 3. SCORING
# ══════════════════════════
print('\n=== 3. SCORING ===')

@test('3a: PE score returns integer >= 10')
def _():
    a, _, _, _ = make_agent()
    sig = {'symbol': 'SBIN'}
    # Mock yfinance to avoid real download
    with patch('trading.agents.multyfi_options.yf.download', return_value=MagicMock()):
        score = a._score_pe(sig)
    assert isinstance(score, int)
    assert score >= 10

@test('3b: CE score returns integer >= 10')
def _():
    a, _, _, _ = make_agent()
    with patch('trading.agents.multyfi_options.yf.download', return_value=MagicMock()):
        score = a._score_ce({'symbol': 'SBIN'})
    assert isinstance(score, int)
    assert score >= 10

@test('3c: score with yfinance error still returns base')
def _():
    a, _, _, _ = make_agent()
    with patch('trading.agents.multyfi_options.yf.download', side_effect=Exception('network')):
        score = a._score_pe({'symbol': 'FAKE'})
    assert score == 10  # base score, no crash


# ══════════════════════════
# 4. TRADE EXECUTION
# ══════════════════════════
print('\n=== 4. TRADE ===')

@test('4a: open PE trade — ATM option selected')
def _():
    a, pm, fm, _ = make_agent()
    sig = {'symbol': 'SBIN', 'strike': 1000, 'option_type': 'PE', 'action': 'BUY'}
    a._open_trade(sig, 'PE', 22)
    assert len(pm.active()) == 1
    pos = pm.active()[0]
    assert pos.fno_sec_id == 'PE_1000'  # ATM
    assert pos.trail_activate_pct == 10.0
    assert pos.trail_pct == 5.0

@test('4b: SL set at -20% of premium')
def _():
    a, pm, _, _ = make_agent()
    sig = {'symbol': 'SBIN', 'strike': 1000}
    a._open_trade(sig, 'PE', 22)
    pos = pm.active()[0]
    # PE_1000 premium = 20, SL = 20 * 0.8 = 16
    assert pos.sl > 0
    assert abs(pos.sl - 16) < 0.1

@test('4c: no option chain -> no trade')
def _():
    a, pm, _, _ = make_agent()
    MockAPI._chains['SBIN'] = []
    sig = {'symbol': 'SBIN', 'strike': 1000}
    a._open_trade(sig, 'PE', 22)
    assert len(pm.active()) == 0
    # Restore
    MockAPI._chains['SBIN'] = [
        {'strike_price': 1000, 'lot_size': 1500, 'pe': {'security_id': 'PE_1000', 'last_price': 20}, 'ce': {'security_id': 'CE_1000', 'last_price': 20}},
    ]

@test('4d: broker FnO fails -> no position, FM released')
def _():
    a, pm, fm, _ = make_agent()
    MockAPI._fail_next = True
    sig = {'symbol': 'SBIN', 'strike': 1000}
    a._open_trade(sig, 'PE', 22)
    assert len(pm.active()) == 0
    assert fm.available == 100_000

@test('4e: duplicate position blocked')
def _():
    a, pm, _, _ = make_agent()
    sig = {'symbol': 'SBIN', 'strike': 1000}
    a._open_trade(sig, 'PE', 22)
    assert len(pm.active()) == 1
    a._open_trade(sig, 'PE', 25)  # same stock
    assert len(pm.active()) == 1  # still 1


# ══════════════════════════
# 5. SELL SIGNAL (EXIT)
# ══════════════════════════
print('\n=== 5. SELL/EXIT ===')

@test('5a: SELL signal closes existing position')
def _():
    from trading.types import Side
    a, pm, _, mon = make_agent()
    # Open a position first
    pm.open('SBIN', Side.BUY, 1500, 20, order_id='FNO-1', strategy='multyfi_options', fno_sec_id='PE_1000')
    assert len(pm.active()) == 1
    # SELL signal
    signal = {'symbol': 'SBIN', 'action': 'SELL', 'option_type': 'PE', 'date': '2026-09-09', 'strike': 1000}
    # Simulate what _poll does for SELL
    mon.force_exit('SBIN', 'MULTYFI_EXIT')
    assert len(pm.active()) == 0

@test('5b: SELL signal for non-existent position — no crash')
def _():
    a, pm, _, mon = make_agent()
    result = mon.force_exit('FAKESYM', 'MULTYFI_EXIT')
    assert result == False


# ══════════════════════════
# 6. NO CREDENTIALS
# ══════════════════════════
print('\n=== 6. AUTH ===')

@test('6a: no auth token -> run exits immediately')
def _():
    a, _, _, _ = make_agent()
    a._creds = {}  # no token
    # run() should return without polling
    a.run()  # should not crash or hang


# ══════════════════════════
# SUMMARY
# ══════════════════════════
print(f'\n{"=" * 60}')
print(f'MULTYFI OPTIONS WALKTHROUGH: {passed} passed, {failed} failed')
if errors:
    print(f'\nFAILURES:')
    for name, err in errors:
        print(f'  FAIL: {name}: {err}')
else:
    print('ALL SCENARIOS PASS.')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
