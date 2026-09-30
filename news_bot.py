"""
Live News Trading Bot — Runs at 8:30 AM IST, finds conv 16+ trades, executes at 9:15 AM.

Flow:
  8:30 AM — Fetch news from Google News RSS (previous evening + pre-market)
  8:31 AM — Claude scans headlines, picks tradeable ones
  8:32 AM — Scrape full article content for selected headlines
  8:33 AM — Claude reads full articles, applies 5 questions, outputs trades with conviction
  8:45 AM — Trades ready. Conv 16+ only. Send Telegram alert.
  9:15 AM — Market opens. Execute MIS orders via INDmoney.
  3:10 PM — Bot squares off all positions (before broker's 3:20 penalty).

Usage:
  python news_bot.py              # Pre-market scan (8:30 AM cron)
  python news_bot.py --continuous # Intraday scanner (9:30 AM - 2:30 PM, every 15 min)
  python news_bot.py --scan-only  # Just scan, don't execute trades
  python news_bot.py --backtest 2024-08-09  # Backtest a specific date
"""
import json, os, sys, time, re, logging
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# Setup logging
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'news_bot_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('news_bot')

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

from news_fetcher import fetch_news, fetch_live_rss, fetch_pulse, filter_stock_news, scrape_articles_by_index
from pydantic import BaseModel, Field, field_validator
from enum import Enum
import requests

from live import indmoney_client as api
from live.price_feed import PriceFeed

CLAUDE_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
MIN_CONVICTION = 16
STOP_LOSS_PCT = 3.0  # Exit if moves 3% against us from entry
MAX_PCT_PER_TRADE = 0.333  # Never more than 33% on one trade


# ── Pydantic Schema ──────────────────────────────────────────

class MISCall(str, Enum):
    BUY_MIS = "BUY_MIS"
    SELL_MIS = "SELL_MIS"
    SKIP = "SKIP"

class TradeRec(BaseModel):
    symbol: str
    call: MISCall
    conviction: int = Field(ge=1, le=20)
    story: str
    news_summary: str
    news_timestamp: str
    previous_close: float
    q1_surprising: str
    q2_binding: str
    q3_material: str
    q4_one_time: str
    q5_continuation: str
    expected_move: str
    risk: str
    entry_timing: str

class ScanOutput(BaseModel):
    stocks_found: list[dict]
    trades: list[dict]
    validation: list[dict] = []


# ── Claude API ──────────────────────────────────────────

ANALYST_PROMPT = """You are an intraday news trader on Indian stock markets (NSE/BSE).
Your job: find stocks that will move TODAY based on breaking news, and decide BUY_MIS or SELL_MIS.

Date: {date}

STEP 1 — NEWS IS PRE-FETCHED FOR YOU:
The news articles below have been fetched from Google News RSS with strict date filtering.
All articles are from {prev_date} evening to {date} morning ONLY. No future leakage.
DO NOT search for more news. Use ONLY the articles provided below.

IMPORTANT FILTERING RULES:
1. SKIP any article about quarterly results / earnings / profit growth or decline — that's handled by a separate filing system.
2. For stocks you DO pick, check if the stock already moved big (>3%) on the PREVIOUS trading day — if yes, the news is priced in.

React to: block deals, order wins, contracts, regulatory actions, analyst calls, management changes, M&A, sector policy, partnerships, IPO listings, stake changes, index inclusion.

Use web search to find the stock's previous closing price AND previous day's move.

BEFORE making any call, READ BETWEEN THE LINES. Ask yourself these 5 questions for each trade:

Q1: IS THIS ACTUALLY SURPRISING? Or does this company get this type of news routinely?
Q2: IS THIS BINDING REVENUE or just speculation?
Q3: IS THE IMPACT MATERIAL relative to the company's size?
Q4: IS THIS A ONE-TIME SHOCK that will bounce back?
Q5: WILL THIS MOVE CONTINUE AFTER THE GAP or is the gap the entire move?

If any of Q1-Q4 flags a problem, LOWER conviction to below 13 or SKIP.

STEP 2 — FILTER:
Only include stocks where:
- The news is FRESH (same day or previous evening)
- The news is MATERIAL (will move the stock >1%)
- The stock has enough liquidity for MIS trading (not penny stocks)

STEP 3 — OUTPUT:
Respond with ONLY a valid JSON object. No text before or after. Just raw JSON.

IMPORTANT OUTPUT RULES:
- Pick only the highest conviction trades. Quality over quantity.
- Stories max 3 sentences. Be terse.
- stocks_found max 5 entries.
- validation: empty list [].
- Output MUST be parseable JSON. No trailing commas.

CALL types (what each means for execution):
- BUY_MIS: expect 1-day pop, exit same day
- SELL_MIS: expect 1-day drop, intraday short
- SKIP: you genuinely cannot tell which way it will go

CONVICTION scale (1-20):
- 1-4: noise, barely a signal
- 5-8: slight lean
- 9-12: moderate confidence
- 13-16: strong conviction
- 17-20: slam dunk

Schema:
{schema}
"""


