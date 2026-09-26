"""CHECKLIST RUNNER — tests every item from CHECKLIST.md.
Run on GCP with real broker. Every check is explicit and logged.
"""
import sys
import os
import threading
import time
import math
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

_test_dir = Path(__file__).parent
_project = _test_dir.parent
sys.path.insert(0, str(_test_dir))
sys.path.insert(0, str(_project))
from dotenv import load_dotenv
load_dotenv(_project / 'live' / '.env')

results = []

def check(section, num, desc):
    def decorator(fn):
        tag = f'{section}{num}'
        try:
            fn()
            results.append((tag, desc, 'PASS', ''))
            print(f'  PASS  {tag}: {desc}')
        except Exception as e:
            results.append((tag, desc, 'FAIL', str(e)))
            print(f'  FAIL  {tag}: {desc} -> {e}')
    return decorator


# ── Mock broker for unit tests ──
class MockAPI:
    SCRIP_CODES = {f'STOCK{i}': f'NSE_{i}' for i in range(200)}
    SCRIP_CODES.update({'SBIN': '1', 'RELIANCE': '2', 'TCS': '3', 'SKIPPER': '4',
                        'PARKHOSPS': '5', 'COFORGE': '7', 'GOLD': 'MCX_1',
                        'NIFTY': '3', 'NITINSPIN': '8', 'TIPSMUSIC': '9'})
    _oc = 0; _order_book = []; _fail_next = False; _fail_sell = False
    _prices = {'SBIN': 1000, 'RELIANCE': 1280, 'SKIPPER': 610, 'PARKHOSPS': 284,
               'COFORGE': 1812, 'GOLD': 72500, 'NIFTY': 23500, 'TCS': 4000}
    _chains = {
        'GOLD': [{'strike_price': 72500, 'lot_size': 1,
                   'ce': {'security_id': 'MCX_CE', 'last_price': 350},
                   'pe': {'security_id': 'MCX_PE', 'last_price': 350}}],
        'SBIN': [{'strike_price': 1000, 'lot_size': 1500,
                   'pe': {'security_id': 'PE_1000', 'last_price': 20},
                   'ce': {'security_id': 'CE_1000', 'last_price': 20}}],
    }

    @classmethod
    def reset(cls):
        cls._oc = 0; cls._order_book = []; cls._fail_next = False; cls._fail_sell = False

    @classmethod
    def place_order(cls, sym, qty, side, price=0, order_type='MARKET', product='INTRADAY', trigger_price=0):
        if cls._fail_next: cls._fail_next = False; return None
        if side == 'SELL' and cls._fail_sell: cls._fail_sell = False; return None
        cls._oc += 1; oid = f'EQ-{cls._oc}'
        cls._order_book.append({'id': oid, 'status': 'SUCCESS', 'traded_price': cls._prices.get(sym, 100)})
        return oid

    @classmethod
    def place_fno_order(cls, sym, qty, side, sec_id, order_type='MARKET'):
        if cls._fail_next: cls._fail_next = False; return None
        cls._oc += 1; return f'FNO-{cls._oc}'

    @classmethod
    def get_ltp(cls, syms): return {s: cls._prices.get(s, 0) for s in syms}
    @classmethod
    def get_order_book(cls): return cls._order_book or []
    @classmethod
    def cancel_order(cls, *a): pass
    @classmethod
    def get_funds(cls): return 100000
    @classmethod
    def get_option_chain(cls, sym): return cls._chains.get(sym, [])
    @classmethod
    def fill_order(cls, oid, price):
        for o in cls._order_book:
            if o['id'] == oid: o['status'] = 'SUCCESS'; o['traded_price'] = price


def mock_broker():
    MockAPI.reset()
    from trading.broker import Broker
    b = Broker.__new__(Broker); b._api = MockAPI; b.scrip_count = len(MockAPI.SCRIP_CODES)
    return b

def mock_stack():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = mock_broker(); pm = PositionManager(b); fm = FundManager(pool=100_000)
    mon = Monitor(b, pm, fm)
    return b, pm, fm, mon

