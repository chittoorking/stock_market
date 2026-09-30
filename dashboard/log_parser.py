"""Background log parser — tails bot logs, extracts trades into SQLite."""
import re
import asyncio
import logging
from pathlib import Path
from . import config, db

log = logging.getLogger("dashboard")

# Track file positions to avoid re-reading
_file_positions = {}

# Regex patterns for trade extraction
CRYPTO_ENTRY = re.compile(
    r'ENTRY (\w+) (LONG|SHORT) price=([\d.]+) atr=([\d.]+) qty=([\d.]+)'
)
CRYPTO_CLOSE = re.compile(
    r'CLOSE (\w+) \[(\w+)\] side=(\w+) pnl.([-\d.]+)%'
)
US_ENTRY = re.compile(
    r'ENTRY (\w+) (L|S|BUY|SELL).+?price=([\d.]+)'
)
US_EXIT = re.compile(
    r'EXIT (\w+).+?pnl=\$?([-\d.]+).+?reason=(\w+)', re.IGNORECASE
)
# Generic patterns
ENTRY_PATTERN = re.compile(
    r'(?:ENTRY|OPEN|BUY|SELL)\s+(\w+)\s+(LONG|SHORT|L|S|BUY|SELL)', re.IGNORECASE
)
EXIT_PATTERN = re.compile(
    r'(?:CLOSE|EXIT)\s+(\w+)\s+.*?pnl', re.IGNORECASE
)


def parse_log_line(bot_id, line):
    """Parse a single log line for trade events. Returns trade dict or None."""
    line = line.strip()
    if not line:
        return None

    # Crypto bot patterns
    if bot_id == "crypto":
        m = CRYPTO_ENTRY.search(line)
        if m:
            return {
                "type": "entry",
                "symbol": m.group(1),
                "side": m.group(2).lower(),
                "price": float(m.group(3)),
                "qty": float(m.group(5)),
                "atr": float(m.group(4)),
            }
        m = CRYPTO_CLOSE.search(line)
        if m:
            return {
                "type": "exit",
                "symbol": m.group(1),
                "reason": m.group(2),
                "side": m.group(3),
                "pnl_pct": float(m.group(4)),
            }

    # US stocks patterns
    if bot_id == "us_stocks":
        m = US_ENTRY.search(line)
        if m:
            return {
                "type": "entry",
                "symbol": m.group(1),
                "side": "long" if m.group(2) in ("L", "BUY") else "short",
                "price": float(m.group(3)),
            }
        m = US_EXIT.search(line)
        if m:
            return {
                "type": "exit",
                "symbol": m.group(1),
                "pnl": float(m.group(2)),
                "reason": m.group(3),
            }

    return None


def tail_log(bot_id):
    """Read new lines from a bot's log file."""
    bot = config.BOTS.get(bot_id)
    if not bot:
        return []

    log_path = Path(bot["log_file"])
    if not log_path.exists():
        return []

    pos = _file_positions.get(bot_id, 0)
    file_size = log_path.stat().st_size

    # Handle log rotation (file got smaller)
    if file_size < pos:
        pos = 0

    if file_size == pos:
        return []

    new_lines = []
    with open(log_path, "r", errors="replace") as f:
        f.seek(pos)
        new_lines = f.readlines()
        _file_positions[bot_id] = f.tell()

    return new_lines


def get_recent_logs(bot_id, lines=50):
    """Get last N lines from a bot's log."""
    bot = config.BOTS.get(bot_id)
    if not bot:
        return ""
    log_path = Path(bot["log_file"])
    if not log_path.exists():
        return "No log file found"
    try:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception as e:
        return f"Error reading log: {e}"


async def background_parser():
    """Background task: continuously parse logs for trades."""
    # Track pending entries (entry seen, waiting for exit)
    pending = {}  # bot_id:symbol -> entry_data

    while True:
        for bot_id in config.BOTS:
            try:
                new_lines = tail_log(bot_id)
                for line in new_lines:
                    event = parse_log_line(bot_id, line)
                    if not event:
                        continue

                    key = f"{bot_id}:{event['symbol']}"

                    if event["type"] == "entry":
                        # Extract timestamp from log line
                        ts_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        entry_time = ts_match.group(1) if ts_match else None
                        pending[key] = {
                            "bot": bot_id,
                            "symbol": event["symbol"],
                            "side": event.get("side", ""),
                            "entry_price": event.get("price", 0),
                            "qty": event.get("qty", 0),
                            "entry_time": entry_time,
                        }

                    elif event["type"] == "exit":
                        ts_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        exit_time = ts_match.group(1) if ts_match else None

                        entry = pending.pop(key, {})
                        db.insert_trade(
                            bot=bot_id,
                            symbol=event["symbol"],
                            side=entry.get("side", event.get("side", "")),
                            qty=entry.get("qty", 0),
                            entry_price=entry.get("entry_price", 0),
                            exit_price=event.get("price", 0),
                            pnl=event.get("pnl", 0),
                            pnl_pct=event.get("pnl_pct", 0),
                            entry_time=entry.get("entry_time"),
                            exit_time=exit_time,
                            reason=event.get("reason", "unknown"),
                        )
            except Exception as e:
                log.error(f"Log parser error for {bot_id}: {e}")

        await asyncio.sleep(5)
