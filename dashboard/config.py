"""Dashboard configuration — bot definitions, paths, settings."""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(os.getenv("DASHBOARD_BASE", str(Path.home())))
DATA_DIR = BASE_DIR / "news-trading" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Auth
ADMIN_USER = os.getenv("DASH_USER", "admin")
ADMIN_PASS = os.getenv("DASH_PASS", "money2026")

# Database
DB_PATH = DATA_DIR / "dashboard.db"

# IBKR
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "4004"))
IBKR_DASH_CLIENT_ID = 99  # Dashboard's own IBKR connection

# Bot definitions
BOTS = {
    "crypto": {
        "name": "Crypto Bot",
        "market": "Crypto (SOL/ETH/AAVE)",
        "script": "v3_crypto_runner.py",
        "pgrep": "v3_crypto_runner",
        "cwd": str(BASE_DIR / "crypto-bot"),
        "python": "python3",
        "log_file": str(BASE_DIR / "crypto-bot" / "bot_ibkr.log"),
        "state_file": str(DATA_DIR / "crypto_state.json"),
        "pause_file": str(DATA_DIR / "crypto_paused"),
        "config_file": str(DATA_DIR / "crypto_config.json"),
        "env": {
            "SIM_MODE": "false",
            "CAPITAL_PER_COIN": "2000",
            "IBKR_HOST": "127.0.0.1",
            "IBKR_PORT": "4004",
            "IBKR_CLIENT_ID": "2",
        },
        "schedule": "24/7, 1H bars",
        "broker": "ibkr",
    },
    "us_stocks": {
        "name": "US Stocks Bot",
        "market": "US (96 stocks)",
        "script": "v3_us_runner.py",
        "pgrep": "v3_us_runner",
        "cwd": str(BASE_DIR / "crypto-bot"),
        "python": "python3",
        "log_file": str(BASE_DIR / "crypto-bot" / "us_bot.log"),
        "state_file": str(DATA_DIR / "us_stocks_state.json"),
        "pause_file": str(DATA_DIR / "us_stocks_paused"),
        "config_file": str(DATA_DIR / "us_stocks_config.json"),
        "env": {
            "IBKR_HOST": "127.0.0.1",
            "IBKR_PORT": "4004",
            "IBKR_CLIENT_ID": "3",
            "PAPER_MODE": "true",
            "RESULTS_FILE": str(BASE_DIR / "crypto-bot" / "us500_2023_results.json"),
        },
        "schedule": "Mon-Fri 9:30AM-3:55PM ET",
        "broker": "ibkr",
    },
    "gapgo": {
        "name": "Gap & Go Bot",
        "market": "US (39 stocks)",
        "script": "v3_gapgo_runner.py",
        "pgrep": "v3_gapgo_runner",
        "cwd": str(BASE_DIR / "crypto-bot"),
        "python": "python3",
        "log_file": str(BASE_DIR / "crypto-bot" / "gapgo_bot.log"),
        "state_file": str(DATA_DIR / "gapgo_state.json"),
        "pause_file": str(DATA_DIR / "gapgo_paused"),
        "config_file": str(DATA_DIR / "gapgo_config.json"),
        "env": {
            "IBKR_HOST": "127.0.0.1",
            "IBKR_PORT": "4004",
            "IBKR_CLIENT_ID": "4",
            "PAPER_MODE": "true",
        },
        "schedule": "Mon-Fri 10:00AM-3:55PM ET (gap days)",
        "broker": "ibkr",
    },
    "indian_news": {
        "name": "News Bot (Gemini)",
        "market": "NSE News MIS + F&O | Proj >=3%",
        "script": "live/indian_news_bot.py",
        "pgrep": "indian_news_bot",
        "cwd": str(BASE_DIR / "news-trading"),
        "python": str(BASE_DIR / "news-trading" / "venv" / "bin" / "python"),
        "log_file": str(BASE_DIR / "news-trading" / "live" / "logs" / "options_cron.log"),
        "state_file": str(DATA_DIR / "indian_news_state.json"),
        "pause_file": str(DATA_DIR / "indian_news_paused"),
        "config_file": str(DATA_DIR / "indian_news_config.json"),
        "env": {},
        "schedule": "Mon-Fri 9:05AM scan, 9:15-3:10 trade",
        "broker": "indmoney",
        "token_file": str(DATA_DIR / "indmoney_token.txt"),
    },
    "indian_continuous": {
        "name": "Intraday Scanner",
        "market": "NSE Breaking News | 9:30-2:30",
        "script": "live/indian_news_bot.py --continuous",
        "pgrep": "indian_news_bot.*continuous",
        "cwd": str(BASE_DIR / "news-trading"),
        "python": str(BASE_DIR / "news-trading" / "venv" / "bin" / "python"),
        "log_file": str(BASE_DIR / "news-trading" / "live" / "logs" / "continuous_cron.log"),
        "state_file": str(DATA_DIR / "indian_continuous_state.json"),
        "pause_file": str(DATA_DIR / "indian_continuous_paused"),
        "config_file": str(DATA_DIR / "indian_continuous_config.json"),
        "env": {},
        "schedule": "Mon-Fri 9:30AM-2:30PM every 5min",
        "broker": "indmoney",
        "token_file": str(DATA_DIR / "indmoney_token.txt"),
    },
    "ipo": {
        "name": "IPO Trader",
        "market": "NSE IPO Listings | 5-25% premium",
        "script": "live/ipo_trader.py",
        "pgrep": "ipo_trader",
        "cwd": str(BASE_DIR / "news-trading"),
        "python": str(BASE_DIR / "news-trading" / "venv" / "bin" / "python"),
        "log_file": str(BASE_DIR / "news-trading" / "live" / "logs" / "ipo_cron.log"),
        "state_file": str(DATA_DIR / "ipo_state.json"),
        "pause_file": str(DATA_DIR / "ipo_paused"),
        "config_file": str(DATA_DIR / "ipo_config.json"),
        "env": {},
        "schedule": "Mon-Fri 9:55AM",
        "broker": "indmoney",
        "token_file": str(DATA_DIR / "indmoney_token.txt"),
    },
}

# Alerts
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_SMTP = os.getenv("EMAIL_SMTP", "")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

# Default risk config
DEFAULT_RISK = {
    "crypto": {"max_positions": 3, "capital_per_trade": 2000, "max_daily_loss": 500},
    "us_stocks": {"max_positions": 5, "capital_per_trade": 100000, "max_daily_loss": 10000},
    "gapgo": {"max_positions": 3, "capital_per_trade": 50000, "max_daily_loss": 5000},
    "indian_news": {"max_positions": 5, "capital_per_trade": 12000, "max_daily_loss": 5000},
    "indian_continuous": {"max_positions": 3, "capital_per_trade": 12000, "max_daily_loss": 3000},
    "ipo": {"max_positions": 1, "capital_per_trade": 30000, "max_daily_loss": 10000},
}