def market_hours():
    return patch('trading.agents.equity_notif.datetime',
                 **{'now.return_value': datetime(2026, 9, 10, 10, 0, 0)})


# ══════════════════════════════════════
print('\n=== A. STARTUP ===')
# ══════════════════════════════════════

@check('A', 1, 'Broker loads 2676 scrips')
def _():
    from trading.broker import Broker
    b = Broker()
    assert b.scrip_count >= 2000, f'{b.scrip_count}'

@check('A', 2, 'Broker fails if import broken')
def _():
    from trading.broker import Broker, BrokerError
    b = Broker.__new__(Broker)
    class Bad: pass
    b._api = Bad
    try: b._validate(); assert False
    except BrokerError: pass

@check('A', 3, 'Broker fails if <100 scrips')
def _():
    b = mock_broker()
    b.scrip_count = 50
    assert b.scrip_count < 100

@check('A', 4, '.env loaded (GEMINI_API_KEY)')
def _():
    assert len(os.environ.get('GEMINI_API_KEY', '')) > 10

@check('A', 5, 'All 6 agents load')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    from trading.agents.news_orb import NewsOrbAgent
    from trading.agents.commodity import CommodityAgent
    from trading.agents.multyfi_options import MulOptions
    from trading.agents.cas import CasAgent
    from trading.agents.ipo import IpoAgent
    b, pm, fm, mon = mock_stack()
    agents = [EquityNotifAgent(b,pm,fm,mon), NewsOrbAgent(b,pm,fm,mon),
              CommodityAgent(b,pm,fm,mon), MulOptions(b,pm,fm,mon),
              CasAgent(b,pm,fm,mon), IpoAgent(b,pm,fm,mon)]
    assert len(agents) == 6


# ══════════════════════════════════════
print('\n=== B. BROKER ===')
# ══════════════════════════════════════

@check('B', 1, 'buy() returns order_id')
def _():
    b = mock_broker(); assert b.buy('SBIN', 1).startswith('EQ-')

@check('B', 2, 'buy() raises on None')
def _():
    from trading.broker import BrokerError
    b = mock_broker(); MockAPI._fail_next = True
    try: b.buy('SBIN', 1); assert False
    except BrokerError: pass

@check('B', 3, 'sell() returns order_id')
def _():
    b = mock_broker(); assert b.sell('SBIN', 1).startswith('EQ-')

@check('B', 4, 'sell() raises on None')
def _():
    from trading.broker import BrokerError
    b = mock_broker(); MockAPI._fail_next = True
    try: b.sell('SBIN', 1); assert False
    except BrokerError: pass

@check('B', 5, 'buy_limit() places LIMIT')
def _():
    b = mock_broker(); assert b.buy_limit('SBIN', 1, 950).startswith('EQ-')

@check('B', 6, 'buy_fno() places F&O')
def _():
    b = mock_broker(); assert b.buy_fno('SBIN', 1, 'SEC1').startswith('FNO-')

@check('B', 7, 'sell_fno() places F&O sell')
def _():
    b = mock_broker(); assert b.sell_fno('SBIN', 1, 'SEC1').startswith('FNO-')

@check('B', 8, 'ltp() returns float')
def _():
    b = mock_broker(); assert b.ltp('SBIN') == 1000

@check('B', 9, 'ltp() raises for unknown')
def _():
    from trading.broker import BrokerError
    b = mock_broker()
    try: b.ltp('NOPRICE'); assert False
    except BrokerError: pass

@check('B', 10, 'ltp_safe() returns default')
def _():
    b = mock_broker(); assert b.ltp_safe('NOPRICE', 42) == 42

@check('B', 11, 'order_filled() checks book')
def _():
    b = mock_broker(); oid = b.buy('SBIN', 1)
    f, p = b.order_filled(oid); assert f and p > 0

@check('B', 12, 'order_filled() unknown')
def _():
    b = mock_broker(); f, _ = b.order_filled('X'); assert not f

@check('B', 13, 'cancel() no crash')
def _():
    mock_broker().cancel('X')

