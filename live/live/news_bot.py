"""
Live News Trading Bot — Runs at 8:30 AM IST, finds conv 16+ trades, executes at 9:15 AM.

Flow:
  8:30 AM — Fetch news from Google News RSS (previous evening + pre-market)
  8:31 AM — Claude scans headlines, picks tradeable ones
  8:32 AM — Scrape full article content for selected headlines
  8:33 AM — Claude reads full articles, applies 5 questions, outputs trades with conviction
  8:45 AM — Trades ready. Conv 16+ only. Send Telegram alert.
  9:15 AM — Market opens. Execute MIS orders via Upstox.
  3:15 PM — Auto square-off all MIS positions (or broker does at 3:20).

Usage:
  python news_bot.py              # Run full pipeline (scan + trade)
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

from news_fetcher import fetch_news, filter_stock_news, scrape_articles_by_index
from pydantic import BaseModel, Field, field_validator
from enum import Enum
import requests

CLAUDE_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
MIN_CONVICTION = 16
STOP_LOSS_PCT = 3.0  # Exit if moves 3% against us from entry
MAX_TRADES_PER_DAY = 3


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
- Maximum 3 trades. Pick only the highest conviction ones.
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
        from live import indmoney_client as api
        result = api.place_order(symbol, qty, side, price=0, order_type='MARKET', product='INTRADAY')
        log.info(f"INDmoney order: {side} {qty} x {symbol} -> {result}")
        return result
    except Exception as e:
        log.error(f"INDmoney error: {e}")
        return {'status': 'error', 'message': str(e)}


def get_available_capital():
    """Fetch available capital from INDmoney account."""
    try:
        from live import indmoney_client as api
        funds = api.get_funds()
        log.info(f"INDmoney available funds: Rs {funds:,.0f}")
        return funds
    except Exception as e:
        log.warning(f"Could not fetch funds: {e}. Using default Rs 1L")
        return 100000


def calculate_qty_and_capital(trades, total_capital):
    """Split capital across trades based on conviction slabs.

    Conviction slabs:
      18-20: 40% of capital (slam dunk, biggest bet)
      17:    35% of capital
      16:    25% of capital (threshold, smallest bet)

    If only 1 trade: gets 100% regardless.
    If multiple: split by slab weights, capped at MAX_TRADES_PER_DAY.
    """
    trades = trades[:MAX_TRADES_PER_DAY]

    if len(trades) == 1:
        t = trades[0]
        price = t.get('previous_close', 0)
        qty = max(1, int(total_capital / price)) if price > 0 else 0
        return [(t, qty, total_capital)]

    # Assign weights by conviction
    weights = []
    for t in trades:
        conv = t.get('conviction', 16)
        if conv >= 18:
            weights.append(40)
        elif conv == 17:
            weights.append(35)
        else:
            weights.append(25)

    total_weight = sum(weights)
    result = []
    for t, w in zip(trades, weights):
        alloc = total_capital * (w / total_weight)
        price = t.get('previous_close', 0)
        qty = max(1, int(alloc / price)) if price > 0 else 0
        result.append((t, qty, alloc))

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
                from live import indmoney_client as api
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

    # Check gap at open — skip if >5% (move already happened)
    if not backtest and not scan_only:
        log.info("Checking opening prices for gap filter...")
        import yfinance as yf
        filtered_trades = []
        for t in trades:
            sym = t['symbol']
            prev = t.get('previous_close', 0)
            if prev <= 0:
                filtered_trades.append(t)
                continue
            try:
                df = yf.download(f'{sym}.NS', period='1d', interval='1m', progress=False)
                if not df.empty:
                    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
                        df.columns = df.columns.get_level_values(0)
                    open_price = float(df.iloc[0]['Open'])
                    gap = abs((open_price - prev) / prev) * 100
                    if gap > 3:
                        log.info(f"  SKIP {sym}: gapped {gap:+.1f}% — move already priced at open")
                        send_telegram(f"⏭ SKIP {sym}: gapped {gap:.1f}% at open. Move done.")
                        continue
                    else:
                        log.info(f"  {sym}: gap {gap:.1f}% OK — entering")
                        t['entry_price'] = open_price
            except Exception as e:
                log.warning(f"  {sym}: gap check failed ({e}), entering anyway")
            filtered_trades.append(t)
        trades = filtered_trades

    # Execute at open
    executed = execute_trades(trades, paper_mode=paper_mode)

    # Monitor positions: stop loss + square off at 3:10 PM
    if not backtest and executed:
        import yfinance as yf

        log.info(f"Monitoring {len(executed)} positions. SL={STOP_LOSS_PCT}%, exit=3:10 PM")
        active = list(executed)  # copy

        while active:
            now = datetime.now()

            # 3:10 PM hard exit
            if now.hour >= 15 and now.minute >= 10:
                log.info("3:10 PM — Squaring off all remaining positions")
                for e in active:
                    reverse_side = 'SELL' if e['side'] == 'BUY' else 'BUY'
                    if paper_mode:
                        log.info(f"[PAPER] Square-off: {reverse_side} {e['qty']} x {e['symbol']}")
                    else:
                        place_indmoney_order(e['symbol'], reverse_side, e['qty'])
                    send_telegram(f"🔄 Square-off: {reverse_side} {e['qty']}x {e['symbol']}")
                break

            # Check stop loss every 60 seconds
            for e in active[:]:
                sym = e['symbol']
                entry = e.get('entry_price', e.get('price', 0))
                if entry <= 0:
                    continue
                try:
                    df = yf.download(f'{sym}.NS', period='1d', interval='1m', progress=False)
                    if df.empty:
                        continue
                    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
                        df.columns = df.columns.get_level_values(0)
                    current = float(df.iloc[-1]['Close'])

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
                except Exception as ex:
                    log.warning(f"Price check failed for {sym}: {ex}")

            if active:
                time.sleep(60)  # check every minute

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


if __name__ == '__main__':
    main()
