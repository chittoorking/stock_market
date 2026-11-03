"""Main — single entry point.

    python3 -m trading.main
    python3 -m trading.main --agents news_orb multyfi_options
    python3 -m trading.main --port 8905 --pool 100000
"""
import argparse
import importlib
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# Ensure both trading_v2 AND news-trading root are importable
_root = Path(__file__).parent.parent  # trading_v2/
_project = _root.parent              # news-trading/
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_project))

from dotenv import load_dotenv
load_dotenv(_project / 'live' / '.env')

from trading.broker import Broker
from trading.positions import PositionManager
from trading.fund_manager import FundManager
from trading.monitor import Monitor
from trading.server import TradingServer
from trading.logger import get_logger

log = get_logger('main')

AGENTS = {
    'news_orb':        'trading.agents.news_orb:NewsOrbAgent',
    'multyfi_options': 'trading.agents.multyfi_options:MulOptions',
    'ipo':             'trading.agents.ipo:IpoAgent',
    'pullback':        'trading.agents.pullback:PullbackAgent',
    'market_lens':     'trading.agents.market_lens:MarketLensAgent',
    'anomaly':         'trading.agents.anomaly:AnomalyAgent',
}

# Agents with custom run() — started as background threads
AUTONOMOUS = {'news_orb', 'multyfi_options', 'ipo',
              'pullback', 'market_lens', 'anomaly'}

TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'


def _tg(msg):
    try:
        import requests
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT, 'text': msg}, timeout=5)
    except Exception:
        pass


# Track agent health for /health endpoint
_agent_status = {}  # name -> {'alive': bool, 'crashes': int, 'last_crash': str}


def _load(path: str):
    mod, cls = path.rsplit(':', 1)
    return getattr(importlib.import_module(mod), cls)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8905)
    ap.add_argument('--pool', type=float, default=100_000)
    ap.add_argument('--agents', nargs='+', default=list(AGENTS.keys()))
    ap.add_argument('--poll', type=int, default=10)
    args = ap.parse_args()

    log.info('=' * 50)
    log.info(f'TRADING SYSTEM v2')
    log.info(f'  Pool: Rs {args.pool:,.0f} | Port: {args.port}')
    log.info(f'  Agents: {args.agents}')
    log.info('=' * 50)

    # Core
    broker = Broker()
    positions = PositionManager(broker)
    fm = FundManager(pool=args.pool)
    monitor = Monitor(broker, positions, fm, poll_interval=args.poll)
    monitor.start()

    # Agents
    loaded = []
    for name in args.agents:
        if name not in AGENTS:
            log.warning(f'Unknown agent: {name}')
            continue
        try:
            cls = _load(AGENTS[name])
            agent = cls(broker, positions, fm, monitor)
            loaded.append(agent)
        except Exception as e:
            log.error(f'Failed to load {name}: {e}')
            traceback.print_exc()

    # Start autonomous agents with crash recovery
    for agent in loaded:
        if agent.name in AUTONOMOUS:
            threading.Thread(target=_run_with_recovery, args=(agent,),
                             daemon=True).start()
            log.info(f'Started autonomous: {agent.name}')

    # HTTP server
    server = TradingServer(port=args.port)
    server.set_core(fm, positions, monitor)
    for a in loaded:
        server.register(a)

    log.info(f'Ready. {len(loaded)} agents.')
    server.start()


def _run_with_recovery(agent):
    """Run agent with INFINITE crash recovery. Never gives up during market hours.
    Backoff: 10s → 30s → 60s → 120s, capped at 120s.
    Resets backoff after 5 minutes of stable running."""
    crashes = 0
    _agent_status[agent.name] = {'alive': True, 'crashes': 0, 'last_crash': ''}

    while True:
        started = time.time()
        try:
            _agent_status[agent.name]['alive'] = True
            agent.run()
            # Normal exit (e.g., IPO runs once and finishes)
            log.info(f'{agent.name} finished normally')
            return
        except Exception as e:
            crashes += 1
            now = datetime.now()
            _agent_status[agent.name] = {
                'alive': False, 'crashes': crashes,
                'last_crash': now.strftime('%H:%M:%S')
            }
            log.error(f'{agent.name} CRASHED #{crashes}: {e}')
            traceback.print_exc()

            # Alert on Telegram
            _tg(f'⚠️ {agent.name} crashed #{crashes} at {now.strftime("%H:%M")}: {str(e)[:100]}')

            # Don't retry after market hours (3:20 PM)
            if now.hour > 15 or (now.hour == 15 and now.minute >= 20):
                log.info(f'{agent.name} crashed after market hours — not retrying')
                return

            # Backoff: 10s, 30s, 60s, 120s max
            wait = min(120, 10 * (3 ** min(crashes - 1, 3)))
            log.info(f'{agent.name} restarting in {wait}s...')
            time.sleep(wait)

            # Reset crash counter if agent ran stable for 5+ minutes
            if time.time() - started > 300:
                crashes = 0


if __name__ == '__main__':
    main()