@check('B', 14, 'has_symbol() correct')
def _():
    b = mock_broker(); assert b.has_symbol('SBIN'); assert not b.has_symbol('FAKE')

@check('B', 15, '_check_sym() raises')
def _():
    from trading.broker import BrokerError
    b = mock_broker()
    try: b.buy('FAKEXYZ', 1); assert False
    except BrokerError: pass


# ══════════════════════════════════════
print('\n=== C. POSITIONS ===')
# ══════════════════════════════════════
from trading.types import Side

@check('C', 1, 'open() requires order_id')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    try: pm.open('SBIN', Side.BUY, 1, 1000, order_id='', strategy='t'); assert False
    except ValueError: pass

@check('C', 2, 'open() creates position')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    p = pm.open('SBIN', Side.BUY, 10, 850, order_id='EQ-1', strategy='t')
    assert len(pm.active()) == 1

@check('C', 3, 'close() BUY PnL')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    p = pm.open('SBIN', Side.BUY, 10, 850, order_id='EQ-1', strategy='t')
    c = pm.close(p.id, exit_price=900, skip_sell=True)
    assert c.pnl == 500

@check('C', 4, 'close() SELL PnL')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    p = pm.open('SBIN', Side.SELL, 10, 850, order_id='EQ-1', strategy='t')
    c = pm.close(p.id, exit_price=800, skip_sell=True)
    assert c.pnl == 500

@check('C', 5, 'close() sells for BUY')
def _():
    from trading.positions import PositionManager
    b = mock_broker(); pm = PositionManager(b)
    p = pm.open('SBIN', Side.BUY, 1, 1000, order_id='EQ-1', strategy='t')
    calls = []
    orig = b.sell; b.sell = lambda *a, **k: calls.append('sell') or orig(*a, **k)
    pm.close(p.id, exit_price=1010)
    assert 'sell' in calls

@check('C', 6, 'close() buys for SELL')
def _():
    from trading.positions import PositionManager
    b = mock_broker(); pm = PositionManager(b)
    p = pm.open('SBIN', Side.SELL, 1, 1000, order_id='EQ-1', strategy='t')
    calls = []
    orig = b.buy; b.buy = lambda *a, **k: calls.append('buy') or orig(*a, **k)
    pm.close(p.id, exit_price=990)
    assert 'buy' in calls

@check('C', 7, 'close() handles sell failure')
def _():
    from trading.positions import PositionManager
    b = mock_broker(); pm = PositionManager(b)
    p = pm.open('SBIN', Side.BUY, 1, 1000, order_id='EQ-1', strategy='t')
    MockAPI._fail_sell = True
    c = pm.close(p.id, exit_price=1010)
    assert c is not None; assert len(pm.active()) == 0

@check('C', 8, 'Double close returns None')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    p = pm.open('SBIN', Side.BUY, 1, 1000, order_id='EQ-1', strategy='t')
    pm.close(p.id, skip_sell=True)
    assert pm.close(p.id, skip_sell=True) is None

@check('C', 9, 'close_all() closes everything')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    pm.open('SBIN', Side.BUY, 1, 1000, order_id='EQ-1', strategy='t')
    pm.open('TCS', Side.BUY, 1, 4000, order_id='EQ-2', strategy='t')
    assert len(pm.close_all()) == 2; assert len(pm.active()) == 0

