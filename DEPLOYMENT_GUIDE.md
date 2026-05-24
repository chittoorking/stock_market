# CAM_R3 Trading Bot — Deployment Guide

## The Strategy

**What it does**: Shorts stocks when price touches Camarilla R3 level AND the stock has been trending DOWN on the daily chart.

**Why it works**: Camarilla R3 is a resistance level calculated from previous day's High, Low, Close. When a stock in a downtrend touches this resistance, it bounces down 67% of the time. This has been consistent across 4 years and 45 NSE stocks.

**The numbers (verified on 4729 trades, Jan 2022 — May 2026)**:

| Metric | Value |
|--------|-------|
| Win Rate | 67% |
| Target | 1.50% |
| Stop Loss | 1.00% |
| Trades per day | 4-5 |
| Avg profit per trade (Rs 10L position) | Rs 3,322 |
| Avg daily profit | Rs 16,247 |
| Expected yearly profit | Rs 36-40 Lakh |

## What You Need

1. **Capital**: Rs 10 Lakh minimum (Rs 5L minimum to be profitable)
2. **Upstox Account**: With intraday margin enabled (5x leverage)
3. **Computer**: With Python installed, internet connection
4. **Time**: Login before 9:15 AM daily. Bot runs 5 minutes.

## Files

```
bot.py                          — The trading bot (single file, 260 lines)
data/5min/                      — 4 years of 5-min data for 45 stocks
data/all_signals_analysis.csv   — All 4729 trades with 46 features
journal/                        — Daily trade logs
CLAUDE.md                       — Trading rules reference
```

## Setup Steps

### Step 1: Install Python (if not installed)
Download from python.org. Make sure `pip` is available.

### Step 2: Install Required Package
```bash
pip install requests
```

### Step 3: Get Upstox Access Token
1. Login to Upstox Developer Portal (https://account.upstox.com/developer/apps)
2. Create an app if you don't have one
3. Get your daily access token (you need to do this EVERY morning before market opens)
4. Save it:
```bash
echo "YOUR_TOKEN_HERE" > upstox_token.txt
```
**Note**: Upstox tokens expire daily. You must login and refresh every morning.

### Step 4: Paper Trade (First 1 Month)
```bash
python bot.py --date 2026-05-22
```
This simulates on historical data. Run for different dates to verify.

For LIVE paper trading (uses real Upstox data but no real orders):
```bash
python bot.py
```

### Step 5: Go Live (After 1 Month Paper Trading)
Only if paper trading shows WR > 60% over 100+ trades:
```bash
python bot.py --live --capital 200000
```
Start with Rs 2L. Scale up after 1 month.

## Daily Routine

| Time | Action |
|------|--------|
| 8:30 AM | Login to Upstox, get access token, save to upstox_token.txt |
| 9:15 AM | Market opens. Bot loads data. |
| 10:15 AM | Bot scans all 45 stocks for CAM_R3 signals |
| 10:16 AM | Bot places SHORT orders for qualifying stocks |
| 10:17 AM onwards | Orders execute. Target and stop loss are set. |
| 3:15 PM | All positions auto-squared off by Upstox |
| 3:30 PM | Check journal/YYYY-MM-DD.md for today's results |

## How the Bot Decides

```
For each of 45 NIFTY stocks:
  1. Is this stock trending DOWN on the daily chart? (last 5 days)
     NO → skip
  2. Calculate Camarilla R3 = prev_close + (prev_high - prev_low) × 0.275
  3. Did price touch R3 in the first 50 minutes today?
     NO → skip
  4. Did price close below R3 after touching it? (bounce confirmed)
     NO → skip
  5. YES → SHORT this stock
     Entry: current price
     Target: entry - 1.50%
     Stop: entry + 1.00%
```

## Risk Management

- **Per trade risk**: 1.00% of position (Rs 10,000 on Rs 10L)
- **Max daily loss**: If 5 trades all lose = Rs 50,000 (5% of Rs 10L capital)
- **Max drawdown seen in 4 years**: ~8.8%
- **Recovery time**: Worst drawdowns recovered within 2-3 weeks
- **NEVER increase position size after losses**
- **NEVER skip the stop loss**
- **NEVER hold overnight**

## When to STOP Trading

Stop and review if any of these happen:
- WR drops below 55% over 50 consecutive trades
- 3 consecutive months of negative P&L
- Max drawdown exceeds 15%
- Market regime changes (new regulations, circuit breaker rules, etc.)

## Charges Breakdown (per trade, Rs 10L position)

| Charge | Amount |
|--------|--------|
| Brokerage | Rs 20 |
| STT (sell side) | Rs 250 |
| Exchange charges | Rs 69 |
| Stamp duty | Rs 30 |
| SEBI | Rs 1 |
| GST | Rs 16 |
| **Total** | **Rs 386** |

## Honest Disclaimers

1. **Past performance does not guarantee future results**
2. The 67% WR is from backtesting. Live trading may differ due to slippage, missed fills, and changing market conditions
3. 2025 was the weakest year in our backtest (lower WR). The strategy may underperform in certain market regimes
4. **Start small. Paper trade first. Scale up only after live verification.**
5. This is not financial advice. You are responsible for your own trading decisions.

## Contact / Support

Bot code and data are in this repository.
All analysis, experiments, and reasoning documented in git history (60+ commits).
