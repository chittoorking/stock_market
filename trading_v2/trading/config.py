"""Trading system configuration — all constants in one place.

Import this instead of hardcoding magic numbers.
"""

# ── Scoring thresholds ──────────────────────────────────────────────
PE_MIN_SCORE = 20          # 88.5% WR on 130 trades
CE_MIN_SCORE = 19          # 84% WR
FUT_MIN_SCORE = 5          # 89% WR on 166 trades

# ── Fund manager ────────────────────────────────────────────────────
MAX_CONCURRENT = 10
MAX_PER_STRATEGY = 5
DAILY_LOSS_PCT = 10.0
EQUITY_RISK_PCT = 2.0

# ── Options ─────────────────────────────────────────────────────────
OPTIONS_SL_PCT = 20.0      # -20% SL on premium
OPTIONS_TRAIL_ACTIVATE = 0  # disabled — Multyfi exit optimal
OPTIONS_TRAIL_PCT = 0

# ── Futures ─────────────────────────────────────────────────────────
FUTURES_MIN_SL_PCT = 0.3   # skip if SL < 0.3%

# ── ORB ─────────────────────────────────────────────────────────────
ORB_MINUTES = 5            # 5-min opening range
ORB_BUFFER_PCT = 0.1       # 0.1% above OR high for entry
ORB_MAX_ENTRY_MOVE = 2.0   # skip if already moved 2% from prev close
ORB_MIN_REMAINING = 1.0    # need 1% remaining move

# ── MCX commodities ─────────────────────────────────────────────────
MCX_COMMODITIES = ('GOLD', 'CRUDE', 'SILVER', 'COPPER', 'NICKEL',
                   'NATURALGAS', 'ZINC', 'LEAD', 'ALUMIN', 'COTTON', 'MENTHA')

# ── Telegram ────────────────────────────────────────────────────────
TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT_ID = '866752968'

# ── Multyfi ─────────────────────────────────────────────────────────
MULTYFI_BASE = 'https://app.multyfi.com/api'
MULTYFI_POLL_INTERVAL = 5

# ── Broker ──────────────────────────────────────────────────────────
INDMONEY_BASE = 'https://api.indstocks.com'
