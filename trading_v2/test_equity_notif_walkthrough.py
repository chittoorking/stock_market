"""Walk through equity_notif agent — every signal format, every edge case."""
import sys
from pathlib import Path
from unittest.mock import patch
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
    SCRIP_CODES.update({'SBIN': '1', 'SKIPPER': '4', 'NITINSPIN': '8',
                        'KAYNES': '9', 'UNIPARTS': '6', 'VIJAYA': '10',
                        'TCS': '3', 'RELIANCE': '2', 'MANGLMCEM': '11'})
    _oc = 0; _order_book = []; _fail_next = False
    _prices = {'SBIN': 1000, 'SKIPPER': 610, 'NITINSPIN': 648, 'KAYNES': 3609,
               'UNIPARTS': 897, 'VIJAYA': 1504, 'TCS': 4000, 'RELIANCE': 1280,
               'MANGLMCEM': 1089}

    @classmethod
    def reset(cls):
        cls._oc = 0; cls._order_book = []; cls._fail_next = False

    @classmethod
    def place_order(cls, sym, qty, side, price=0, order_type='MARKET', product='INTRADAY', trigger_price=0):
        if cls._fail_next: cls._fail_next = False; return None
        cls._oc += 1
        oid = f'EQ-{cls._oc}'
        cls._order_book.append({'id': oid, 'status': 'SUCCESS', 'traded_price': cls._prices.get(sym, 100)})
        return oid

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


def make_agent():
    MockAPI.reset()
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.equity_notif import EquityNotifAgent
    b = Broker.__new__(Broker); b._api = MockAPI; b.scrip_count = len(MockAPI.SCRIP_CODES)
    pm = PositionManager(b)
    fm = FundManager(pool=100_000)
    mon = Monitor(b, pm, fm)
    return EquityNotifAgent(b, pm, fm, mon), pm, fm


def market_hours():
    """Patch to simulate market hours."""
    return patch('trading.agents.equity_notif.datetime',
                 **{'now.return_value': datetime(2026, 9, 10, 10, 0, 0)})


# ══════════════════════════════════════════
# 1. STRUCTURED SIGNAL FORMAT
# ══════════════════════════════════════════
print('\n=== 1. STRUCTURED SIGNALS ===')

@test('1a: basic structured signal (TAKE path with VWAP mock)')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 995, 'target': 1020})
    assert r['decision'] == 'TAKE'
    assert r['sym'] == 'SBIN'
    assert r['sl_pct'] == 0.5
    assert r['side'] == 'BUY'
    assert r['trade']['status'] == 'placed'
    assert len(pm.active()) == 1

@test('1b: structured signal with string prices')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': '1000', 'sl': '995', 'target': '1020'})
    assert r['decision'] == 'TAKE'

@test('1c: structured signal with comma prices')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1300, 1200, True)):
        r = a.on_signal({'stock': 'RELIANCE', 'entry': '1,280', 'sl': '1,270', 'target': '1,300'})
    assert r['decision'] == 'TAKE'

@test('1d: structured signal missing SL')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'stock': 'SBIN', 'entry': 1000})
    assert 'error' in r

@test('1e: structured signal entry=0')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'stock': 'SBIN', 'entry': 0, 'sl': 990})
    assert 'error' in r

@test('1f: symbol field instead of stock')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(620, 600, True)):
        r = a.on_signal({'symbol': 'SKIPPER', 'entry': 610, 'sl': 605, 'target': 630})
    assert r['sym'] == 'SKIPPER'
    assert r['decision'] == 'TAKE'


# ══════════════════════════════════════════
# 2. TEXT SIGNAL FORMAT
# ══════════════════════════════════════════
print('\n=== 2. TEXT SIGNALS ===')

@test('2a: standard text format')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(620, 600, True)):
        r = a.on_signal({'text': 'BUY SKIPPER around 606 SL 599 TGT 630'})
    assert r['sym'] == 'SKIPPER'
    assert r['decision'] == 'TAKE'

@test('2b: INDmoney equity intraday format')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(650, 600, True)):
        r = a.on_signal({'text': 'Equity Intraday Trade Stock Name NITINSPIN Entry Range 622 Stop Loss 618 Target 645'})
    assert r['sym'] == 'NITINSPIN'