def call_claude(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            resp = requests.post(CLAUDE_URL,
                headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 4000,
                    "temperature": 0.2,
                    "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=180)
            if resp.status_code == 200:
                texts = []
                for block in resp.json().get('content', []):
                    if block.get('type') == 'text' and block.get('text', '').strip():
                        texts.append(block['text'])
                return '\n'.join(texts)
            elif resp.status_code == 429:
                wait = (attempt + 1) * 15
                log.warning(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"API error {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            log.error(f"Request error: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
    return None


def parse_response(raw_text):
    depth = 0
    start = None
    for i, ch in enumerate(raw_text):
        if ch == '{':
            if depth == 0: start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                json_str = raw_text[start:i+1]
                try:
                    data = json.loads(json_str)
                    result = ScanOutput(**data)
                    return result, None
                except json.JSONDecodeError:
                    fixed = re.sub(r',\s*}', '}', json_str)
                    fixed = re.sub(r',\s*]', ']', fixed)
                    try:
                        data = json.loads(fixed)
                        result = ScanOutput(**data)
                        return result, None
                    except Exception as e:
                        return None, f"JSON fix failed: {e}"
                except Exception as e:
                    return None, f"Pydantic error: {e}"
    return None, "No complete JSON found"


# ── Telegram ──────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"[TELEGRAM] {msg}")
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10)
    except Exception as e:
        log.error(f"Telegram error: {e}")


# ── INDmoney Orders ──────────────────────────────────────────

def place_indmoney_order(symbol, side, qty):
    """Place MIS order via INDmoney."""
    try:
        result = api.place_order(symbol, qty, side, price=0, order_type='MARKET', product='INTRADAY')
        log.info(f"INDmoney order: {side} {qty} x {symbol} -> {result}")
        return result
    except Exception as e:
        log.error(f"INDmoney error: {e}")
        return {'status': 'error', 'message': str(e)}


def get_available_capital():
    """Fetch available capital from INDmoney account."""
    try:
        funds = api.get_funds()
        log.info(f"INDmoney available funds: Rs {funds:,.0f}")
        return funds
    except Exception as e:
        log.warning(f"Could not fetch funds: {e}. Using default Rs 1L")
        return 100000


def calculate_qty_and_capital(trades, total_capital):
    """Allocate capital: max 33% per trade, min 10%, priority by conviction.

    Rules:
    1. No single trade gets more than 33% of total capital — EVER
    2. Every trade gets minimum 10%
    3. Within 10-33% range, higher conviction gets more
    4. Unused capital stays idle

    Weights: conv 18+ = 5, conv 17 = 3, conv 16 = 2
    """
    trades = sorted(trades, key=lambda t: t.get('conviction', 0), reverse=True)

    if not trades:
        return []

    # Dynamic min per trade: 5% of capital or Rs 5K, whichever is higher
    min_per_trade = max(5000, total_capital * 0.05)
    max_possible = max(1, int(total_capital / min_per_trade))
    trades = trades[:max_possible]
    log.info(f"  Capital: Rs {total_capital:,.0f} | Min/trade: Rs {min_per_trade:,.0f} | Max trades: {max_possible} | Got: {len(trades)}")

    # Assign weights by conviction
    weights = []
    for t in trades:
        conv = t.get('conviction', 16)
        if conv >= 18:
            weights.append(5)
        elif conv == 17:
            weights.append(3)
        else:
            weights.append(2)

    total_weight = sum(weights)

    # First pass: raw percentages by weight, clamped to min-33%
    min_pct = min_per_trade / total_capital
    raw_pcts = []
    for w in weights:
        pct = max(min_pct, min(MAX_PCT_PER_TRADE, w / total_weight))
        raw_pcts.append(pct)

    # Normalize so total never exceeds 100%
    total_pct = sum(raw_pcts)
    if total_pct > 1.0:
        raw_pcts = [p / total_pct for p in raw_pcts]

    result = []
    for t, pct in zip(trades, raw_pcts):
        alloc = total_capital * pct
        price = t.get('previous_close', 0)
        qty = max(1, int(alloc / price)) if price > 0 else 0
        result.append((t, qty, alloc))
        log.info(f"  Alloc: {t['symbol']} conv={t['conviction']} -> {pct*100:.0f}% = Rs {alloc:,.0f}")

    return result


# ── Main Pipeline ──────────────────────────────────────────

def scan_news(date_str):
    """Full news scanning pipeline. Returns list of trades with conv 16+."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    prev_dt = dt - timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    log.info(f"Scanning news for {date_str} (prev: {prev_date})")

    # Step 1: Fetch news
    articles = fetch_news(date_str)
    filtered = filter_stock_news(articles)
    log.info(f"Found {len(articles)} total, {len(filtered)} after filter")

    if not filtered:
        log.info("No news found")
        return []

    # Step 2: Claude picks headlines
    headlines_text = f"=== {len(filtered)} NEWS HEADLINES FOR {date_str} ===\n\n"
    for i, a in enumerate(filtered):
        headlines_text += f"[{i}] [{a['published'][:25]}] {a['title']}\n"

    pick_prompt = f"""You are scanning pre-market news for {date_str}. Below are {len(filtered)} headlines.
Pick the ones that could MOVE a specific stock >1% today. Return ONLY a JSON list of indices.

SKIP these (DO NOT pick):
- Quarterly results, earnings, profit/revenue announcements
- Generic market commentary
- Opinion/analysis pieces

PICK these:
- Block deals, bulk deals, large stake changes
- Order wins, contracts worth crores
- Regulatory actions (SEBI, RBI, NCLT)
- Analyst upgrades/downgrades with specific target prices
- Management changes
- M&A, acquisitions, mergers, demergers
- Sector policy, govt schemes, tariff changes
- IPO listings, index inclusion/exclusion
- Partnerships, JVs with major companies
- Promoter buying/selling

{headlines_text}

Respond with ONLY a JSON array of indices, e.g. [0, 3, 7, 12]. Nothing else."""

    log.info("Claude picking headlines...")
    pick_raw = call_claude(pick_prompt)
    if not pick_raw:
        log.error("No response from headline picker")
        return []

    idx_match = re.search(r'\[[\d,\s]+\]', pick_raw)
    if not idx_match:
        log.error(f"Could not parse indices: {pick_raw[:200]}")
        return []

    selected = json.loads(idx_match.group())
    log.info(f"Selected {len(selected)} articles")

    if not selected:
        log.info("No tradeable headlines")
        return []

    # Step 3: Scrape content
    log.info("Scraping articles...")
    scrape_articles_by_index(filtered, selected)

    news_text = f"\n=== SELECTED NEWS FOR {date_str} (full content) ===\n\n"
    for i in selected:
        if i >= len(filtered): continue
        a = filtered[i]
        news_text += f"[{i}] [{a['published'][:25]}] [{a.get('source','')}]\n"
        news_text += f"HEADLINE: {a['title']}\n"
        content = a.get('content', '')
        if content:
            news_text += f"ARTICLE:\n{content}\n"
        news_text += "\n---\n\n"

    # Step 4: Claude analyzes
    schema_str = json.dumps(ScanOutput.model_json_schema(), indent=2)
    prompt = ANALYST_PROMPT.replace("{date}", date_str).replace("{prev_date}", prev_date).replace("{schema}", schema_str)
    prompt += f"\n\n{news_text}"
    prompt += "\nUse web search ONLY to find previous closing prices. Do NOT search for additional news."

    log.info("Claude analyzing articles...")
    raw = call_claude(prompt)
    if not raw:
        log.error("No response from analyzer")
        return []

    # Retry JSON if parse fails
    parsed, error = parse_response(raw)
    if error:
        log.warning(f"First parse failed: {error}. Retrying...")
        fix_prompt = f"Your previous response had invalid JSON. Output ONLY valid JSON:\n\n{raw[-2000:]}"
        raw = call_claude(fix_prompt)
        if raw:
            parsed, error = parse_response(raw)

    if error or not parsed:
        log.error(f"Parse failed: {error}")
        return []

    # Filter conv 16+
    high_conv = [t for t in parsed.trades if t.get('conviction', 0) >= MIN_CONVICTION and t.get('call') != 'SKIP']
    log.info(f"Total trades: {len(parsed.trades)}, Conv {MIN_CONVICTION}+: {len(high_conv)}")

    return high_conv


def execute_trades(trades, paper_mode=False):
    """Execute MIS orders via INDmoney, sized by conviction slabs."""
    capital = get_available_capital()
    allocations = calculate_qty_and_capital(trades, capital)

    executed = []
    for t, qty, alloc in allocations:
        sym = t['symbol']
        call = t['call']
        conv = t['conviction']
        side = 'BUY' if call == 'BUY_MIS' else 'SELL'
        price = t.get('previous_close', 0)

        log.info(f"{'[PAPER] ' if paper_mode else ''}Executing: {side} {qty} x {sym} @ ~{price:.1f} (conv {conv}, alloc Rs {alloc:,.0f})")

        if paper_mode:
            result = {'status': 'paper', 'symbol': sym, 'side': side, 'qty': qty}
        else:
            try:
                result = api.place_order(sym, qty, side, price=0, order_type='MARKET', product='INTRADAY')
            except Exception as e:
                log.error(f"Order failed for {sym}: {e}")
                result = {'status': 'error', 'message': str(e)}

        executed.append({
            'symbol': sym,
            'call': call,
            'conviction': conv,
            'qty': qty,
            'side': side,
            'price': price,
            'capital_allocated': alloc,
            'result': result,
            'story': t.get('story', ''),
            'timestamp': datetime.now().isoformat(),
        })

        emoji = '🟢' if side == 'BUY' else '🔴'
        msg = f"{emoji} <b>{side} MIS: {sym}</b>\nConv: {conv}/20 | Qty: {qty} | Rs {alloc:,.0f}\nExpected: {t.get('expected_move','?')}\n{t.get('story','')[:200]}"
        send_telegram(msg)

    return executed


def main():
    scan_only = '--scan-only' in sys.argv
    backtest = '--backtest' in sys.argv
    paper_mode = '--paper' in sys.argv

    if not CLAUDE_KEY:
        log.error("Set ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    # Determine date
    if backtest:
        idx = sys.argv.index('--backtest')
        date_str = sys.argv[idx + 1]
    else:
        date_str = datetime.now().strftime('%Y-%m-%d')

    log.info(f"{'='*50}")
    log.info(f"NEWS BOT — {date_str} {'(SCAN ONLY)' if scan_only else '(PAPER)' if paper_mode else '(LIVE)'}")
    log.info(f"{'='*50}")

    # Scan
    trades = scan_news(date_str)

    if not trades:
        log.info("No conv 16+ trades found. Sitting out today.")
        send_telegram(f"📊 News Bot {date_str}: No high-conviction trades. Sitting out.")
        return

    # Display
    log.info(f"\n{'='*50}")
    log.info(f"TRADES TO EXECUTE ({len(trades)})")
    for t in trades:
        log.info(f"  {t['symbol']} {t['call']} conv={t['conviction']} | {t.get('news_summary','')[:80]}")
        log.info(f"    Q1: {t.get('q1_surprising','')[:60]}")
        log.info(f"    Q3: {t.get('q3_material','')[:60]}")
        log.info(f"    Q5: {t.get('q5_continuation','')[:60]}")

    if scan_only:
        log.info("Scan only mode — not executing.")
        return

    # Wait for market open if running live
    if not backtest:
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0)
        if now < market_open:
            wait = (market_open - now).total_seconds()
            log.info(f"Waiting {wait:.0f}s for market open at 9:15 AM...")
            send_telegram(f"⏳ Waiting for market open. {len(trades)} trades ready.")
            time.sleep(wait)

    # Check gap at open — skip if >3% (move already happened)
    if not backtest and not scan_only:
        log.info("Checking opening prices for gap filter (via INDmoney LTP)...")
        time.sleep(5)  # wait a few seconds after 9:15 for first tick

        filtered_trades = []
        for t in trades:
            sym = t['symbol']
            prev = t.get('previous_close', 0)
            if prev <= 0:
                filtered_trades.append(t)
                continue
            try:
                ltp_data = api.get_ltp([sym])
                open_price = ltp_data.get(sym, 0)
                if open_price <= 0:
                    log.warning(f"  {sym}: no LTP, entering with prev_close as entry")
                    t['entry_price'] = prev
                    filtered_trades.append(t)
                    continue

                gap = abs((open_price - prev) / prev) * 100
                if gap > 3:
                    log.info(f"  SKIP {sym}: gapped {gap:.1f}% — move already priced at open")
                    send_telegram(f"⏭ SKIP {sym}: gapped {gap:.1f}% at open. Move done.")
                    continue
                else:
                    log.info(f"  {sym}: gap {gap:.1f}% OK — entering at {open_price:.1f}")
                    t['entry_price'] = open_price
            except Exception as e:
                log.warning(f"  {sym}: gap check failed ({e}), entering with prev_close")
                t['entry_price'] = prev
            filtered_trades.append(t)
        trades = filtered_trades

    # Execute at open
    executed = execute_trades(trades, paper_mode=paper_mode)

    # Monitor positions via WebSocket: stop loss + square off at 3:10 PM
    if not backtest and executed:
        feed = PriceFeed()
        token = api.get_token()
        feed.start(token)
        time.sleep(2)

        # Subscribe to all traded symbols
        symbols = [e['symbol'] for e in executed]
        feed.subscribe(symbols)

        log.info(f"Monitoring {len(executed)} positions via WebSocket. SL={STOP_LOSS_PCT}%, exit=3:10 PM")
        active = list(executed)

        while active:
            now = datetime.now()

            # 3:10 PM hard exit
            if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
                log.info("3:10 PM — Squaring off all remaining positions")
                for e in active:
                    reverse_side = 'SELL' if e['side'] == 'BUY' else 'BUY'
                    if paper_mode:
                        log.info(f"[PAPER] Square-off: {reverse_side} {e['qty']} x {e['symbol']}")
                    else:
                        place_indmoney_order(e['symbol'], reverse_side, e['qty'])
                    send_telegram(f"🔄 Square-off: {reverse_side} {e['qty']}x {e['symbol']}")
                break

            # Check stop loss every 5 seconds (WebSocket is real-time, no rate limit)
            for e in active[:]:
                sym = e['symbol']
                entry = e.get('entry_price', e.get('price', 0))
                if entry <= 0:
                    continue

                current = feed.get_ltp(sym)
                if not current:
                    continue

                if e['side'] == 'BUY':
                    move = ((current - entry) / entry) * 100
                    hit_sl = move <= -STOP_LOSS_PCT
                else:
                    move = ((current - entry) / entry) * 100
                    hit_sl = move >= STOP_LOSS_PCT

                if hit_sl:
                    reverse_side = 'SELL' if e['side'] == 'BUY' else 'BUY'
                    log.info(f"STOP LOSS HIT: {sym} at {current:.1f} ({move:+.1f}% from entry {entry:.1f})")
                    if paper_mode:
                        log.info(f"[PAPER] SL exit: {reverse_side} {e['qty']} x {sym}")
                    else:
                        place_indmoney_order(sym, reverse_side, e['qty'])
                    send_telegram(f"🛑 STOP LOSS: {reverse_side} {e['qty']}x {sym} at {current:.1f} ({move:+.1f}%)")
                    active.remove(e)

            if active:
                time.sleep(5)  # check every 5 seconds (WebSocket is instant)

    # Save results
    out = {
        'date': date_str,
        'scan_time': datetime.now().isoformat(),
        'trades': trades,
        'executed': executed,
        'mode': 'paper' if paper_mode else 'live',
    }
    out_path = LOG_DIR / f'trades_{date_str}.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    log.info(f"Saved to {out_path}")

    summary = f"📊 News Bot {date_str}:\n"
    for e in executed:
        summary += f"  {'🟢' if e['side']=='BUY' else '🔴'} {e['side']} {e['qty']}x {e['symbol']} (conv {e['conviction']})\n"
    send_telegram(summary)


def continuous_scan():
    """Intraday continuous scanner — runs every 15 min from 9:30 AM to 2:30 PM.
    Catches news that breaks during market hours and enters MIS immediately.
    """
    paper_mode = '--paper' in sys.argv

    if not CLAUDE_KEY:
        log.error("Set ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y-%m-%d')
    seen_headlines = set()  # track headlines already processed, avoid re-trading
    active_positions = []   # track positions for SL monitoring
    feed = None             # WebSocket price feed, started once

    log.info(f"{'='*50}")
    log.info(f"NEWS BOT CONTINUOUS — {date_str} {'(PAPER)' if paper_mode else '(LIVE)'}")
    log.info(f"Scanning every 5 min from 9:30 AM to 2:30 PM")
    log.info(f"{'='*50}")

    while True:
        now = datetime.now()

        # Only scan 9:30 AM to 2:30 PM (need 40 min for trade to play out before 3:10 exit)
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            time.sleep(60)
            continue
        if now.hour > 14 or (now.hour == 14 and now.minute > 30):
            log.info("2:30 PM — stopping continuous scan (too late for new entries)")
            break

        log.info(f"\n--- Intraday scan at {now.strftime('%H:%M')} ---")

        # Fetch latest news (Google News RSS returns most recent first)
        try:
            articles = fetch_live_rss()  # real-time RSS, not date-filtered Google News
            filtered = filter_stock_news(articles)

            # Only process NEW headlines not seen before
            new_articles = []
            for a in filtered:
                title = a['title']
                if title not in seen_headlines:
                    seen_headlines.add(title)
                    new_articles.append(a)

            if not new_articles:
                log.info(f"  No new articles since last scan")
                time.sleep(300)  # wait 15 min
                continue

            log.info(f"  {len(new_articles)} NEW articles (of {len(filtered)} total)")

            # Claude picks tradeable headlines from new articles only
            headlines_text = f"=== {len(new_articles)} NEW NEWS HEADLINES (just broke) ===\n\n"
            for i, a in enumerate(new_articles):
                headlines_text += f"[{i}] [{a['published'][:25]}] {a['title']}\n"

            pick_prompt = f"""You are scanning BREAKING NEWS during market hours on {date_str}.
These headlines just broke in the last few minutes. Market is OPEN right now.
Pick ones that could move a stock >1% in the NEXT FEW HOURS. Return ONLY a JSON array of indices.

SKIP: quarterly results, generic commentary, opinion pieces.
PICK: block deals, order wins, regulatory, analyst calls, M&A, policy, IPO listings.

{headlines_text}

Respond with ONLY a JSON array, e.g. [0, 3]. Nothing else."""

            pick_raw = call_claude(pick_prompt)
            if not pick_raw:
                time.sleep(300)
                continue

            idx_match = re.search(r'\[[\d,\s]*\]', pick_raw)
            if not idx_match:
                time.sleep(300)
                continue

            selected = json.loads(idx_match.group())
            if not selected:
                log.info("  No tradeable headlines in new batch")
                time.sleep(300)
                continue

            log.info(f"  Claude picked {len(selected)} for analysis")

            # Scrape content
            scrape_articles_by_index(new_articles, selected)

            news_text = f"\n=== BREAKING NEWS (just now, market is OPEN) ===\n\n"
            for i in selected:
                if i >= len(new_articles):
                    continue
                a = new_articles[i]
                news_text += f"[{i}] [{a['published'][:25]}] [{a.get('source','')}]\n"
                news_text += f"HEADLINE: {a['title']}\n"
                content = a.get('content', '')
                if content:
                    news_text += f"ARTICLE:\n{content}\n"
                news_text += "\n---\n\n"

            # Analyze with modified prompt for intraday
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            prev_dt = dt - timedelta(days=1)
            while prev_dt.weekday() >= 5:
                prev_dt -= timedelta(days=1)
            prev_date = prev_dt.strftime('%Y-%m-%d')

            schema_str = json.dumps(ScanOutput.model_json_schema(), indent=2)
            prompt = ANALYST_PROMPT.replace("{date}", date_str).replace("{prev_date}", prev_date).replace("{schema}", schema_str)
            prompt += f"\n\nIMPORTANT: Market is OPEN RIGHT NOW. These trades will be entered IMMEDIATELY, not at next morning open."
            prompt += f"\n\n{news_text}"
            prompt += "\nUse web search to find current stock price (not previous close). The stock may have already reacted."

            raw = call_claude(prompt)
            if not raw:
                time.sleep(300)
                continue

            parsed, error = parse_response(raw)
            if error:
                fix_prompt = f"Output ONLY valid JSON:\n\n{raw[-2000:]}"
                raw = call_claude(fix_prompt)
                if raw:
                    parsed, error = parse_response(raw)

            if error or not parsed:
                log.info(f"  Parse failed: {error}")
                time.sleep(300)
                continue

            # Filter conv 16+
            high_conv = [t for t in parsed.trades if t.get('conviction', 0) >= MIN_CONVICTION and t.get('call') != 'SKIP']

            if not high_conv:
                log.info(f"  {len(parsed.trades)} trades found, none conv 16+")
                time.sleep(300)
                continue

            # Set entry price to current LTP (no gap filter for live — entering at market price)
            trades_to_execute = []
            for t in high_conv:
                sym = t['symbol']
                ltp = api.get_ltp([sym]).get(sym, 0)
                if ltp > 0:
                    t['entry_price'] = ltp
                trades_to_execute.append(t)

            if not trades_to_execute:
                time.sleep(300)
                continue

            # Execute
            log.info(f"  EXECUTING {len(trades_to_execute)} intraday trades!")
            executed = execute_trades(trades_to_execute, paper_mode=paper_mode)
            active_positions.extend(executed)

            # Start WebSocket if first trade
            if feed is None and active_positions:
                feed = PriceFeed()
                token = api.get_token()
                feed.start(token)
                time.sleep(2)

            # Subscribe new symbols
            if feed and executed:
                feed.subscribe([e['symbol'] for e in executed])

        except Exception as e:
            log.error(f"  Scan error: {e}")

        # Check SL on active positions
        if feed and active_positions:
            for e in active_positions[:]:
                sym = e['symbol']
                entry = e.get('entry_price', e.get('price', 0))
                if entry <= 0:
                    continue
                current = feed.get_ltp(sym)
                if not current:
                    continue
                if e['side'] == 'BUY':
                    move = ((current - entry) / entry) * 100
                    hit_sl = move <= -STOP_LOSS_PCT
                else:
                    move = ((current - entry) / entry) * 100
                    hit_sl = move >= STOP_LOSS_PCT
                if hit_sl:
                    reverse_side = 'SELL' if e['side'] == 'BUY' else 'BUY'
                    log.info(f"STOP LOSS HIT: {sym} at {current:.1f} ({move:+.1f}%)")
                    if not paper_mode:
                        place_indmoney_order(sym, reverse_side, e['qty'])
                    send_telegram(f"🛑 SL: {reverse_side} {e['qty']}x {sym} ({move:+.1f}%)")
                    active_positions.remove(e)

        time.sleep(300)  # 15 min between scans

    # 3:10 PM — square off all remaining
    if active_positions:
        log.info("3:10 PM — Squaring off all intraday positions")
        for e in active_positions:
            reverse_side = 'SELL' if e['side'] == 'BUY' else 'BUY'
            if not paper_mode:
                place_indmoney_order(e['symbol'], reverse_side, e['qty'])
            send_telegram(f"🔄 Square-off: {reverse_side} {e['qty']}x {e['symbol']}")

    log.info("Continuous scanner done for today.")


if __name__ == '__main__':
    if '--continuous' in sys.argv:
        continuous_scan()
    else:
        main()
