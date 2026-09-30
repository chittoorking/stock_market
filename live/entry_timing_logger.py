"""
Entry Timing Logger — runs alongside news bot.
Every time a trade is executed, logs price at 5-min intervals for 60 min.
Tracks: open price, 5/10/15/20/25/30/45/60 min prices, day low, day high.

Deployed alongside indian_news_bot.py on GCP.
Reads the bot's trade log to detect new trades, then monitors prices.

Output: logs/entry_timing.json
"""
import json
import os
import sys
import time
import threading
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

import requests

IST = timezone(timedelta(hours=5, minutes=30))
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
TIMING_FILE = LOG_DIR / 'entry_timing.json'

log = logging.getLogger('entry_timing')
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')

# INDmoney LTP
try:
    from live import indmoney_client as api
except ImportError:
    api = None


def get_ltp(symbol):
    """Get current price."""
    if api:
        try:
            data = api.get_ltp([symbol])
            return data.get(symbol, 0)
        except Exception:
            pass
    return 0


def load_timing_data():
    if TIMING_FILE.exists():
        return json.loads(TIMING_FILE.read_text())
    return []


def save_timing_data(data):
    TIMING_FILE.write_text(json.dumps(data, indent=2, default=str))


def track_entry(symbol, entry_price, entry_time_str, trade_type, catalyst):
    """
    Track price movement after entry for 60 minutes.
    Logs price at 5-min intervals + computes pullback stats.
    """
    log.info(f"TRACKING: {symbol} entry={entry_price} type={trade_type}")

    record = {
        'symbol': symbol,
        'entry_price': entry_price,
        'entry_time': entry_time_str,
        'trade_type': trade_type,
        'catalyst': catalyst,
        'prices': {},
        'day_high': entry_price,
        'day_low': entry_price,
        'max_pullback_pct': 0,
        'max_pullback_time': None,
        'best_entry_price': entry_price,
        'best_entry_time': None,
        'close_price': None,
    }

    # Track for 120 minutes (2 hours) at 1-min intervals
    check_intervals = list(range(1, 121))  # 1 to 120 minutes
    snapshot_times = [1, 2, 3, 5, 10, 15, 20, 25, 30, 45, 60, 90, 120]

    start_time = datetime.now()
    best_entry = entry_price
    best_entry_min = 0
    max_pullback = 0
    max_pullback_min = 0

    for minute in check_intervals:
        time.sleep(60)  # wait 1 minute

        now = datetime.now()
        # Stop after 3:10 PM
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            break

        price = get_ltp(symbol)
        if price <= 0:
            continue

        # Update tracking
        if price > record['day_high']:
            record['day_high'] = price
        if price < record['day_low']:
            record['day_low'] = price

        # Pullback from entry (for BUY trades)
        pullback_pct = (price - entry_price) / entry_price * 100
        if pullback_pct < max_pullback:
            max_pullback = pullback_pct
            max_pullback_min = minute
            record['max_pullback_pct'] = round(max_pullback, 2)
            record['max_pullback_time'] = f'+{minute}min'

        # Best entry (lowest price for BUY)
        if price < best_entry:
            best_entry = price
            best_entry_min = minute
            record['best_entry_price'] = round(best_entry, 2)
            record['best_entry_time'] = f'+{minute}min'

        # Log at snapshot intervals
        if minute in snapshot_times:
            pnl_from_open = (price - entry_price) / entry_price * 100
            pnl_from_best = (price - best_entry) / best_entry * 100 if best_entry > 0 else 0
            record['prices'][f'+{minute}min'] = {
                'price': round(price, 2),
                'pnl_from_entry': round(pnl_from_open, 2),
                'pnl_from_best': round(pnl_from_best, 2),
            }
            log.info(f"  {symbol} +{minute}min: {price:.2f} ({pnl_from_open:+.2f}% from entry, best entry was {best_entry:.2f} at +{best_entry_min}min)")

    # Get close price
    close_price = get_ltp(symbol)
    if close_price > 0:
        record['close_price'] = round(close_price, 2)
        record['pnl_at_open_entry'] = round((close_price - entry_price) / entry_price * 100, 2)
        record['pnl_at_best_entry'] = round((close_price - best_entry) / best_entry * 100, 2) if best_entry > 0 else 0
        record['improvement'] = round(record['pnl_at_best_entry'] - record['pnl_at_open_entry'], 2)

    # Summary
    log.info(f"SUMMARY: {symbol}")
    log.info(f"  Entry: {entry_price} | Best entry: {best_entry} at +{best_entry_min}min")
    log.info(f"  Max pullback: {max_pullback:+.2f}% at +{max_pullback_min}min")
    log.info(f"  Close: {record.get('close_price', '?')}")
    log.info(f"  P&L at open entry: {record.get('pnl_at_open_entry', '?')}%")
    log.info(f"  P&L at best entry: {record.get('pnl_at_best_entry', '?')}%")
    log.info(f"  Improvement if waited: {record.get('improvement', '?')}%")

    # Save
    data = load_timing_data()
    data.append(record)
    save_timing_data(data)

    return record


