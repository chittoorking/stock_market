"""
Multi-account LLM pool manager — round-robin with cooldowns.
Multi-account pool with RPM/TPM tracking and auto-cooldown.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Deque, Tuple

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class AccountState(str, Enum):
    AVAILABLE = "AVAILABLE"
    COOLING_DOWN = "COOLING_DOWN"
    DISABLED = "DISABLED"


@dataclass
class ModelUsageTracker:
    """Sliding-window usage tracker per model per account."""
    rpm_limit: int = 500
    tpm_limit: int = 150000
    window_seconds: int = 60
    _requests: Deque[float] = field(default_factory=deque)
    _tokens: Deque[Tuple[float, int]] = field(default_factory=deque)

    def _prune(self):
        cutoff = time.time() - self.window_seconds
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()

    def record(self, token_count: int = 0):
        now = time.time()
        self._requests.append(now)
        if token_count:
            self._tokens.append((now, token_count))

    def can_handle_request(self) -> Tuple[bool, Dict[str, Any]]:
        self._prune()
        current_rpm = len(self._requests)
        current_tpm = sum(t for _, t in self._tokens)
        usage = {
            "rpm": current_rpm,
            "rpm_limit": self.rpm_limit,
            "rpm_pct": current_rpm / self.rpm_limit if self.rpm_limit else 0,
            "tpm": current_tpm,
            "tpm_limit": self.tpm_limit,
            "tpm_pct": current_tpm / self.tpm_limit if self.tpm_limit else 0,
        }
        # Cooldown at 80% capacity
        can_handle = (current_rpm < self.rpm_limit * 0.8) and (current_tpm < self.tpm_limit * 0.8)
        return can_handle, usage


@dataclass
class LLMAccount:
    """Single LLM account with its own client and usage tracking."""
    account_id: int
    api_key: str
    client: AsyncOpenAI = field(init=False)
    state: AccountState = AccountState.AVAILABLE
    tracker: ModelUsageTracker = field(default_factory=ModelUsageTracker)
    cooldown_until: float = 0.0
    total_requests: int = 0
    total_errors: int = 0

    def __post_init__(self):
        self.client = AsyncOpenAI(api_key=self.api_key)


class LLMManager:
    """
    Manages a pool of LLM accounts with round-robin load balancing.
    Multi-account LLM pool with round-robin load balancing.
    """

    def __init__(self):
        self.accounts: List[LLMAccount] = []
        self._current_index = 0
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Load accounts from config and validate."""
        account_configs = settings.get_llm_accounts()
        for cfg in account_configs:
            account = LLMAccount(
                account_id=cfg["account_id"],
                api_key=cfg["api_key"],
            )
            account.tracker = ModelUsageTracker(
                rpm_limit=cfg["gpt4o_rpm_limit"],
                tpm_limit=cfg["gpt4o_tpm_limit"],
            )
            self.accounts.append(account)
            logger.info("LLM account %d loaded", cfg["account_id"])

        if not self.accounts:
            logger.warning("No LLM accounts configured — adding default from ACCOUNT_1")
            if settings.ACCOUNT_1_API_KEY:
                self.accounts.append(LLMAccount(
                    account_id=1,
                    api_key=settings.ACCOUNT_1_API_KEY,
                ))

        logger.info("LLM Manager initialized with %d accounts", len(self.accounts))

    async def get_client(self) -> Tuple[AsyncOpenAI, int]:
        """Get the next available client using round-robin with cooldown awareness."""
        async with self._lock:
            now = time.time()
            attempts = len(self.accounts)

            for _ in range(attempts):
                account = self.accounts[self._current_index]
                self._current_index = (self._current_index + 1) % len(self.accounts)

                # Check cooldown
                if account.state == AccountState.COOLING_DOWN:
                    if now >= account.cooldown_until:
                        account.state = AccountState.AVAILABLE
                    else:
                        continue

                if account.state == AccountState.DISABLED:
                    continue

                # Check capacity
                can_handle, usage = account.tracker.can_handle_request()
                if can_handle:
                    return account.client, account.account_id

                # Enter cooldown
                account.state = AccountState.COOLING_DOWN
                account.cooldown_until = now + 30  # 30s cooldown
                logger.warning(
                    "Account %d entering cooldown (RPM: %d/%d, TPM: %d/%d)",
                    account.account_id,
                    usage["rpm"], usage["rpm_limit"],
                    usage["tpm"], usage["tpm_limit"],
                )

            # All accounts exhausted — use least loaded
            logger.error("All LLM accounts at capacity, using least loaded")
            least_loaded = min(self.accounts, key=lambda a: a.tracker._requests.__len__())
            return least_loaded.client, least_loaded.account_id

    def record_usage(self, account_id: int, token_count: int = 0):
        """Record usage after a successful request."""
        for account in self.accounts:
            if account.account_id == account_id:
                account.tracker.record(token_count)
                account.total_requests += 1
                break

    def record_error(self, account_id: int):
        """Record an error for an account."""
        for account in self.accounts:
            if account.account_id == account_id:
                account.total_errors += 1
                # Disable account after 10 consecutive errors
                if account.total_errors > 10:
                    account.state = AccountState.DISABLED
                    logger.error("Account %d disabled after too many errors", account_id)
                break

    def get_status(self) -> List[Dict[str, Any]]:
        """Get status of all accounts for monitoring."""
        statuses = []
        for account in self.accounts:
            can_handle, usage = account.tracker.can_handle_request()
            statuses.append({
                "account_id": account.account_id,
                "state": account.state.value,
                "can_handle": can_handle,
                "rpm": usage["rpm"],
                "tpm": usage["tpm"],
                "total_requests": account.total_requests,
                "total_errors": account.total_errors,
            })
        return statuses


# Singleton
llm_manager = LLMManager()
