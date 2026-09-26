"""LIVE TEST — every agent, every functionality, real broker."""
import sys
import time
from pathlib import Path

_td = Path(__file__).parent
sys.path.insert(0, str(_td))
sys.path.insert(0, str(_td.parent))
from dotenv import load_dotenv
load_dotenv(_td.parent / 'live' / '.env')

from trading.broker import Broker
from trading.positions import PositionManager
from trading.fund_manager import FundManager
from trading.monitor import Monitor
from trading.types import Side
from trading.services import orb, gemini, rss

b = Broker()
pm = PositionManager(b)
fm = FundManager(pool=100_000)
mon = Monitor(b, pm, fm, poll_interval=5)
mon.start()

passed = 0
failed = 0

def test(name):
    def dec(fn):
        global passed, failed
        try:
            fn()
            passed += 1
            print(f'  PASS: {name}')
        except Exception as e:
            failed += 1
            print(f'  FAIL: {name} -> {e}')
    return dec

def cleanup():
    for p in pm.active():
        pm.close(p.id)
    fm.reset_daily()

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('AGENT 1: EQUITY NOTIF')
print('=' * 60)
from trading.agents.equity_notif import EquityNotifAgent
eq = EquityNotifAgent(b, pm, fm, mon)

@test('EQ1: BUY real order (NHPC)')
def _():
    cleanup()
    ltp = b.ltp('NHPC')
    sl = round(ltp * 0.99, 2)
    tgt = round(ltp * 1.02, 2)
    r = eq.on_signal({'stock': 'NHPC', 'entry': ltp, 'sl': sl, 'target': tgt})
    assert r['decision'] == 'TAKE', f'Got {r.get("decision")} {r.get("reasons")}'
    assert r['trade']['status'] == 'placed'
    assert len(pm.active()) == 1
    pos = pm.active()[0]
    print(f'    Order {pos.order_id} qty={pos.qty} @ {pos.entry_price}')

@test('EQ2: Monitor keeps position alive')
def _():
    time.sleep(6)
    assert len(pm.active()) == 1

@test('EQ3: EXIT via notification')
def _():
    r = eq.on_signal({'text': 'Exit NHPC Book Profit'})
    assert r['exited'] == True
    assert len(pm.active()) == 0
    print(f'    Sold. FM available: {fm.available}')

@test('EQ4: SKIP wide SL')
def _():
    r = eq.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 900, 'target': 1100})
    assert r['decision'] == 'SKIP'

@test('EQ5: Empty signal error')
def _():
    assert 'error' in eq.on_signal({})

@test('EQ6: INDmoney text parsed')
def _():
    cleanup()
    r = eq.on_signal({'text': 'Equity Intraday Trade Stock Name NHPC Entry Range 76 Stop Loss 75 Target 78'})
    assert r.get('sym') == 'NHPC'
    print(f'    Parsed: {r.get("sym")} decision={r.get("decision")}')
    cleanup()

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('AGENT 2: NEWS ORB SERVICES')
print('=' * 60)

@test('ORB1: RSS fetch')
def _():
    a = rss.fetch_all()
    assert len(a) > 50
    print(f'    {len(a)} articles')

@test('ORB2: Gemini INTRADAY/DELIVERY')
def _():
    trades = gemini.analyze_stocks(
        [{'title': 'SRG Housing loans Rs 400 cr fictitious says NHB', 'source': 'ET', 'pubDate': '?'}],
        '2026-09-10')
    if trades:
        print(f'    {trades[0]["symbol"]} {trades[0]["projection"]:+.1f}%')
    else:
        print(f'    Skipped (below threshold)')

@test('ORB3: ATR SL real')
def _():
    sl = orb.get_atr_sl('SBIN')
    assert 1 <= sl <= 20
    print(f'    SBIN ATR SL: {sl}%')

@test('ORB4: Filter pass')
def _():
    r = orb.apply_filters(
        [{'symbol': 'X', 'nse_symbol': 'X', 'call': 'BUY', 'projection': 7.0}],
        {'X': {'high': 101, 'low': 100, 'open': 100.2, 'prev_close': 100}})
    assert len(r) == 1

@test('ORB5: Filter reject gap')
def _():
    r = orb.apply_filters(
        [{'symbol': 'X', 'nse_symbol': 'X', 'call': 'BUY', 'projection': 5.0}],
        {'X': {'high': 105, 'low': 100, 'open': 104.5, 'prev_close': 100}})
    assert len(r) == 0

@test('ORB6: Limit order + fill detection')
def _():
    cleanup()
    ltp = b.ltp('NHPC')
    oid = b.buy_limit('NHPC', 1, round(ltp + 0.5, 2))
    time.sleep(3)
    filled, price = b.order_filled(oid)
    print(f'    Limit {oid}: filled={filled} price={price}')
    if filled:
        b.sell('NHPC', 1)

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('AGENT 3: COMMODITY')
print('=' * 60)
from trading.agents.commodity import CommodityAgent
com = CommodityAgent(b, pm, fm, mon)