def watch_trade_log():
    """
    Watch the news bot log for new trade entries.
    When detected, start tracking in a background thread.
    """
    log_file = LOG_DIR / 'options_cron.log'
    seen_trades = set()

    log.info("Entry Timing Logger started. Watching for new trades...")

    while True:
        now = datetime.now()
        # Only run during market hours
        if now.hour < 9 or now.hour > 15:
            time.sleep(60)
            continue

        try:
            if log_file.exists():
                lines = log_file.read_text(errors='replace').split('\n')
                for line in lines[-100:]:  # check last 100 lines
                    # Look for trade execution lines
                    if 'BUY MIS' in line or 'MIS BUY' in line or 'BUY' in line and 'entry' in line.lower():
                        # Extract symbol and price
                        # Format: "BUY MIS: 5x SBIN @ 850.0"
                        import re
                        match = re.search(r'(?:BUY|SELL)\s+(?:MIS:?\s+)?(\d+)x\s+(\w+)\s+@\s+([\d.]+)', line)
                        if match:
                            qty = int(match.group(1))
                            symbol = match.group(2)
                            price = float(match.group(3))
                            trade_key = f"{now.strftime('%Y-%m-%d')}_{symbol}_{price}"

                            if trade_key not in seen_trades:
                                seen_trades.add(trade_key)
                                log.info(f"NEW TRADE DETECTED: {symbol} @ {price}")
                                # Start tracking in background
                                threading.Thread(
                                    target=track_entry,
                                    args=(symbol, price, now.strftime('%H:%M:%S'), 'MIS', 'news'),
                                    daemon=True,
                                ).start()
        except Exception as e:
            log.error(f"Watch error: {e}")

        time.sleep(30)  # check every 30 seconds


def print_report():
    """Print summary of all tracked entries."""
    data = load_timing_data()
    if not data:
        print("No timing data yet.")
        return

    print(f"\n{'='*70}")
    print(f"ENTRY TIMING REPORT ({len(data)} trades)")
    print(f"{'='*70}")

    total_improvement = 0
    pullback_count = 0

    for r in data:
        imp = r.get('improvement', 0)
        total_improvement += imp
        if r.get('max_pullback_pct', 0) < -0.3:
            pullback_count += 1

        print(f"\n  {r['symbol']} ({r['entry_time']})")
        print(f"    Entry: {r['entry_price']} | Best: {r.get('best_entry_price','?')} at {r.get('best_entry_time','?')}")
        print(f"    Pullback: {r.get('max_pullback_pct','?')}% at {r.get('max_pullback_time','?')}")
        print(f"    Close P&L: open={r.get('pnl_at_open_entry','?')}% vs best={r.get('pnl_at_best_entry','?')}%")
        print(f"    Improvement if waited: {imp:+.2f}%")

    avg_imp = total_improvement / len(data) if data else 0
    print(f"\n{'='*70}")
    print(f"AVERAGE IMPROVEMENT: {avg_imp:+.2f}% per trade")
    print(f"TRADES WITH >0.3% PULLBACK: {pullback_count}/{len(data)} ({pullback_count/len(data)*100:.0f}%)")
    print(f"{'='*70}")

    if avg_imp > 0.3:
        print(f"\nVERDICT: WAITING IS BETTER by {avg_imp:+.2f}% per trade")
        print(f"At 5x leverage: Rs {avg_imp * 5000:.0f} extra per trade")
    elif avg_imp > 0:
        print(f"\nVERDICT: Slight improvement, not significant yet. Need more data.")
    else:
        print(f"\nVERDICT: OPEN ENTRY IS BETTER. Don't wait.")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--report':
        print_report()
    else:
        watch_trade_log()
