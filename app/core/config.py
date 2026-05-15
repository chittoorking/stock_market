"""
Pydantic-based configuration for the trading bot.
All config from .env, structured validation, multi-account LLM pool.
"""
from __future__ import annotations

import os
from typing import Optional, List, Dict
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # ─── Application ───
    APP_NAME: str = "Trading AI Agent"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ─── Database ───
    DATABASE_URL: str = "postgresql+asyncpg://localhost:5432/trading_bot"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ─── LLM Multi-Account Pool ───
    ACCOUNT_1_API_KEY: Optional[str] = None
    ACCOUNT_1_GPT4O_RPM_LIMIT: int = 500
    ACCOUNT_1_GPT4O_TPM_LIMIT: int = 150000
    ACCOUNT_2_API_KEY: Optional[str] = None
    ACCOUNT_2_GPT4O_RPM_LIMIT: int = 500
    ACCOUNT_2_GPT4O_TPM_LIMIT: int = 150000
    ACCOUNT_3_API_KEY: Optional[str] = None
    ACCOUNT_3_GPT4O_RPM_LIMIT: int = 500
    ACCOUNT_3_GPT4O_TPM_LIMIT: int = 150000
    ACCOUNT_4_API_KEY: Optional[str] = None
    ACCOUNT_4_GPT4O_RPM_LIMIT: int = 500
    ACCOUNT_4_GPT4O_TPM_LIMIT: int = 150000
    ACCOUNT_5_API_KEY: Optional[str] = None
    ACCOUNT_5_GPT4O_RPM_LIMIT: int = 500
    ACCOUNT_5_GPT4O_TPM_LIMIT: int = 150000

    # ─── Broker: Upstox (Primary for NSE) ───
    UPSTOX_API_KEY: Optional[str] = None
    UPSTOX_API_SECRET: Optional[str] = None
    UPSTOX_ACCESS_TOKEN: Optional[str] = None
    UPSTOX_REDIRECT_URI: str = "https://localhost"

    # ─── Broker: Zerodha Kite ───
    KITE_API_KEY: Optional[str] = None
    KITE_API_SECRET: Optional[str] = None
    KITE_ACCESS_TOKEN: Optional[str] = None

    # ─── Broker: Alpaca (US Markets) ───
    ALPACA_API_KEY: Optional[str] = None
    ALPACA_API_SECRET: Optional[str] = None
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    # ─── Broker: CCXT (Crypto) ───
    CCXT_EXCHANGE: str = "binance"
    CCXT_API_KEY: Optional[str] = None
    CCXT_API_SECRET: Optional[str] = None

    # ─── Risk Limits ───
    MAX_POSITION_SIZE: float = 100000.0
    MAX_PORTFOLIO_EXPOSURE: float = 500000.0
    MAX_LEVERAGE: float = 2.0
    DEFAULT_STOP_LOSS_PCT: float = 3.0
    DEFAULT_TAKE_PROFIT_PCT: float = 10.0
    MAX_DAILY_LOSS: float = 25000.0
    MAX_OPEN_POSITIONS: int = 10

    # ─── Market Data ───
    MARKET_DATA_PROVIDER: str = "yfinance"
    MARKET_DATA_WS_URL: Optional[str] = None

    # ─── Notifications ───
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    SLACK_WEBHOOK_URL: Optional[str] = None
    EMAIL_SMTP_HOST: str = "smtp.gmail.com"
    EMAIL_SMTP_PORT: int = 587
    EMAIL_USERNAME: Optional[str] = None
    EMAIL_PASSWORD: Optional[str] = None
    EMAIL_FROM: str = "trading-bot@yourdomain.com"

    # ─── Session ───
    SESSION_TTL_SECONDS: int = 3600
    SIGNAL_TTL_SECONDS: int = 900

    # ─── Auth ───
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    AUTH_REQUIRED: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    def get_llm_accounts(self) -> List[Dict]:
        """Collect all configured LLM accounts for the pool manager."""
        accounts = []
        for i in range(1, 6):
            api_key = getattr(self, f"ACCOUNT_{i}_API_KEY", None)
            if api_key:
                accounts.append({
                    "account_id": i,
                    "api_key": api_key,
                    "gpt4o_rpm_limit": getattr(self, f"ACCOUNT_{i}_GPT4O_RPM_LIMIT", 500),
                    "gpt4o_tpm_limit": getattr(self, f"ACCOUNT_{i}_GPT4O_TPM_LIMIT", 150000),
                })
        return accounts

    def get_active_broker(self) -> str:
        """Determine which broker is configured."""
        if self.UPSTOX_API_KEY:
            return "upstox"
        if self.KITE_API_KEY:
            return "kite"
        if self.ALPACA_API_KEY:
            return "alpaca"
        if self.CCXT_API_KEY:
            return "ccxt"
        return "paper"


settings = Settings()