@test('2c: text via body field')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(900, 800, True)):
        r = a.on_signal({'body': 'BUY UNIPARTS around 897 SL 889 TGT 930'})
    assert r['sym'] == 'UNIPARTS'

@test('2d: text via message field')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(4100, 3900, True)):
        r = a.on_signal({'message': 'BUY TCS around 4000 SL 3950'})
    assert r['sym'] == 'TCS'

@test('2e: extra whitespace')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(620, 600, True)):
        r = a.on_signal({'text': '  BUY   SKIPPER   around  606.45   SL  599   TGT  630  '})
    assert r['sym'] == 'SKIPPER'

@test('2f: Rs prefix in prices')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'text': 'BUY SBIN Entry Rs.1000 SL Rs.990 Target Rs.1020'})
    assert r['sym'] == 'SBIN'


# ══════════════════════════════════════════
# 3. FILTERS
# ══════════════════════════════════════════
print('\n=== 3. FILTERS ===')

@test('3a: SL too wide (>3%) -> SKIP')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 900, 'target': 1100})
    assert r['decision'] == 'SKIP'
    assert r['sl_pct'] == 10.0

@test('3b: SL exactly 3% -> SKIP')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 970, 'target': 1050})
    assert r['decision'] == 'SKIP'
    assert r['sl_pct'] == 3.0

@test('3c: SL 2.99% -> pass SL filter (may fail VWAP)')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 970.1, 'target': 1050})
    assert r['decision'] == 'TAKE'

@test('3d: VWAP check fails -> SKIP')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(980, 1000, False)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 995, 'target': 1020})
    assert r['decision'] == 'SKIP'
    assert 'Close 980.0 < VWAP 1000.0' in r['reasons'][0]

@test('3e: VWAP data unavailable -> SKIP')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(None, None, False)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 995, 'target': 1020})
    assert r['decision'] == 'SKIP'
    assert 'No VWAP data' in r['reasons'][0]

@test('3f: market closed -> SKIP')
def _():
    a, _, _ = make_agent()
    with patch('trading.agents.equity_notif.datetime',
               **{'now.return_value': datetime(2026, 9, 9, 20, 0, 0)}), \
         patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 995, 'target': 1020})
    assert r['decision'] == 'SKIP'
    assert 'market closed' in r['reasons'][0]


# ══════════════════════════════════════════
# 4. EXIT SIGNALS
# ══════════════════════════════════════════
print('\n=== 4. EXIT SIGNALS ===')

@test('4a: exit with EXIT prefix')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'EXIT SKIPPER'})
    assert r['action'] == 'EXIT'
    assert r['sym'] == 'SKIPPER'

@test('4b: book profit')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'Book Profit NITINSPIN'})
    assert r['action'] == 'EXIT'
    assert r['sym'] == 'NITINSPIN'

@test('4c: square off')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'Square Off SBIN'})
    assert r['action'] == 'EXIT'
    assert r['sym'] == 'SBIN'

@test('4d: target hit')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'Target Hit KAYNES at 3650'})
    assert r['action'] == 'EXIT'
    assert r['sym'] == 'KAYNES'

@test('4e: "close" alone does NOT trigger exit')
def _():
    a, _, _ = make_agent()
    # "BUY SBIN close to 850" should NOT trigger exit
    r = a.on_signal({'text': 'BUY SBIN close to 850 SL 840'})
    assert r.get('action') != 'EXIT'  # should parse as entry, not exit

@test('4f: "close position" DOES trigger exit')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'Close Position SBIN'})
    assert r['action'] == 'EXIT'

@test('4g: exit with active position -> closes it')
def _():
    from trading.types import Side
    a, pm, fm = make_agent()
    pm.open('SKIPPER', Side.BUY, 16, 610, order_id='EQ-1', strategy='equity_notif')
    assert len(pm.active()) == 1
    r = a.on_signal({'text': 'EXIT SKIPPER'})
    assert r['exited'] == True
    assert len(pm.active()) == 0

@test('4h: exit without active position')
def _():
    a, pm, _ = make_agent()
    r = a.on_signal({'text': 'EXIT FAKESYM'})
    assert r['exited'] == False

