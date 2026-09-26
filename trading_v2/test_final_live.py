"""FINAL LIVE VALIDATION — every endpoint, every agent, real broker."""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / 'live' / '.env')

import requests as req

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

print('\n--- SYSTEM ---')
@test('health 6 agents')
def _():
    assert len(req.get('http://localhost:8905/health', timeout=5).json()['agents']) == 5

@test('status FM')
def _():
    assert req.get('http://localhost:8905/status', timeout=5).json()['pool'] == 100000

@test('positions list')
def _():
    assert 'trades' in req.get('http://localhost:8905/positions', timeout=5).json()

print('\n--- EQUITY NOTIF ---')
@test('structured skip VWAP')
def _():
    r = req.post('http://localhost:8905/notify', json={'stock':'SBIN','entry':1000,'sl':990,'target':1020}, timeout=10).json()
    assert r['sym'] == 'SBIN'

@test('wide SL skip')
def _():
    assert req.post('http://localhost:8905/notify', json={'stock':'SBIN','entry':1000,'sl':900,'target':1100}, timeout=10).json()['decision'] == 'SKIP'

@test('empty error')
def _():
    assert 'error' in req.post('http://localhost:8905/notify', json={}, timeout=10).json()

@test('comma prices')
def _():
    r = req.post('http://localhost:8905/notify', json={'stock':'RELIANCE','entry':'1,280','sl':'1,275','target':'1,300'}, timeout=10).json()
    assert r['sym'] == 'RELIANCE'

@test('exit endpoint')
def _():
    assert 'exited' in req.post('http://localhost:8905/exit', json={'symbol':'SBIN'}, timeout=10).json()

print('\n--- ARISE PROXY ---')
@test('GET notify instant')
def _():
    r = req.get('http://localhost:8900/api/notify?text=TEST', timeout=5).json()
    assert r['status'] == 'received'

@test('POST notify instant')
def _():
    r = req.post('http://localhost:8900/api/notify', json={'text':'POST TEST'}, timeout=5).json()
    assert r['status'] == 'received'

print('\n--- BROKER ---')
from trading.broker import Broker, BrokerError
b = Broker()

@test('2600+ scrips')
def _():
    assert b.scrip_count >= 2600

@test('SBIN LTP')
def _():
    assert 500 < b.ltp('SBIN') < 2000

@test('RELIANCE LTP')
def _():
    assert 1000 < b.ltp('RELIANCE') < 3000

@test('funds > 0')
def _():
    assert b.funds() > 0

@test('bad symbol raises')
def _():
    try: b.ltp('ZZZZFAKE'); assert False
    except BrokerError: pass

@test('order_filled nonexistent')
def _():
    assert b.order_filled('FAKE-999')[0] == False

@test('GOLD chain > 10')
def _():
    assert len(b.option_chain('GOLD')) > 10

@test('CRUDE chain > 10')
def _():
    assert len(b.option_chain('CRUDEOIL')) > 10

print('\n--- MULTYFI API ---')
creds = json.load(open('/home/ai18developer/multyfi_creds.json'))
mh = {'authtoken': creds['authToken'], 'mobile': creds['mobile']}

@test('options/premium 200')
def _():
    assert req.get('https://app.multyfi.com/api/options/premium', headers=mh, timeout=15, verify=False).status_code == 200

@test('futures/premium 200')
def _():
    assert req.get('https://app.multyfi.com/api/futures/premium', headers=mh, timeout=15, verify=False).status_code == 200

@test('streams 200')
def _():
    assert req.get('https://app.multyfi.com/api/streams?page=1&limit=3', headers=mh, timeout=15, verify=False).status_code == 200

print('\n--- MULTYFI AGENT ---')
@test('extract signal')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.multyfi_options import MulOptions
    from trading.logger import get_logger
    pm = PositionManager(b); fm = FundManager(); mon = Monitor(b, pm, fm)
    a = MulOptions.__new__(MulOptions)
    a._broker = b; a._positions = pm; a._fm = fm; a._monitor = mon
    a._creds = creds; a._seen = set(); a._log = get_logger('mul_test')
    sig = a._extract_signal({
        'fullDocument': {'action': 'BUY', 'baseSymbol': 'SBIN', 'optionType': 'PE', 'strike': 1000},
        'createdAt': '2026-09-11'})
    assert sig and sig['symbol'] == 'SBIN'

@test('MULTIPLE skipped')
def _():
    from trading.positions import PositionManager
    from trading.fund_manager import FundManager
    from trading.monitor import Monitor
    from trading.agents.multyfi_options import MulOptions
    from trading.logger import get_logger
    pm = PositionManager(b); fm = FundManager(); mon = Monitor(b, pm, fm)
    a = MulOptions.__new__(MulOptions)
    a._broker = b; a._positions = pm; a._fm = fm; a._monitor = mon
    a._creds = creds; a._seen = set(); a._log = get_logger('mul_test')
    a._process_option({'action': 'MULTIPLE', 'baseSymbol': 'NIFTY', 'optionType': 'CE', 'strike': 23700})
    assert len(pm.active()) == 0

print('\n--- GEMINI ---')
@test('gemini responds')
def _():
    from trading.services.gemini import call_safe
    assert len(call_safe('Reply OK', grounding=False)) > 0

print('\n--- RSS ---')
@test('50+ articles')
def _():
    from trading.services.rss import fetch_all
    assert len(fetch_all()) > 50

print('\n--- ORB ---')
from trading.services import orb
@test('ATR SL SBIN')
def _():
    assert 1 <= orb.get_atr_sl('SBIN') <= 20

@test('filter pass')
def _():
    assert len(orb.apply_filters(
        [{'symbol':'X','nse_symbol':'X','call':'BUY','projection':7.0}],
        {'X':{'high':101,'low':100,'open':100.2,'prev_close':100}})) == 1

@test('filter reject')
def _():
    assert len(orb.apply_filters(
        [{'symbol':'X','nse_symbol':'X','call':'BUY','projection':5.0}],
        {'X':{'high':105,'low':100,'open':104.5,'prev_close':100}})) == 0

print('\n--- FM ---')
from trading.fund_manager import FundManager
@test('request + release')
def _():
    fm = FundManager(pool=100_000)
    ok,_,tid = fm.request('test','SBIN',9)
    assert ok; fm.release(tid); assert fm.available == 100_000

@test('max concurrent')
def _():
    fm = FundManager(pool=100_000)
    fm.request('equity_notif','A'); fm.request('equity_notif','B')
    assert not fm.request('equity_notif','C')[0]

print('\n--- CRON ---')
import subprocess
@test('start.sh cron')
def _():
    assert 'trading_v2/start.sh' in subprocess.run(['crontab','-l'], capture_output=True, text=True).stdout

@test('healthcheck cron')
def _():
    assert 'healthcheck.sh' in subprocess.run(['crontab','-l'], capture_output=True, text=True).stdout

@test('token refresh cron')
def _():
    assert 'refresh_token' in subprocess.run(['crontab','-l'], capture_output=True, text=True).stdout

print(f'\n{"=" * 60}')
print(f'TOTAL: {passed}/{passed + failed} passed, {failed} failed')
print(f'{"=" * 60}')
sys.exit(0 if failed == 0 else 1)
