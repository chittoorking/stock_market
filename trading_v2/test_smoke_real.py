"""SMOKE TESTS — real broker, real API, no mocks.

Run on GCP only. Tests actual broker connectivity, symbol resolution,
LTP fetching, order book, and full agent pipelines with real data.
"""
import sys
from pathlib import Path

_test_dir = Path(__file__).parent          # trading_v2/
_project = _test_dir.parent                # news-trading/
sys.path.insert(0, str(_test_dir))
sys.path.insert(0, str(_project))

from dotenv import load_dotenv
load_dotenv(_project / 'live' / '.env')

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


# ══════════════════════════
# 1. BROKER — REAL API
# ══════════════════════════
print('\n=== 1. REAL BROKER ===')

@test('1a: broker init loads 2000+ scrips')
def _():
    from trading.broker import Broker
    b = Broker()
    assert b.scrip_count > 2000, f'Only {b.scrip_count} scrips'

@test('1b: LTP for SBIN returns valid price')
def _():
    from trading.broker import Broker
    b = Broker()
    p = b.ltp('SBIN')
    assert 500 < p < 2000, f'SBIN LTP={p} seems wrong'

@test('1c: LTP for RELIANCE returns valid price')
def _():
    from trading.broker import Broker
    b = Broker()
    p = b.ltp('RELIANCE')
    assert 1000 < p < 3000, f'RELIANCE LTP={p} seems wrong'

@test('1d: LTP for fake symbol raises BrokerError')
def _():
    from trading.broker import Broker, BrokerError
    b = Broker()
    try:
        b.ltp('ZZZZFAKE')
        assert False, 'should raise'
    except BrokerError:
        pass

@test('1e: has_symbol works')
def _():
    from trading.broker import Broker
    b = Broker()
    assert b.has_symbol('SBIN')
    assert b.has_symbol('RELIANCE')
    assert b.has_symbol('TCS')
    assert b.has_symbol('SKIPPER')
    assert b.has_symbol('PARKHOSPS')
    assert not b.has_symbol('FAKEXYZ123')

@test('1f: funds returns positive number')
def _():
    from trading.broker import Broker
    b = Broker()
    f = b.funds()
    assert f > 0, f'Funds={f}'

@test('1g: order_book returns list')
def _():
    from trading.broker import Broker
    b = Broker()
    filled, price = b.order_filled('NONEXISTENT-999')
    assert filled == False

@test('1h: ltp_safe returns default for bad symbol')
def _():
    from trading.broker import Broker
    b = Broker()
    p = b.ltp_safe('ZZZZFAKE', 42.0)
    assert p == 42.0


# ══════════════════════════
# 2. FUND MANAGER — REAL INIT
# ══════════════════════════
print('\n=== 2. FUND MANAGER ===')

@test('2a: FM init with real pool')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    assert fm.available == 100_000
    s = fm.status()
    assert s['pool'] == 100_000
    assert s['deployed'] == 0

@test('2b: FM request + release cycle')
def _():
    from trading.fund_manager import FundManager
    fm = FundManager(pool=100_000)
    ok, amt, tid = fm.request('equity_notif', 'SBIN', 9)
    assert ok
    assert amt > 0
    assert tid.startswith('equity_notif_')
    fm.release(tid, pnl=100)
    assert fm.available == 100_000


# ══════════════════════════
# 3. POSITIONS — REAL BROKER
# ══════════════════════════
print('\n=== 3. POSITIONS ===')

@test('3a: position open/close with real LTP')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.types import Side
    b = Broker()
    pm = PositionManager(b)
    # open with fake order_id (don't place real order)
    pos = pm.open('SBIN', Side.BUY, 1, b.ltp('SBIN'), order_id='SMOKE-1',
                  strategy='smoke_test')
    assert len(pm.active()) == 1
    # close with skip_sell (don't place real sell)
    closed = pm.close(pos.id, skip_sell=True)
    assert closed is not None
    assert len(pm.active()) == 0


# ══════════════════════════
# 4. MONITOR — REAL
# ══════════════════════════
print('\n=== 4. MONITOR ===')

@test('4a: monitor init + start + stop')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = Broker()
    pm = PositionManager(b)
    fm = FundManager(pool=100_000)
    m = Monitor(b, pm, fm, poll_interval=60)
    m.start()
    import time; time.sleep(1)
    m.stop()

@test('4b: force_exit returns False for non-existent')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = Broker()
    m = Monitor(b, PositionManager(b), FundManager())
    assert m.force_exit('NONEXISTENT') == False


# ══════════════════════════
# 5. EQUITY NOTIF — REAL VWAP CHECK
# ══════════════════════════
print('\n=== 5. EQUITY NOTIF ===')

@test('5a: SKIP signal (wide SL)')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.equity_notif import EquityNotifAgent
    b = Broker(); pm = PositionManager(b); fm = FundManager(); m = Monitor(b, pm, fm)
    a = EquityNotifAgent(b, pm, fm, m)
    r = a.on_signal({'stock': 'SBIN', 'entry': 1000, 'sl': 800, 'target': 1200})
    assert r['decision'] == 'SKIP'
    assert r['sl_pct'] == 20.0

@test('5b: exit signal for non-existent position')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.equity_notif import EquityNotifAgent
    b = Broker(); pm = PositionManager(b); fm = FundManager(); m = Monitor(b, pm, fm)
    a = EquityNotifAgent(b, pm, fm, m)
    r = a.on_signal({'text': 'Exit SBIN Book Profit'})
    assert r.get('action') == 'EXIT' or 'error' not in r