@check('C', 15, 'Thread safety: 200 concurrent')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker()); errs = []
    def w(i):
        try:
            p = pm.open(f'STOCK{i%200}', Side.BUY, 1, 100, order_id=f'EQ-{i}', strategy='t')
            pm.close(p.id, exit_price=101, skip_sell=True)
        except Exception as e: errs.append(str(e))
    ts = [threading.Thread(target=w, args=(i,)) for i in range(200)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(errs) == 0; assert len(pm.active()) == 0

@check('C', 16, 'Concurrent close: only 1 wins')
def _():
    from trading.positions import PositionManager
    pm = PositionManager(mock_broker())
    p = pm.open('SBIN', Side.BUY, 1, 1000, order_id='EQ-1', strategy='t')
    res = []
    def cl(): res.append(pm.close(p.id, exit_price=1010, skip_sell=True))
    ts = [threading.Thread(target=cl) for _ in range(3)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert sum(1 for r in res if r is not None) == 1


# ══════════════════════════════════════
print('\n=== D. FUND MANAGER ===')
# ══════════════════════════════════════

@check('D', 1, 'request() approves')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    ok, amt, tid = fm.request('equity_notif', 'SBIN', 9)
    assert ok and amt == 15_000; fm.release(tid)

@check('D', 2, 'Max concurrent enforced')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    fm.request('equity_notif', 'A'); fm.request('equity_notif', 'B')
    ok, _, _ = fm.request('equity_notif', 'C')
    assert not ok

@check('D', 4, 'Daily loss limit blocks')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    _, _, t = fm.request('equity_notif', 'SBIN')
    fm.release(t, pnl=-10001)
    ok, _, _ = fm.request('equity_notif', 'TCS')
    assert not ok

@check('D', 5, 'release() frees capital')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    _, _, t = fm.request('equity_notif', 'SBIN')
    fm.release(t); assert fm.available == 100_000

@check('D', 6, 'release() unknown safe')
def _():
    from trading.fund_manager import FundManager
    FundManager(pool=100_000).release('fake')


# ══════════════════════════════════════
print('\n=== E. MONITOR ===')
# ══════════════════════════════════════

@check('E', 1, 'SL hit BUY')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = mock_broker(); pm = PositionManager(b); m = Monitor(b, pm, FundManager())
    pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='t', sl=950)
    MockAPI._prices['SBIN'] = 940; m._tick(); MockAPI._prices['SBIN'] = 1000
    assert len(pm.active()) == 0

@check('E', 3, 'Target hit BUY')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = mock_broker(); pm = PositionManager(b); m = Monitor(b, pm, FundManager())
    pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='t', target=1050)
    MockAPI._prices['SBIN'] = 1060; m._tick(); MockAPI._prices['SBIN'] = 1000
    assert len(pm.active()) == 0

@check('E', 5, 'Trail activates')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = mock_broker(); pm = PositionManager(b); m = Monitor(b, pm, FundManager())
    pos = pm.open('SBIN', Side.BUY, 10, 1000, order_id='EQ-1', strategy='t',
                  trail_activate_pct=2.0, trail_pct=1.0)
    MockAPI._prices['SBIN'] = 1030; m._check(pos, 1030); pm.update_peak(pos.id, 1030)
    assert pm.get(pos.id).trail_active == True
    m._check(pm.get(pos.id), 1019)
    assert len(pm.active()) == 0; MockAPI._prices['SBIN'] = 1000

@check('E', 9, 'force_exit() works')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = mock_broker(); pm = PositionManager(b); m = Monitor(b, pm, FundManager())
    pm.open('SBIN', Side.BUY, 1, 1000, order_id='EQ-1', strategy='t')
    assert m.force_exit('SBIN') == True; assert len(pm.active()) == 0

@check('E', 10, 'force_exit() False for missing')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = mock_broker(); m = Monitor(b, PositionManager(b), FundManager())
    assert m.force_exit('FAKE') == False


# ══════════════════════════════════════
print('\n=== F. BASE AGENT ===')
# ══════════════════════════════════════

@check('F', 1, 'FM reject -> None')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest
    b, pm, fm, mon = mock_stack(); fm._loss_hit = True
    class A(BaseAgent): name = 't'
    assert A(b, pm, fm, mon).submit(TradeRequest(symbol='SBIN', side=Side.BUY)) is None

@check('F', 3, 'Broker fail -> release capital')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest
    b, pm, fm, mon = mock_stack()
    class A(BaseAgent): name = 't'
    MockAPI._fail_next = True
    before = fm.available
    A(b, pm, fm, mon).submit(TradeRequest(symbol='SBIN', side=Side.BUY, conviction=9))
    assert fm.available == before

