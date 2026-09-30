"""
News-based Intraday Trading — scrape breaking news, analyze, trade MIS same day.
No XBRL, no filings. Pure news + sentiment → BUY_MIS or SELL_MIS.

Usage:
  python news_intraday.py                    # scan today's news
  python news_intraday.py --date 2024-08-14  # scan specific date (backtest)
"""
import json, os, sys, time, re
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
import requests

CLAUDE_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"


class MISCall(str, Enum):
    BUY_MIS = "BUY_MIS"
    SELL_MIS = "SELL_MIS"
    SKIP = "SKIP"


class NewsTradeOutput(BaseModel):
    stocks_found: list[dict] = Field(description="List of stocks with actionable news. Each dict has: symbol, company_name, news_headline, news_source, news_timestamp (exact time)")

    trades: list[dict] = Field(description="""List of trade predictions (PRE-MARKET, before 9:15 AM). Each dict has:
        symbol, call (BUY_MIS/SELL_MIS/SKIP), conviction (1-20),
        story (3-5 sentence analyst narrative),
        news_summary (what happened),
        news_timestamp (time published),
        previous_close (closing price day before),
        q1_surprising (is this news SURPRISING or routine for this company? one sentence),
        q2_binding (is this BINDING revenue or speculative/opinion? one sentence),
        q3_material (what % of market cap is the impact? is it material?),
        q4_one_time (is this a one-time shock that will bounce, or ongoing? one sentence),
        q5_continuation (will the move continue intraday after any gap, or is the gap the entire move?),
        expected_move (prediction e.g. '+3-5%'),
        risk (what could go wrong),
        entry_timing (at open / wait 15min / wait for dip)""")

    validation: list[dict] = Field(description="""AFTER-THE-FACT validation. Search for actual closing price on the date. Each dict has:
        symbol, actual_close, actual_change_pct, correct (true/false based on call direction)""")


