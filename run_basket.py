#!/usr/bin/env python3
"""Run the Gap Fill Basket Trader V3.

Usage:
  python run_basket.py                    # Paper mode, auto-detect capital
  python run_basket.py --live             # LIVE mode, auto-detect capital from broker
  python run_basket.py --live --capital 50000   # LIVE with specific capital
"""
import sys
sys.path.insert(0, '.')
from live.basket_trader import main

if __name__ == '__main__':
    main()