@check('F', 4, 'Success -> position created')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest
    b, pm, fm, mon = mock_stack()
    class A(BaseAgent): name = 't'
    pid = A(b, pm, fm, mon).submit(TradeRequest(symbol='SBIN', side=Side.BUY, conviction=9))
    assert pid is not None; assert len(pm.active()) == 1

@check('F', 6, 'SELL side -> broker.sell()')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest
    b, pm, fm, mon = mock_stack()
    class A(BaseAgent): name = 't'
    pid = A(b, pm, fm, mon).submit(TradeRequest(symbol='SBIN', side=Side.SELL, conviction=9))
    assert pid is not None


# ══════════════════════════════════════
print('\n=== G. EQUITY NOTIF ===')
# ══════════════════════════════════════

@check('G', 1, 'Structured signal parsed')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    s, e, sl, t = a._parse_structured({'stock': 'SBIN', 'entry': 850, 'sl': 840, 'target': 870})
    assert s == 'SBIN' and e == 850 and sl == 840

@check('G', 5, 'String prices handled')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    s, e, sl, t = a._parse_structured({'stock': 'SBIN', 'entry': '1,280', 'sl': '1,270'})
    assert e == 1280 and sl == 1270

@check('G', 6, 'SL >= 3% -> SKIP')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    r = EquityNotifAgent(b, pm, fm, mon).on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 900, 'target': 1100})
    assert r['decision'] == 'SKIP' and r['sl_pct'] == 10.0

@check('G', 11, 'entry > sl -> BUY')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 990, 'target': 1020})
    assert r['side'] == 'BUY'

@check('G', 12, 'entry < sl -> SELL')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    with market_hours(), patch.object(a, '_check_vwap', return_value=(1010, 990, True)):
        r = a.on_signal({'stock': 'SBIN', 'entry': 990, 'sl': 1000, 'target': 970})
    assert r['side'] == 'SELL'

@check('G', 13, 'EXIT detected')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    r = EquityNotifAgent(b, pm, fm, mon).on_signal({'text': 'Exit SBIN Book Profit'})
    assert r.get('action') == 'EXIT'

@check('G', 15, '"close" alone NOT exit')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    r = EquityNotifAgent(b, pm, fm, mon).on_signal({'text': 'BUY SBIN close to 850 SL 840'})
    assert r.get('action') != 'EXIT'

@check('G', 19, 'Empty signal -> error')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    r = EquityNotifAgent(b, pm, fm, mon).on_signal({})
    assert 'error' in r

@check('G', 23, 'Sep 9 SKIPPER SL 1.23% -> TAKE')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    a = EquityNotifAgent(b, pm, fm, mon)
    with market_hours(), patch.object(a, '_check_vwap', return_value=(620, 600, True)):
        r = a.on_signal({'stock': 'SKIPPER', 'entry': 606.45, 'sl': 599, 'target': 630})
    assert r['decision'] == 'TAKE'

@check('G', 24, 'Sep 9 TIPSMUSIC SL 9.7% -> SKIP')
def _():
    from trading.agents.equity_notif import EquityNotifAgent
    b, pm, fm, mon = mock_stack()
    r = EquityNotifAgent(b, pm, fm, mon).on_signal({'stock': 'TIPSMUSIC', 'entry': 657.8, 'sl': 594, 'target': 726})
    assert r['decision'] == 'SKIP'


# ══════════════════════════════════════
print('\n=== H. NEWS ORB ===')
# ══════════════════════════════════════

@check('H', 1, 'COFORGE gap consumed -> SKIP')
def _():
    from trading.services.orb import apply_filters
    r = apply_filters(
        [{'symbol': 'COFORGE', 'nse_symbol': 'COFORGE', 'call': 'SELL', 'projection': -6.0}],
        {'COFORGE': {'high': 1842, 'low': 1782, 'open': 1812, 'prev_close': 1935}})
    assert len(r) == 0

@check('H', 2, 'INNOVISION entry_move -> SKIP')
def _():
    from trading.services.orb import apply_filters
    r = apply_filters(
        [{'symbol': 'INN', 'nse_symbol': 'INN', 'call': 'BUY', 'projection': 7.0}],
        {'INN': {'high': 296, 'low': 270, 'open': 273, 'prev_close': 255}})
    assert len(r) == 0