@test('COM1: Gold analysis')
def _():
    r = com._analyze(
        'Check gold. Output JSON: {"decision":"TRADE/SKIP","direction":"BUY/SELL","dominant_force":"X","confidence":1-10}',
        'GOLD')
    if r:
        print(f'    GOLD: {r["direction"]} conf={r["confidence"]}')
    else:
        print(f'    GOLD: SKIP')

@test('COM2: Duplicate prevention')
def _():
    cleanup()
    com._active_commodities.add('GOLD')
    pm.open('GOLD', Side.BUY, 1, 72500, order_id='T-G', strategy='commodity')
    before = len(pm.active())
    com._scan('GOLD', 'prompt')
    assert len(pm.active()) == before
    print(f'    Duplicate blocked')
    pm.close_all()
    com._active_commodities.clear()

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('AGENT 4: MULTYFI OPTIONS')
print('=' * 60)
from trading.agents.multyfi_options import MulOptions
mul = MulOptions.__new__(MulOptions)
mul._broker = b; mul._positions = pm; mul._fm = fm; mul._monitor = mon
mul._creds = {}; mul._seen = set()
from trading.logger import get_logger
mul._log = get_logger('multyfi_options')

@test('MUL1: Extract signal')
def _():
    sig = mul._extract_signal({'content': 'BUY SBIN PE 1000 at 20', 'createdAt': '2026-09-10'})
    assert sig and sig['symbol'] == 'SBIN' and sig['option_type'] == 'PE'

@test('MUL2: No auth exits')
def _():
    mul.run()

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('AGENT 5: CAS')
print('=' * 60)
from trading.agents.cas import CasAgent
cas = CasAgent(b, pm, fm, mon)

@test('CAS1: Thursday = expiry')
def _():
    from datetime import datetime
    assert datetime.now().weekday() == 3

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('AGENT 6: IPO')
print('=' * 60)
from trading.agents.ipo import IpoAgent
ipo = IpoAgent(b, pm, fm, mon)

@test('IPO1: Check listings')
def _():
    listings = ipo._get_listings()
    print(f'    Listings today: {len(listings)}')

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('MONITOR: SL + TARGET + FORCE EXIT')
print('=' * 60)

@test('MON1: SL hit auto-closes')
def _():
    cleanup()
    ltp = b.ltp('NHPC')
    oid = b.buy('NHPC', 1)
    time.sleep(2)
    # SL above current = triggers immediately
    ok, _, tid = fm.request('test', 'NHPC')
    pm.open('NHPC', Side.BUY, 1, ltp, order_id=oid, strategy='test',
            sl=ltp + 1, trade_id=tid)
    time.sleep(7)
    assert len(pm.active()) == 0
    print(f'    SL closed')

@test('MON2: Target hit auto-closes')
def _():
    cleanup()
    ltp = b.ltp('NHPC')
    oid = b.buy('NHPC', 1)
    time.sleep(2)
    ok, _, tid = fm.request('test', 'NHPC')
    pm.open('NHPC', Side.BUY, 1, ltp, order_id=oid, strategy='test',
            target=ltp - 1, trade_id=tid)
    time.sleep(7)
    assert len(pm.active()) == 0
    print(f'    Target closed')

@test('MON3: Force exit')
def _():
    cleanup()
    ltp = b.ltp('NHPC')
    oid = b.buy('NHPC', 1)
    time.sleep(2)
    pm.open('NHPC', Side.BUY, 1, ltp, order_id=oid, strategy='test')
    assert mon.force_exit('NHPC') == True
    assert len(pm.active()) == 0
    print(f'    Force exit OK')

# ══════════════════════════════════════
print('\n' + '=' * 60)
print('FUND MANAGER')
print('=' * 60)
cleanup()

@test('FM1: Request + release')
def _():
    ok, amt, tid = fm.request('test', 'SBIN', 9)
    assert ok
    fm.release(tid, pnl=50)
    assert fm.available == 100_000
    print(f'    Allocated {amt}, released')

@test('FM2: Max concurrent')
def _():
    cleanup()
    fm.request('equity_notif', 'A')
    fm.request('equity_notif', 'B')
    ok, _, _ = fm.request('equity_notif', 'C')
    assert not ok
    print(f'    3rd blocked')
    cleanup()

mon.stop()

# ══════════════════════════════════════
print('\n' + '=' * 60)
print(f'TOTAL: {passed}/{passed + failed} passed, {failed} failed')
print('=' * 60)
sys.exit(0 if failed == 0 else 1)
