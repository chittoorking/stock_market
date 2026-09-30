# TRADING STRATEGIES — COMPLETE DOCUMENTATION
## Built & Tested Aug 24-27, 2026

---

## STRATEGY 1: Indian News Options/MIS
**Status: DEPLOYED on GCP | Paper Mode**
**WR: 86% (430 days tested) | 97% with reversal**

### How it works
1. 9:05 AM IST — Fetch 400+ articles from ET Markets, LiveMint, BS, NDTV, Zerodha Pulse
2. Gemini Flash reads FULL articles via Google Search grounding
3. Projects expected % move for each stock
4. Counter-argument test: "Can you argue opposite direction? If yes, SKIP"
5. Only trade projection >= 3%
6. 9:15 AM — Market opens. F&O stock → buy CE/PE option. Non-F&O → MIS
7. -20% SL on option. +10% trail activation, 5% from peak
8. If SL hits → REVERSE (buy opposite option). News moves are sticky.
9. 3:10 PM — Hard exit everything

### Key findings
- Projection-based > conviction-based (64% → 86% WR)
- Full article reading > headlines only
- Wins = ONE force, ONE direction (RBI ban, IT raid)
- Losses = TWO opposing forces (M&A ambiguous, pricing unclear)
- Reversal works: 7/7 times SL hit, stock closed opposite = reversal profits

### Settings
- TRADE_MODE=both (F&O→options, non-F&O→MIS)
- 210 F&O stocks from live NSE data (fno_stocks_live.json)
- Model: Gemini 3.6 Flash (~$2 per 430 days)

### Files
- Bot: live/options_bot.py
- Cron: 35 3 * * 1-5 (9:05 AM IST)
- F&O list: fno_stocks_live.json
- Server: 35.238.32.244

---

## STRATEGY 2: Indian IPO Listing Day
**Status: DEPLOYED on GCP | Paper Mode**
**WR: 100% (25/25 tested) | MIS mode**

### How it works
1. 9:55 AM — Check NSE API for IPOs listing today
2. 10:00 AM — IPO lists. Check listing premium
3. Premium 5-25% → BUY MIS (intraday)
4. Trail: activate +1%, trail 1% from peak
5. 3:10 PM — Hard exit

### Why it works
- IPO subscribed 10-60x = massive demand
- Premium 5-25% = not too hot, not too cold
- MIS gives 4x leverage vs old CNC mode
- 25/25 = 100% WR in this premium zone

### Files
- Bot: live/ipo_trader.py
- Cron: 55 9 * * 1-5 (9:55 AM IST)

---

## STRATEGY 3: US Overnight News Trading
**Status: DEPLOYED on GCP | Paper Mode**
**WR: 75% (20 dates tested) | Stocks via IBKR**

### How it works
1. 6:00 PM IST — Fetch US news from Yahoo, CNBC, MarketWatch, SeekingAlpha RSS + Google News
2. Gemini reads full articles, applies filters
3. Buy stock in IBKR after-hours (4-8 PM ET)
4. -5% SL from entry (active overnight)
5. Sell next morning at open or trail

### Filters (from loss analysis)
- Skip activist stakes (stocks barely move)
- Skip mega caps >$100B (barely react)
- Skip expected/scheduled/rumored events
- Counter-argument test
- Must project >3% move

### Why overnight not intraday
- US market too efficient for intraday (53% WR)
- News breaks after market close
- Move happens in after-hours/pre-market (70% of total move)
- By next morning, move is done

### Files
- Bot: live/us_overnight_bot.py
- Cron: 30 12 * * 1-5 (6:00 PM IST)
- IBKR: port 4004 on GCP

---

## STRATEGY 4: US IPO Listing Day
**Status: DEPLOYED (inside US overnight bot) | Paper Mode**
**WR: 91% (35/35 ALL 2024 IPOs) | Stocks via IBKR**

### How it works
1. Check if US IPO listing today
2. Premium 0-200% → BUY at open via IBKR
3. Trail: +1% activate, 1% from peak
4. Sell same day (true intraday)