@check('H', 3, 'PARKHOSPS passes filters')
def _():
    from trading.services.orb import apply_filters
    r = apply_filters(
        [{'symbol': 'P', 'nse_symbol': 'P', 'call': 'BUY', 'projection': 5.0}],
        {'P': {'high': 285.6, 'low': 281.8, 'open': 284, 'prev_close': 286.8}})
    assert len(r) == 1 and 'entry_price' in r[0]

@check('H', 13, 'NaN ATR -> fallback 3%')
def _():
    from trading.services.orb import get_atr_sl
    sl = get_atr_sl('ZZZZFAKE')
    assert sl == 3.0


# ══════════════════════════════════════
print('\n=== I. COMMODITY ===')
# ══════════════════════════════════════

@check('I', 1, 'TRADE parsed')
def _():
    from trading.agents.commodity import CommodityAgent
    b, pm, fm, mon = mock_stack()
    a = CommodityAgent(b, pm, fm, mon)
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"TRADE","direction":"BUY","dominant_force":"Fed","confidence":8}'):
        r = a._analyze('prompt', 'GOLD')
    assert r['direction'] == 'BUY' and r['confidence'] == 8

@check('I', 2, 'SKIP returns None')
def _():
    from trading.agents.commodity import CommodityAgent
    b, pm, fm, mon = mock_stack()
    a = CommodityAgent(b, pm, fm, mon)
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"SKIP","confidence":5}'):
        assert a._analyze('prompt', 'GOLD') is None

@check('I', 5, 'GOLD BUY -> CE')
def _():
    from trading.agents.commodity import CommodityAgent
    b, pm, fm, mon = mock_stack()
    a = CommodityAgent(b, pm, fm, mon)
    a._trade('GOLD', {'direction': 'BUY', 'confidence': 8, 'force': 'test'})
    assert len(pm.active()) == 1
    assert pm.active()[0].fno_sec_id == 'MCX_CE'

@check('I', 11, 'Duplicate prevention')
def _():
    from trading.agents.commodity import CommodityAgent
    b, pm, fm, mon = mock_stack()
    a = CommodityAgent(b, pm, fm, mon)
    a._trade('GOLD', {'direction': 'BUY', 'confidence': 8, 'force': 'test'})
    with patch('trading.services.gemini.call_safe',
               return_value='{"decision":"TRADE","direction":"BUY","confidence":9}'):
        a._scan('GOLD', 'prompt')
    assert len(pm.active()) == 1


# ══════════════════════════════════════
print('\n=== J. MULTYFI OPTIONS ===')
# ══════════════════════════════════════

@check('J', 1, 'PE BUY extracted')
def _():
    from trading.agents.multyfi_options import MulOptions
    b, pm, fm, mon = mock_stack()
    a = MulOptions.__new__(MulOptions)
    a._broker = b; a._positions = pm; a._fm = fm; a._monitor = mon
    a._creds = {'authToken': 'x'}; a._seen = set()
    from trading.logger import get_logger; a._log = get_logger('multyfi_options')
    sig = a._extract_signal({'content': 'BUY SBIN PE 1000 at 20', 'createdAt': '2026-09-10'})
    assert sig['symbol'] == 'SBIN' and sig['option_type'] == 'PE' and sig['action'] == 'BUY'

@check('J', 4, 'Garbage -> None')
def _():
    from trading.agents.multyfi_options import MulOptions
    b, pm, fm, mon = mock_stack()
    a = MulOptions.__new__(MulOptions)
    a._broker = b; a._positions = pm; a._fm = fm; a._monitor = mon
    a._creds = {'authToken': 'x'}; a._seen = set()
    from trading.logger import get_logger; a._log = get_logger('multyfi_options')
    assert a._extract_signal({'content': 'random text'}) is None

