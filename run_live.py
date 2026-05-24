"""Entry point — run the live trading bot."""
import argparse
from live.trader import LiveTrader

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='CAM BOT v3 — Live Trading')
    p.add_argument('--live', action='store_true', help='Live mode (real orders)')
    p.add_argument('--capital', type=int, default=100000, help='Capital in Rs (default 1L)')
    args = p.parse_args()

    import os
    if args.capital:
        os.environ['CAPITAL'] = str(args.capital)

    trader = LiveTrader(paper_mode=not args.live)
    trader.run()