NEWS_SCANNER_PROMPT = """You are an intraday news trader on Indian stock markets (NSE/BSE).
Your job: find stocks that will move TODAY based on breaking news, and decide BUY_MIS or SELL_MIS.

Date: {date}

STEP 1 — NEWS IS PRE-FETCHED FOR YOU:
The news articles below have been fetched from Google News RSS with strict date filtering.
All articles are from {prev_date} evening to {date} morning ONLY. No future leakage.
DO NOT search for more news. Use ONLY the articles provided below.

Your job: read each headline, understand what happened, and decide if it will move the stock today.

IMPORTANT FILTERING RULES:
1. SKIP any article about quarterly results / earnings / profit growth or decline — that's handled by a separate filing system.
2. For stocks you DO pick, check if the stock already moved big (>3%) on the PREVIOUS trading day — if yes, the news is priced in.

React to: block deals, order wins, contracts, regulatory actions, analyst calls, management changes, M&A, sector policy, partnerships, IPO listings, stake changes, index inclusion.

Use web search to find the stock's previous closing price AND previous day's move.

BEFORE making any call, READ BETWEEN THE LINES. Ask yourself these 5 questions for each trade:

Q1: IS THIS ACTUALLY SURPRISING? Or does this company get this type of news routinely?
   - HAL getting a defense order = routine, market expects it
   - GRSE getting an EXPORT order = surprising, they rarely export
   - A PSU getting a govt contract = expected. A private company getting one = surprising.

Q2: IS THIS BINDING REVENUE or just speculation?
   - Signed contract with delivery timeline = binding, real
   - MoU / LOI / "in talks" / "exploring" = non-binding, speculative, gaps then fades
   - Analyst target / rating change = opinion, not revenue

Q3: IS THE IMPACT MATERIAL relative to the company's size?
   - Rs 60Cr order for a Rs 5000Cr market cap company = noise (1.2%)
   - Rs 450Cr export order for a Rs 3000Cr company = material (15%)
   - Calculate: order value / market cap. Below 3% = not material enough to move the stock.

Q4: IS THIS A ONE-TIME SHOCK that will bounce back?
   - Promoter death, single analyst downgrade, one-time SEBI notice = emotional gap at open, then smart money buys the dip
   - These gap big but REVERSE intraday. If you enter MIS after the gap, you're on the wrong side.
   - OFS / block deal = supply event, floor price acts as support, bounces intraday

Q5: WILL THIS MOVE CONTINUE AFTER THE GAP or is the gap THE ENTIRE MOVE?
   - Policy changes (MEP removal, tariff change) = ONGOING impact, stock drifts all day
   - USFDA observations = ONGOING risk, sellers come in waves all day
   - Analyst downgrade = ONE-TIME repricing, gap is the move, flat intraday
   - Penny stock hitting circuit = NO entry possible, skip

If any of Q1-Q4 flags a problem, LOWER conviction to below 13 or SKIP. Only call BUY/SELL MIS with conv 16+ when ALL 5 questions are clean.

STEP 2 — FILTER:
Only include stocks where:
- The news is FRESH (same day or previous evening)
- The news is MATERIAL (will move the stock >1%)
- The stock has enough liquidity for MIS trading (not penny stocks)

STEP 3 — FOR EACH TRADEABLE STOCK:
Search for the stock's current price and recent trend to gauge if the news is priced in or fresh.

STEP 4 — OUTPUT:
Respond with ONLY a valid JSON object. No text before or after the JSON. No markdown code blocks. Just raw JSON matching this schema:

{schema}

IMPORTANT OUTPUT RULES:
- Maximum 3 trades. Pick only the highest conviction ones.
- Stories max 2-3 sentences. Be terse.
- stocks_found max 5 entries.
- validation max 3 entries (matching trades only).
- Output MUST be parseable JSON. No trailing commas.

MIS trades are 5x leveraged intraday. Even 2% move = 10% return on margin.
Be selective — quality over quantity. 3-5 high conviction trades per day is ideal.

CRITICAL — TIME AND LEAKAGE RULES:
You are simulating being an analyst at 8:30 AM on {date}, BEFORE market opens at 9:15 AM.
- You can ONLY use news published BEFORE 9:15 AM on {date}
- Previous evening news (after 3:30 PM previous day) is valid — these are overnight catalysts
- Pre-market news (6 AM - 9:15 AM on {date}) is valid
- ANY news from AFTER 9:15 AM on {date} is FUTURE LEAKAGE — you MUST discard it
- You CANNOT know the stock's actual price move on {date} — that hasn't happened yet
- You CANNOT reference intraday price action, closing price, or volume from {date}

For EVERY web source you use, note its exact timestamp. If a source says "stock surged 7% on [date]" — that is AFTER the fact, discard it. You are PREDICTING, not reporting.

For each trade, include:
- story: 5-8 sentence analyst narrative explaining WHY this news will move the stock
- news_timestamp: exact time the news was published (e.g. "2024-08-13 18:45 IST" or "2024-08-14 07:30 IST pre-market")
- previous_close: stock's closing price on the day BEFORE {date}
- expected_move: your prediction (e.g. '+3-5%' or '-5-8%')
- actual_move_pct: set to 0 — you do NOT know this yet

After giving all trades, do a SEPARATE validation step:
Search for what ACTUALLY happened to each stock on {date} (closing price, % change).
Report this separately as a "VALIDATION" section — clearly marked as after-the-fact checking.
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
                    "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
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
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  API error {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"  Error: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
    return None


def parse_response(raw_text):
    # Find the outermost JSON object — match first { to its closing }
    depth = 0
    start = None
    for i, ch in enumerate(raw_text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                json_str = raw_text[start:i+1]
                try:
                    data = json.loads(json_str)
                    result = NewsTradeOutput(**data)
                    return result, None
                except json.JSONDecodeError:
                    # Try fixing common issues — trailing commas
                    fixed = re.sub(r',\s*}', '}', json_str)
                    fixed = re.sub(r',\s*]', ']', fixed)
                    try:
                        data = json.loads(fixed)
                        result = NewsTradeOutput(**data)
                        return result, None
                    except Exception as e:
                        return None, f"JSON fix failed: {e}"
                except Exception as e:
                    return None, f"Pydantic error: {e}"
    return None, "No complete JSON object found"


def main():
    if not CLAUDE_KEY:
        print("ERROR: Set ANTHROPIC_API_KEY in .env"); sys.exit(1)

    # Get date
    date = None
    for i, arg in enumerate(sys.argv):
        if arg == '--date' and i + 1 < len(sys.argv):
            date = sys.argv[i + 1]
    if not date:
        from datetime import datetime
        date = datetime.now().strftime('%Y-%m-%d')

    # Calculate previous trading day
    from datetime import datetime, timedelta
    dt = datetime.strptime(date, '%Y-%m-%d')
    prev_dt = dt - timedelta(days=1)
    # Skip weekends
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    print(f"Scanning news for: {date} (prev trading day: {prev_date})")
    print("=" * 60)

    # STEP 1: Fetch all timestamped news
    from news_fetcher import fetch_news, filter_stock_news, scrape_articles_by_index
    print("Fetching news from Google News RSS...")
    articles = fetch_news(date)
    filtered = filter_stock_news(articles)
    print(f"  Found {len(articles)} total, {len(filtered)} after light filter")

    if not filtered:
        print("  No news found for this date.")
        return

    # STEP 2: Send ALL headlines to Claude — ask which ones to read in full
    headlines_text = f"=== {len(filtered)} NEWS HEADLINES FOR {date} ===\n\n"
    for i, a in enumerate(filtered):
        headlines_text += f"[{i}] [{a['published'][:25]}] {a['title']}\n"

    pick_prompt = f"""You are scanning pre-market news for {date}. Below are {len(filtered)} headlines.