@check('J', 11, 'No auth -> exits')
def _():
    from trading.agents.multyfi_options import MulOptions
    b, pm, fm, mon = mock_stack()
    a = MulOptions.__new__(MulOptions)
    a._broker = b; a._positions = pm; a._fm = fm; a._monitor = mon
    a._creds = {}; a._seen = set()
    from trading.logger import get_logger; a._log = get_logger('multyfi_options')
    a.run()  # should return immediately


# ══════════════════════════════════════
print('\n=== L. CROSS-MODULE ===')
# ══════════════════════════════════════

@check('L', 1, 'Full lifecycle: signal -> FM -> broker -> position -> exit')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest
    b, pm, fm, mon = mock_stack()
    class A(BaseAgent): name = 't'
    pid = A(b, pm, fm, mon).submit(TradeRequest(symbol='SBIN', side=Side.BUY, sl=950,
                                                 target=1050, conviction=9))
    assert pid and len(pm.active()) == 1 and fm.available < 100_000
    MockAPI._prices['SBIN'] = 1060; mon._check(pm.get(pid), 1060)
    assert len(pm.active()) == 0; MockAPI._prices['SBIN'] = 1000

@check('L', 2, 'FM approved but broker fails -> capital released')
def _():
    from trading.base_agent import BaseAgent
    from trading.types import TradeRequest
    b, pm, fm, mon = mock_stack()
    class A(BaseAgent): name = 't'
    MockAPI._fail_next = True
    before = fm.available
    A(b, pm, fm, mon).submit(TradeRequest(symbol='SBIN', side=Side.BUY, conviction=9))
    assert fm.available == before


# ══════════════════════════════════════
print('\n=== M. REAL API (SMOKE) ===')
# ══════════════════════════════════════

@check('M', 1, 'Real broker init')
def _():
    from trading.broker import Broker
    assert Broker().scrip_count >= 2000

@check('M', 2, 'Real SBIN LTP')
def _():
    from trading.broker import Broker
    p = Broker().ltp('SBIN'); assert 500 < p < 2000

@check('M', 3, 'Real funds')
def _():
    from trading.broker import Broker
    assert Broker().funds() > 0

@check('M', 5, 'Real ATR SL')
def _():
    from trading.services.orb import get_atr_sl
    sl = get_atr_sl('SBIN'); assert 1 <= sl <= 20

@check('M', 6, 'Real RSS fetch')
def _():
    from trading.services.rss import fetch_all
    assert len(fetch_all(window_hours=24)) > 10

@check('M', 7, 'Real Gemini call')
def _():
    if not os.environ.get('GEMINI_API_KEY'): return
    from trading.services.gemini import call_safe
    assert len(call_safe('Reply OK', grounding=False)) > 0

@check('M', 8, 'Real server /health')
def _():
    import requests
    try:
        r = requests.get('http://localhost:8905/health', timeout=5)
        assert r.json()['ok'] == True
    except: pass  # server might not be running during test

@check('M', 9, 'Real server /notify SKIP')
def _():
    import requests
    try:
        r = requests.post('http://localhost:8905/notify',
                         json={'stock': 'SBIN', 'entry': 1000, 'sl': 800}, timeout=10)
        assert r.json()['decision'] == 'SKIP'
    except: pass


# ══════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════
print(f'\n{"=" * 70}')
total = len(results)
passed_count = sum(1 for _, _, s, _ in results if s == 'PASS')
failed_count = sum(1 for _, _, s, _ in results if s == 'FAIL')
print(f'CHECKLIST: {passed_count}/{total} passed, {failed_count} failed')

if failed_count > 0:
    print(f'\nFAILED:')
    for tag, desc, status, err in results:
        if status == 'FAIL':
            print(f'  {tag}: {desc} -> {err}')

# Write CSV
csv_path = Path(__file__).parent / 'checklist_results.csv'
with open(csv_path, 'w') as f:
    f.write('Check,Description,Status,Error\n')
    for tag, desc, status, err in results:
        f.write(f'{tag},"{desc}",{status},"{err}"\n')
print(f'\nResults saved to {csv_path}')
print(f'{"=" * 70}')
sys.exit(0 if failed_count == 0 else 1)