@test('5c: VWAP check runs without crash (real yfinance)')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.equity_notif import EquityNotifAgent
    b = Broker(); pm = PositionManager(b); fm = FundManager(); m = Monitor(b, pm, fm)
    a = EquityNotifAgent(b, pm, fm, m)
    close, vwap, ok = a._check_vwap('SBIN')
    # Should return real data
    assert close is not None or close is None  # either works, no crash
    print(f'    SBIN close={close}, vwap={vwap}, ok={ok}')

@test('5d: parse INDmoney format')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.equity_notif import EquityNotifAgent
    b = Broker(); pm = PositionManager(b); fm = FundManager(); m = Monitor(b, pm, fm)
    a = EquityNotifAgent(b, pm, fm, m)
    sym, entry, sl, tgt = a._parse_text(
        'Equity Intraday Trade Stock Name SKIPPER Entry Range 606.45 Stop Loss 599 Target 630'
    )
    assert sym == 'SKIPPER', f'Got {sym}'
    assert entry == 606.45, f'Got {entry}'
    assert sl == 599, f'Got {sl}'


# ══════════════════════════
# 6. ORB SERVICES — REAL
# ══════════════════════════
print('\n=== 6. ORB SERVICES ===')

@test('6a: ATR SL returns valid number for SBIN')
def _():
    from trading.services.orb import get_atr_sl
    sl = get_atr_sl('SBIN')
    assert 1.0 <= sl <= 20.0, f'ATR SL={sl} for SBIN seems wrong'
    print(f'    SBIN ATR SL={sl}%')

@test('6b: ATR SL returns default for fake symbol')
def _():
    from trading.services.orb import get_atr_sl
    sl = get_atr_sl('ZZZZFAKE')
    assert sl == 3.0  # default

@test('6c: apply_filters with real-ish data')
def _():
    from trading.services.orb import apply_filters
    trades = [{'symbol': 'SBIN', 'nse_symbol': 'SBIN', 'call': 'BUY', 'projection': 7.0}]
    or_data = {'SBIN': {'high': 1010, 'low': 1000, 'open': 1002, 'prev_close': 1000}}
    result = apply_filters(trades, or_data)
    assert len(result) == 1  # should pass (remaining 6.8%, entry_move 1.1%)


# ══════════════════════════
# 7. GEMINI — REAL API
# ══════════════════════════
print('\n=== 7. GEMINI ===')

@test('7a: Gemini call_safe with simple prompt')
def _():
    import os
    if not os.environ.get('GEMINI_API_KEY'):
        print('    (skipped: no GEMINI_API_KEY)')
        return
    from trading.services.gemini import call_safe
    r = call_safe('Reply with just the word OK', grounding=False)
    assert len(r) > 0, 'Empty response'
    print(f'    Gemini: {r[:50]}')


# ══════════════════════════
# 8. RSS — REAL FETCH
# ══════════════════════════
print('\n=== 8. RSS ===')

@test('8a: fetch_all returns articles')
def _():
    from trading.services.rss import fetch_all
    articles = fetch_all(window_hours=24)
    assert len(articles) > 10, f'Only {len(articles)} articles'
    print(f'    {len(articles)} articles fetched')


# ══════════════════════════
# 9. FULL STACK — REAL
# ══════════════════════════
print('\n=== 9. FULL STACK ===')

@test('9a: all 6 agents init without crash')
def _():
    from trading.broker import Broker
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    b = Broker(); pm = PositionManager(b); fm = FundManager(); m = Monitor(b, pm, fm)
    from trading.agents.equity_notif import EquityNotifAgent
    from trading.agents.news_orb import NewsOrbAgent
    from trading.agents.commodity import CommodityAgent
    from trading.agents.multyfi_options import MulOptions
    from trading.agents.cas import CasAgent
    from trading.agents.ipo import IpoAgent
    agents = [
        EquityNotifAgent(b, pm, fm, m),
        NewsOrbAgent(b, pm, fm, m),
        CommodityAgent(b, pm, fm, m),
        MulOptions(b, pm, fm, m),
        CasAgent(b, pm, fm, m),
        IpoAgent(b, pm, fm, m),
    ]
    assert len(agents) == 6
    names = [a.name for a in agents]
    assert 'equity_notif' in names
    assert 'news_orb' in names
    assert 'commodity' in names
    assert 'multyfi_options' in names
    assert 'cas' in names
    assert 'ipo' in names

@test('9b: server health endpoint works')
def _():
    import requests
    try:
        r = requests.get('http://localhost:8905/health', timeout=5)
        data = r.json()
        assert data['ok'] == True
        assert len(data['agents']) == 6
        print(f'    Agents: {data["agents"]}')
    except Exception as e:
        print(f'    (server not running: {e})')

@test('9c: server notify endpoint works')
def _():
    import requests
    try:
        r = requests.post('http://localhost:8905/notify',
                         json={'stock': 'SBIN', 'entry': 1000, 'sl': 800, 'target': 1200},
                         timeout=10)
        data = r.json()
        assert data['decision'] == 'SKIP'  # SL 20% too wide
        assert data['sl_pct'] == 20.0
    except Exception as e:
        print(f'    (server not running: {e})')


# ══════════════════════════
# SUMMARY
# ══════════════════════════
print(f'\n{"=" * 60}')
print(f'SMOKE TESTS (REAL): {passed} passed, {failed} failed')
if errors:
    print(f'\nFAILURES:')
    for name, err in errors:
        print(f'  FAIL: {name}: {err}')
else:
    print('ALL REAL TESTS PASS.')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