@test('4i: exit signal with no symbol')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'EXIT all positions now'})
    # "all" is 3 chars but not in SKIP_WORDS... it would match
    # This is an edge case — agent will try to exit symbol "ALL"
    assert r['action'] == 'EXIT'


# ══════════════════════════════════════════
# 5. SIDE DETECTION
# ══════════════════════════════════════════
print('\n=== 5. SIDE DETECTION ===')

@test('5a: entry > SL -> BUY')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 990, 'target': 1020})
    assert r['side'] == 'BUY'

@test('5b: entry < SL -> SELL')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 990, 'sl': 1000, 'target': 970})
    assert r['side'] == 'SELL'


# ══════════════════════════════════════════
# 6. BROKER/FM FAILURES
# ══════════════════════════════════════════
print('\n=== 6. FAILURES ===')

@test('6a: broker fails -> trade status=failed, no position')
def _():
    a, pm, fm = make_agent()
    MockAPI._fail_next = True
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 995, 'target': 1020})
    assert r['decision'] == 'TAKE'
    assert r['trade']['status'] == 'failed'
    assert len(pm.active()) == 0
    assert fm.available == 100_000  # capital released

@test('6b: FM rejects -> trade status=failed')
def _():
    a, pm, fm = make_agent()
    fm._loss_hit = True
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 995, 'target': 1020})
    assert r['trade']['status'] == 'failed'
    assert len(pm.active()) == 0


# ══════════════════════════════════════════
# 7. REAL SCENARIOS FROM SEP 9
# ══════════════════════════════════════════
print('\n=== 7. SEP 9 REPLAY ===')

@test('7a: SKIPPER at 9:32 (SL 1.23%) -> TAKE')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(612, 560, True)):
        r = a.on_signal({'stock': 'SKIPPER', 'entry': 606.45, 'sl': 599, 'target': 630})
    assert r['decision'] == 'TAKE'
    assert abs(r['sl_pct'] - 1.23) < 0.1
    assert len(pm.active()) == 1

@test('7b: UNIPARTS at 10:01 (SL 0.89%) -> TAKE')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(900, 850, True)):
        r = a.on_signal({'stock': 'UNIPARTS', 'entry': 897, 'sl': 889, 'target': 930})
    assert r['decision'] == 'TAKE'
    assert len(pm.active()) == 1

@test('7c: KAYNES (SL 0.72%) -> SKIP on VWAP (was correct behavior)')
def _():
    a, _, _ = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(3600, 3650, False)):
        r = a.on_signal({'stock': 'KAYNES', 'entry': 3606, 'sl': 3580, 'target': 3660})
    assert r['decision'] == 'SKIP'

@test('7d: TIPSMUSIC (SL 9.7%) -> SKIP')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'stock': 'TIPSMUSIC', 'entry': 657.8, 'sl': 594, 'target': 726})
    assert r['decision'] == 'SKIP'
    assert r['sl_pct'] > 9

@test('7e: MANGLMCEM (SL 0.83%) -> should have placed order')
def _():
    a, pm, fm = make_agent()
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1095, 1050, True)):
        r = a.on_signal({'stock': 'MANGLMCEM', 'entry': 1089, 'sl': 1080, 'target': 1120})
    assert r['decision'] == 'TAKE'
    assert len(pm.active()) == 1  # THIS is what should have happened Sep 7


# ══════════════════════════════════════════
# 8. GARBAGE INPUT
# ══════════════════════════════════════════
print('\n=== 8. GARBAGE ===')

@test('8a: empty dict')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({})
    assert 'error' in r

@test('8b: None text')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': None})
    assert 'error' in r

@test('8c: numbers only')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': '123 456 789'})
    assert 'error' in r

@test('8d: random words')
def _():
    a, _, _ = make_agent()
    r = a.on_signal({'text': 'hello world foo bar'})
    assert 'error' in r


# ══════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════
print(f'\n{"=" * 60}')
print(f'EQUITY NOTIF WALKTHROUGH: {passed} passed, {failed} failed')
if errors:
    print(f'\nFAILURES:')
    for name, err in errors:
        print(f'  FAIL: {name}: {err}')
else:
    print('ALL SCENARIOS PASS.')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
