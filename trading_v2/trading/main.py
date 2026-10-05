"""Main — single entry point.

    python3 -m trading.main
    python3 -m trading.main --agents equity_notif news_orb
    python3 -m trading.main --port 8905 --pool 100000
"""
import argparse
import importlib
import sys
import threading
import time
import traceback
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
    'anomaly':         'trading.agents.anomaly:AnomalyAgent',
    # PAPER MODE — logging only, no trades
    'news_orb':        'trading.agents.news_orb:NewsOrbAgent',
    'multyfi_options': 'trading.agents.multyfi_options:MulOptions',
    'ipo':             'trading.agents.ipo:IpoAgent',
}

# Agents with custom run() — started as background threads
AUTONOMOUS = {'anomaly', 'news_orb', 'multyfi_options', 'ipo'}


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


def _run_with_recovery(agent, max_retries=3):
    """Run agent with crash recovery. Retries up to max_retries."""
    for attempt in range(max_retries):
        try:
            agent.run()
            return  # normal exit (e.g., CAS/IPO run once and finish)
        except Exception as e:
            log.error(f'{agent.name} crashed (attempt {attempt + 1}/{max_retries}): {e}')
            traceback.print_exc()
            if attempt < max_retries - 1:
                time.sleep(30)  # wait before retry
    log.error(f'{agent.name} failed after {max_retries} attempts — giving up')



# ── PID Lock — prevent duplicates ───────────────────────────────────
import fcntl, os
_LOCK_FILE = os.path.expanduser('~/trading_main.lock')
_lock_fp = open(_LOCK_FILE, 'w')
try:
    fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fp.write(str(os.getpid()))
    _lock_fp.flush()
except BlockingIOError:
    print('Trading main already running. Exiting.')
    exit(0)

if __name__ == '__main__':
    main()