Pick the ones that could MOVE a specific stock >1% today. Return ONLY a JSON list of indices.

SKIP these (DO NOT pick):
- Quarterly results, earnings, profit/revenue announcements (e.g. "Q1 profit rises 50%", "net profit jumps", "revenue up 23%") — handled by separate filing system
- Generic market commentary ("Nifty outlook", "market may open flat")
- Opinion/analysis pieces ("should you buy?", "what lies ahead")
- Macro without specific stock impact

PICK these:
- Block deals, bulk deals, large stake changes
- Order wins, contracts worth crores
- Regulatory actions (SEBI, RBI, NCLT)
- Analyst upgrades/downgrades with specific target prices
- Management changes (CEO/CFO resign/appoint)
- M&A, acquisitions, mergers, demergers
- Sector policy, govt schemes, tariff changes
- IPO listings, index inclusion/exclusion
- Partnerships, JVs with major companies
- Promoter buying/selling

{headlines_text}

Respond with ONLY a JSON array of indices, e.g. [0, 3, 7, 12]. Nothing else."""

    print("  Claude picking tradeable headlines...")
    pick_raw = call_claude(pick_prompt)
    if not pick_raw:
        print("  ERROR: No response from headline picker"); return

    # Parse indices
    idx_match = re.search(r'\[[\d,\s]+\]', pick_raw)
    if not idx_match:
        print(f"  Could not parse indices from: {pick_raw[:200]}")
        return
    selected_indices = json.loads(idx_match.group())
    print(f"  Selected {len(selected_indices)} articles to read: {selected_indices}")

    if not selected_indices:
        print("  No tradeable news found."); return

    # STEP 3: Scrape full content for selected articles only
    print("  Scraping article content...")
    scrape_articles_by_index(filtered, selected_indices)

    # Format selected articles with full content
    news_text = f"\n=== SELECTED NEWS FOR {date} (full content) ===\n\n"
    for i in selected_indices:
        if i >= len(filtered):
            continue
        a = filtered[i]
        news_text += f"[{i}] [{a['published'][:25]}] [{a.get('source','')}]\n"
        news_text += f"HEADLINE: {a['title']}\n"
        content = a.get('content', '')
        if content:
            news_text += f"ARTICLE:\n{content}\n"
        else:
            news_text += "(Article content could not be scraped)\n"
        news_text += "\n---\n\n"

    # STEP 4: Claude analyzes full articles + searches for prices → makes calls
    schema_str = json.dumps(NewsTradeOutput.model_json_schema(), indent=2)
    prompt = NEWS_SCANNER_PROMPT.replace("{date}", date).replace("{prev_date}", prev_date).replace("{schema}", schema_str)
    prompt += f"\n\n{news_text}"
    prompt += "\nUse web search ONLY to find previous closing prices for stocks in these articles. Do NOT search for additional news."

    print("  Claude analyzing full articles...")
    raw = call_claude(prompt)

    # If JSON parse fails, ask Claude to fix it
    if raw:
        parsed, error = parse_response(raw)
        if error:
            print(f"  First parse failed, asking Claude to output clean JSON...")
            fix_prompt = f"Your previous response had invalid JSON. Here's what you wrote:\n\n{raw[-2000:]}\n\nPlease output ONLY the valid JSON object. No text before or after. Fix any syntax errors."
            raw = call_claude(fix_prompt)
    if not raw:
        print("ERROR: No response"); return

    parsed, error = parse_response(raw)
    if error:
        print(f"PARSE ERROR: {error}")
        print(f"Raw response:\n{raw[:2000]}")
        return

    # Display results
    print(f"\nStocks with news: {len(parsed.stocks_found)}")
    for s in parsed.stocks_found:
        print(f"  {s.get('symbol','?'):>15} | {s.get('news_headline','')[:60]}")

    print(f"\n{'='*60}")
    print(f"TRADES ({len(parsed.trades)} recommendations)")
    print(f"{'='*60}")

    for t in parsed.trades:
        call = t.get('call', 'SKIP')
        conv = t.get('conviction', 0)
        sym = t.get('symbol', '?')

        print(f"\n  {sym} — {call} (conv {conv}/20)")
        print(f"  News [{t.get('news_timestamp','?')}]: {t.get('news_summary', '')[:150]}")
        print(f"  Prev close: {t.get('previous_close', '?')}")
        print(f"  Story: {t.get('story', '')[:200]}")
        print(f"  Q1 Surprising?  {t.get('q1_surprising', '?')}")
        print(f"  Q2 Binding?     {t.get('q2_binding', '?')}")
        print(f"  Q3 Material?    {t.get('q3_material', '?')}")
        print(f"  Q4 One-time?    {t.get('q4_one_time', '?')}")
        print(f"  Q5 Continues?   {t.get('q5_continuation', '?')}")
        print(f"  Expected: {t.get('expected_move', '')} | Entry: {t.get('entry_timing', '')}")
        print(f"  Risk: {t.get('risk', '')[:100]}")

    # Validation (after-the-fact)
    if parsed.validation:
        print(f"\n{'='*60}")
        print(f"VALIDATION (after-the-fact actual moves)")
        print(f"{'='*60}")
        correct = 0
        total = 0
        print(f"{'Symbol':<15} {'Call':<10} {'Conv':>4} {'Expected':>10} {'Actual':>8} {'OK?':<5}")
        print('-' * 60)
        for v in parsed.validation:
            sym = v.get('symbol', '?')
            actual = v.get('actual_change_pct', 0)
            ok = v.get('correct', False)
            # Find matching trade
            trade = next((t for t in parsed.trades if t.get('symbol') == sym), {})
            call = trade.get('call', '?')
            conv = trade.get('conviction', 0)
            exp = trade.get('expected_move', '?')
            total += 1
            if ok:
                correct += 1
            actual_str = f"{actual:+.1f}%" if isinstance(actual, (int, float)) else str(actual)
            print(f"{sym:<15} {call:<10} {conv:>4} {exp:>10} {actual_str:>8} {'OK' if ok else 'WRONG'}")

        print(f"\nACCURACY: {correct}/{total} = {correct/total*100:.0f}%")

    # Proper validation from yfinance
    import yfinance as yf
    from datetime import timedelta

    print(f"\n{'='*60}")
    print(f"VALIDATION (yfinance live prices)")
    print(f"{'='*60}")
    correct = 0
    total = 0
    print(f"{'Symbol':<15} {'Call':<10} {'Conv':>4} {'Prev':>8} {'Close':>8} {'D1':>7} {'OK?':<5}")
    print('-' * 65)

    dt_start = datetime.strptime(date, '%Y-%m-%d') - timedelta(days=5)
    dt_end = datetime.strptime(date, '%Y-%m-%d') + timedelta(days=12)

    for t in parsed.trades:
        sym = t.get('symbol', '?')
        call = t.get('call', 'SKIP')
        conv = t.get('conviction', 0)
        if call == 'SKIP':
            continue

        try:
            ticker = f"{sym}.NS"
            df = yf.download(ticker, start=dt_start.strftime('%Y-%m-%d'),
                           end=dt_end.strftime('%Y-%m-%d'), progress=False)
            if df.empty:
                print(f"{sym:<15} {call:<10} {conv:>4} {'N/A':>8} {'N/A':>8} {'N/A':>7} {'?':<5} no yfinance data")
                continue

            # Flatten multi-level columns if needed
            if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
                df.columns = df.columns.get_level_values(0)

            df.index = df.index.strftime('%Y-%m-%d')
            dates_sorted = sorted(df.index.tolist())

            if date not in dates_sorted:
                print(f"{sym:<15} {call:<10} {conv:>4} {'N/A':>8} {'N/A':>8} {'N/A':>7} {'?':<5} {date} not a trading day")
                continue

            idx = dates_sorted.index(date)
            if idx < 1:
                continue

            pre_close = float(df.loc[dates_sorted[idx-1], 'Close'])
            day_close = float(df.loc[dates_sorted[idx], 'Close'])
            d1_pct = ((day_close - pre_close) / pre_close) * 100

            total += 1
            if (call == 'BUY_MIS' and d1_pct > 0) or (call == 'SELL_MIS' and d1_pct < 0):
                correct += 1
                ok = 'OK'
            else:
                ok = 'WRONG'

            print(f"{sym:<15} {call:<10} {conv:>4} {pre_close:>8.1f} {day_close:>8.1f} {d1_pct:>+6.1f}% {ok:<5}")

            # D1-D5 path
            for d in range(2, 6):
                if idx + d - 1 < len(dates_sorted):
                    c = float(df.loc[dates_sorted[idx + d - 1], 'Close'])
                    p = ((c - pre_close) / pre_close) * 100
                    print(f"{'':>50} d{d}: {p:+.1f}%")

        except Exception as e:
            print(f"{sym:<15} {call:<10} {conv:>4} {'ERR':>8} {'':>8} {'':>7} {'?':<5} {str(e)[:30]}")

    if total:
        print(f"\nVALIDATED: {correct}/{total} = {correct/total*100:.0f}%")
    else:
        print(f"\nNo trades could be validated")

    # Save
    out_path = f'C:/tmp/news_trading/news_trades_{date}.json'
    with open(out_path, 'w') as f:
        json.dump(parsed.model_dump(), f, indent=2, default=str)
    print(f"\nSaved to {out_path}")


if __name__ == '__main__':
    main()