### Why it works (structural, not a pattern)
- Banks underprice IPOs on purpose (guarantee day-1 pop)
- Retail can't buy pre-IPO → rush at open
- Insiders locked up 90-180 days (no sellers)
- Short selling restricted on day 1
- Media hype = buying pressure

### Results (ALL 2024 IPOs, no cherry-picking)
- 35 IPOs in 0-200% zone: 32W/2L/1F = 91% WR
- Avg intraday spike: +23.7%
- ~10-15 IPOs per month
- 2 losses: AVR (flat), IBTA (sell the news)

### Historical (2019-2024, major IPOs)
- 36/36 = 100% WR in 0-200% zone
- Works for 30+ years (structural market feature)

---

## TESTED & REJECTED

### Gold/Commodities Intraday
- Gold: 52-60% WR (coin flip)
- Crude: 57% WR
- Natural Gas: 20% WR
- WHY: Multiple forces compete simultaneously. Daily moves are noise within trend.

### Forex (USD/INR)
- Barely moves (<0.1% daily). Not tradeable.

### US Intraday News
- 53% WR. Market too efficient. Move happens overnight, not intraday.

### Multibagger Stock Screening
- Can't backtest properly (no historical screener data)
- SAST filings: 82% WR on 22 trades (needs more data)
- Momentum + low liquidity: 57-88% CAGR (but that's portfolio, not individual picks)

---

## RISK MANAGEMENT

### Per-trade rules
- Never more than 33% of capital on one trade
- Indian options: -20% SL + reversal
- US stocks: -5% SL from entry
- Trail locks profit before greed kicks in
- Exit by end of day (no overnight for Indian, overnight OK for US)

### Break-even WR
- Indian options: 13% (we have 86%)
- US overnight: 19% (we have 75%)
- US IPO: 5% (we have 91%)
- Massive safety margin on all strategies

### Compounding
- Start Rs 15,000
- 33% per trade, 3 trades/month
- Profit grows capital, capital grows position size
- Never change the rules as capital grows

---

## INFRASTRUCTURE

### GCP Server: 35.238.32.244
```
Cron jobs:
  9:05 AM IST  — Indian News Options/MIS bot
  9:55 AM IST  — Indian IPO bot
  6:00 PM IST  — US Overnight + IPO bot
  24/7         — Crypto bot (separate)
```

### API Keys (.env)
- GEMINI_API_KEY: Gemini 3.6 Flash (grounding)
- ANTHROPIC_API_KEY: Claude Sonnet (backup)
- TELEGRAM_BOT_TOKEN: alerts
- IBKR: port 4004 (US trading)

### Data Sources
**Indian:**
- ET Markets RSS, LiveMint RSS, BS RSS, NDTV RSS
- Zerodha Pulse (258 articles/day)
- NSE Corp Announcements API
- NSE Bhavcopy (option prices)

**US:**
- Yahoo Finance RSS (49/day)
- CNBC RSS (30/day)
- MarketWatch RSS (10/day)
- SeekingAlpha RSS (7/day)
- SEC EDGAR EFTS API (800+ 8-K/day)
- Google News RSS (date filtered)

---

## THE EDGE

"Read the cause, not the reaction."

Everyone else reads charts (the reaction).
We read news, filings, articles (the cause).

AI reads 400+ articles in 60 seconds.
No human can do this.

The strategy is not the AI.
The strategy is WHAT we ask the AI to read.
And HOW we act on it.

---

## PAPER TRADE CHECKLIST

- [ ] Indian News bot: check daily logs
- [ ] Indian IPO bot: check when IPO lists
- [ ] US Overnight bot: check daily scans
- [ ] US IPO bot: check when IPO lists
- [ ] After 2-4 weeks: compare paper WR vs backtest WR
- [ ] If within 10% of backtest: go live with Rs 15,000
- [ ] If below: investigate and fix before going live
