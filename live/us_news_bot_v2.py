"""
US News Trading Bot V2 — Same architecture as Indian bot.
ALL Gemini — no Claude dependency.

Flow:
  6:30 PM IST (9:00 AM ET) — Fetch US pre-market news from RSS + Gemini search
  6:31 PM — Step 1: Cheap Gemini call filters stock-relevant headlines
  6:32 PM — Step 2: Batched Gemini calls with fundamentals (mcap, PE, 1m return)
  7:00 PM IST (9:30 AM ET) — Market opens. Execute trades via IBKR.
  7:00-12:50 AM — Monitor: trailing stop + timeout
  12:50 AM IST (3:50 PM ET) — Hard exit all positions

Usage:
  python us_news_bot_v2.py              # Live mode
  python us_news_bot_v2.py --paper      # Paper mode
  python us_news_bot_v2.py --scan-only  # Just scan
"""
import json, os, sys, time, re, logging, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

import requests
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from live.gemini_utils import call_gemini as gemini_call

# ── Config ──
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_KEY}'

MIN_PROJECTION = 3.0
SL_PCT = 3.0
TRAIL_ACTIVATE = 1.0
TRAIL_PCT = 1.0
TIMEOUT_MIN = 30
MAX_CAPITAL_PCT = 0.33

H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
ET = timezone(timedelta(hours=-4))  # US Eastern Time (EDT)
IST = timezone(timedelta(hours=5, minutes=30))

# ── Logging ──
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'us_news_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('us_news_bot')

# ── US RSS Feeds ──
RSS_FEEDS = [
    ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US', 'Yahoo Finance'),
    ('https://www.marketwatch.com/rss/topstories', 'MarketWatch'),
    ('https://feeds.feedburner.com/zaboravi/rss', 'Benzinga'),
    ('https://feeds.reuters.com/reuters/businessNews', 'Reuters'),
    ('https://rss.nytimes.com/services/xml/rss/nyt/Business.xml', 'NYT Business'),
]

# Google News queries for US stocks
GOOGLE_QUERIES = [
    'stock FDA approval today',
    'stock analyst upgrade downgrade target price',
    'stock acquisition merger deal announced',
    'biotech stock catalyst data readout',
    'stock contract award order win',
    'stock earnings surprise beat miss',
    'SEC enforcement action stock',
]


def fetch_all_news(window_hours=17):
    """Fetch US news from RSS feeds + Google News. Returns (articles, seen_keys)."""
    now = datetime.now(ET)
    cutoff = now
    window_start = cutoff - timedelta(hours=window_hours)

    articles = []
    seen = set()
    source_counts = {}

    # RSS feeds
    for rss_url, source_name in RSS_FEEDS:
        count = 0
        try:
            r = requests.get(rss_url, headers=H, timeout=15)
            if r.status_code != 200:
                source_counts[source_name] = 0
                continue
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item_xml in items:
                title_m = re.search(r'<title[^>]*>(.*?)</title>', item_xml, re.DOTALL)
                pub_m = re.search(r'<pubDate>(.*?)</pubDate>', item_xml, re.DOTALL)
                if not title_m:
                    continue
                title = re.sub(r'<!\[CDATA\[|\]\]>', '', title_m.group(1)).strip()
                title = re.sub(r'<[^>]+>', '', title).strip()

                pub_dt = None
                if pub_m:
                    raw = re.sub(r'<!\[CDATA\[|\]\]>', '', pub_m.group(1)).strip()
                    try:
                        pub_dt = parsedate_to_datetime(raw).astimezone(ET)
                    except Exception as e:
                        log.warning(f"Error: {e}")
                if pub_dt and not (window_start <= pub_dt <= cutoff):
                    continue

                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key in seen or len(title) < 15:
                    continue
                seen.add(key)
                articles.append({
                    'title': title,
                    'pubDate': pub_dt.strftime('%Y-%m-%d %H:%M ET') if pub_dt else '?',
                    'source': source_name,
                })
                count += 1
        except Exception as e:
            log.warning(f"Error: {e}")
        source_counts[source_name] = count

    # Google News RSS
    for q in GOOGLE_QUERIES:
        try:
            url = f'https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en'
            r = requests.get(url, headers=H, timeout=15)
            if r.status_code != 200:
                continue
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', r.text)
            gcount = 0
            for i in range(1, min(len(titles), 15)):
                title = titles[i][0] or titles[i][1]
                title = re.sub(r'<[^>]+>', '', title).strip()
                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key in seen or len(title) < 15:
                    continue
                seen.add(key)
                articles.append({'title': title, 'pubDate': '?', 'source': 'Google'})
                gcount += 1
            source_counts[f'Google:{q[:20]}'] = gcount
        except Exception as e:
            log.warning(f"Error: {e}")

    articles.sort(key=lambda x: x.get('pubDate', ''), reverse=True)
    log.info(f"Sources: {source_counts} | Total: {len(articles)}")
    return articles, seen


def call_gemini(prompt, use_grounding=True, max_retries=3):
    """Call Gemini via shared utility."""
    return gemini_call(prompt, grounding=use_grounding, max_retries=max_retries)

def filter_stock_headlines(articles):
    """Step 1: Cheap Gemini call to filter US stock-relevant headlines."""
    headlines = '\n'.join(f'[{i}] {a["title"]}' for i, a in enumerate(articles))

    prompt = (
        "You are a US stock news filter for NYSE/NASDAQ listed companies.\n"
        "Return index numbers of headlines where SOMETHING HAPPENED to a SPECIFIC company.\n\n"
        "## INCLUDE:\n"
        "[0] Vertex Pharma gets FDA approval for new CF drug -> INCLUDE (FDA approval)\n"
        "[1] Broadcom to acquire VMware for $61B -> INCLUDE (M&A)\n"
        "[2] Piper Sandler upgrades CAPR to Overweight, $25 target -> INCLUDE (analyst upgrade)\n"
        "[3] CrowdStrike CEO resigns effective immediately -> INCLUDE (management change)\n"
        "[4] Palantir wins $500M Army contract -> INCLUDE (contract win)\n"
        "[5] Carvana Q2 earnings crush estimates, profit triples -> INCLUDE (earnings beat)\n"
        "[6] SEC charges Nikola with fraud -> INCLUDE (regulatory action)\n\n"
        "## EXCLUDE:\n"
        "[10] S&P 500 closes at record high -> EXCLUDE (index, not specific stock)\n"
        "[11] Fed signals rate pause -> EXCLUDE (macro)\n"
        "[12] AAPL stock price history -> EXCLUDE (price page)\n"
        "[13] 5 stocks to buy now -> EXCLUDE (listicle)\n"
        "[14] Why is TSLA falling today -> EXCLUDE (price commentary)\n"
        "[15] Trump tariff fears weigh on markets -> EXCLUDE (political/macro)\n"
        "[16] Gold hits $3000 -> EXCLUDE (commodity)\n"
        "[17] Oil prices surge on Iran tensions -> EXCLUDE (commodity/macro)\n\n"
        "## Rules:\n"
        "- ONLY US-listed companies (NYSE/NASDAQ)\n"
        "- Include if NEW EVENT happened\n"
        "- Exclude price commentary, technical analysis, macro, politics, commodities\n"
        "- When in doubt, INCLUDE\n\n"
        "Output ONLY a JSON array of index numbers.\n\n"
        f"{headlines}"
    )

    try:
        raw = call_gemini(prompt, use_grounding=False)
        if raw:
            clean = raw.replace('```json', '').replace('```', '').strip()
            match = re.search(r'\[[\d\s,\n]+\]', clean, re.DOTALL)
            if match:
                indices = json.loads(match.group())
                filtered = [articles[i] for i in indices if i < len(articles)]
                log.info(f"Headline filter: {len(articles)} -> {len(filtered)} stock-relevant")
                return filtered
        log.warning("Filter failed, using all articles")
    except Exception as e:
        log.warning(f"Filter error: {e}")
    return articles


def build_analysis_prompt(articles, date_str):
    """Fundamentals-enriched analysis prompt for US stocks."""
    prev_dt = datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    headlines = '\n'.join(f'[{i}] [{a["source"]}] {a["title"]}' for i, a in enumerate(articles))

    return (
        f"You are a US stock analyst. {date_str}. NYSE/NASDAQ. Market opens 9:30 AM ET.\n\n"
        f"Below are {len(articles)} stock-related news headlines.\n\n"
        f"For EVERY headline, you MUST:\n"
        f"1. Identify the US stock ticker\n"
        f"2. Read the FULL article via Google Search\n"
        f"3. Look up ALL via Google Search:\n"
        f"   - Market cap\n"
        f"   - Previous close on {prev_date}\n"
        f"   - Whether stock already moved on {prev_date} on this news\n"
        f"   - PE ratio\n"
        f"   - Stock price change in last 1 month\n"
        f"4. Give THREE projections: BULL, BEAR, NEUTRAL\n"
        f"5. Calculate FINAL = (bull + bear + neutral) / 3\n"
        f"6. Decide: TRADE or SKIP\n"
        f"7. Give detailed REASON\n\n"
        f"CRITICAL: Output entry for EVERY stock. Never silently skip.\n\n"
        f"SKIP if ANY true:\n"
        f"- Mega cap >$100B (AAPL/MSFT/GOOGL/AMZN/NVDA/META/TSLA/JPM/V/MA) — too big to move\n"
        f"- Stock already moved >3% on {prev_date} on this news\n"
        f"- Stock up >50% in last 1 month (profit booking risk)\n"
        f"- Micro cap <$50M (too illiquid, unreliable)\n"
        f"- News is routine (earnings in line, dividend, analyst initiation without big target)\n"
        f"- Analyst upgrade/downgrade alone without material catalyst\n\n"
        f"TRADE if:\n"
        f"- |FINAL| >= 3%\n"
        f"- News is material and one-directional\n"
        f"- Market cap $500M - $50B (sweet spot for news-driven moves)\n"
        f"- Stock has NOT already moved on this news\n\n"
        f"Market cap context:\n"
        f"- $500M contract for $50B company -> SKIP (1%)\n"
        f"- $500M contract for $2B company -> TRADE (25% of mcap)\n"
        f"- FDA approval for $1B biotech -> TRADE (binary event)\n"
        f"- CEO exit at $5B company -> TRADE (uncertainty)\n\n"
        f"{headlines}\n\n"
        f"OUTPUT raw JSON array. EVERY stock — TRADE and SKIP:\n"
        f'[{{"symbol":"TICK","decision":"TRADE","bull":"+12%","bear":"+3%","neutral":"+7%","projection":"+7.3%","mcap":"$2B","pe":"25","1m_return":"+8%","why":"FDA approved drug, binary catalyst for $1B biotech"}},'
        f'{{"symbol":"BIG","decision":"SKIP","bull":"+1%","bear":"-0.5%","neutral":"+0.3%","projection":"+0.3%","mcap":"$150B","pe":"30","1m_return":"+2%","why":"Mega cap, analyst upgrade alone insufficient"}}]\n'
        f"No markdown fences. Raw JSON only."
    )


def parse_trades(analysis):
    """Parse Gemini response into trade list."""
    if not analysis:
        return []
    trades = []
    seen = set()
    clean = analysis.replace('```json', '').replace('```', '').strip()
    json_match = re.search(r'\[.*\]', clean, re.DOTALL)
    if json_match:
        try:
            items = json.loads(json_match.group())
            for item in items:
                sym = item.get('symbol', '').strip().upper()
                skip_syms = {'BUY', 'SELL', 'NYSE', 'NASDAQ', 'SEC', 'SKIP', 'SPY', 'QQQ'}
                if sym in skip_syms or sym in seen:
                    continue

                decision = item.get('decision', '').upper()
                if decision == 'SKIP':
                    log.info(f"  SKIP: {sym} | {item.get('why','')[:80]}")
                    continue

                bm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bull', '0')))
                bem = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bear', '0')))
                nm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('neutral', '0')))
                if bm and bem and nm:
                    proj = (float(bm.group(1)) + float(bem.group(1)) + float(nm.group(1))) / 3
                else:
                    continue

                if decision != 'TRADE' and abs(proj) < MIN_PROJECTION:
                    continue

                seen.add(sym)
                call = 'BUY' if proj > 0 else 'SELL'
                trades.append({
                    'symbol': sym, 'call': call, 'projection': round(proj, 1),
                    'mcap': item.get('mcap', '?'), 'why': item.get('why', ''),
                })
                log.info(f"  TRADE: {sym} {call} proj={proj:+.1f}% (B={item.get('bull','?')} R={item.get('bear','?')} N={item.get('neutral','?')}) | {item.get('why','')[:60]}")
        except Exception as e:
            log.error(f"Parse error: {e}")
    return trades


def get_us_price(symbol):
    """Get current price via yfinance."""
    try:
        import yfinance as yf
        import warnings
        warnings.filterwarnings('ignore')
        df = yf.download(symbol, period='1d', interval='1m', progress=False)
        if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
            df.columns = df.columns.get_level_values(0)
        if not df.empty:
            return float(df.iloc[-1]['Close'])
    except Exception as e:
            log.warning(f"Error: {e}")
    return 0


def main():
    scan_only = '--scan-only' in sys.argv
    paper_mode = '--paper' in sys.argv

    if not GEMINI_KEY:
        log.error("Set GEMINI_API_KEY in .env")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y-%m-%d')
    log.info(f"{'='*50}")
    log.info(f"US NEWS BOT V2 — {date_str} {'(SCAN)' if scan_only else '(PAPER)' if paper_mode else '(LIVE)'}")
    log.info(f"{'='*50}")

    # Step 1: Fetch news
    articles, seen_keys = fetch_all_news()
    skip = ['cricket', 'soccer', 'nfl', 'nba', 'mlb', 'celebrity', 'kardashian', 'movie', 'netflix']
    articles = [a for a in articles if not any(s in a['title'].lower() for s in skip)]
    log.info(f"After keyword filter: {len(articles)} articles")

    if len(articles) < 3:
        log.info("Not enough articles.")
        return

    # Step 2: Filter stock headlines
    articles = filter_stock_headlines(articles)

    # Step 3: Analyze in batches with fundamentals
    log.info(f"Analyzing {len(articles)} stock headlines in batches...")
    all_trades = []
    batch_size = 20
    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(articles) + batch_size - 1) // batch_size
        log.info(f"  Batch {batch_num}/{total_batches}: {len(batch)} headlines")
        prompt = build_analysis_prompt(batch, date_str)
        analysis = call_gemini(prompt)
        batch_trades = parse_trades(analysis)
        all_trades.extend(batch_trades)
        time.sleep(2)

    # Dedup
    seen_syms = set()
    trades = []
    for t in all_trades:
        if t['symbol'] not in seen_syms:
            seen_syms.add(t['symbol'])
            trades.append(t)
    log.info(f"Total trades after dedup: {len(trades)}")

    if not trades:
        log.info("No trades found. Sitting out.")
        return

    if scan_only:
        log.info("Scan results:")
        for t in trades:
            log.info(f"  {t['symbol']} {t['call']} {t['projection']:+.1f}% mcap={t.get('mcap','?')} | {t['why'][:80]}")
        # Save JSON for EOD accuracy check
        out = {
            'date': date_str,
            'scan_time': datetime.now().isoformat(),
            'total_articles': len(articles),
            'trades': trades,
        }
        out_path = LOG_DIR / f'us_news_{date_str}.json'
        with open(out_path, 'w') as f:
            json.dump(out, f, indent=2, default=str)
        log.info(f"Saved scan to {out_path}")
        return

    # Wait for US market open (9:30 AM ET = 7:00 PM IST)
    now = datetime.now()
    market_open = now.replace(hour=19, minute=0, second=0)
    if now < market_open:
        wait = (market_open - now).total_seconds()
        log.info(f"Waiting {wait:.0f}s for US market open (7:00 PM IST)...")
        time.sleep(wait)

    # Execute and monitor
    log.info(f"Executing {len(trades)} trades...")
    executed = []

    for t in trades:
        sym = t['symbol']
        call = t['call']
        side = 'BUY' if call == 'BUY' else 'SELL'

        price = get_us_price(sym)
        if price <= 0:
            log.warning(f"{sym}: no price, skipping")
            continue

        qty = 10  # TODO: dynamic sizing based on IBKR capital
        log.info(f"  {side} {qty}x {sym} @ ${price:.2f}")

        if not paper_mode:
            # TODO: IBKR order placement
            log.info(f"  [IBKR ORDER] {side} {qty}x {sym} (not implemented yet)")

        executed.append({
            'symbol': sym, 'call': call, 'entry_price': price, 'qty': qty,
            'projection': t['projection'], 'why': t['why'],
            'entry_time': time.time(),
            'peak_price': price, 'trail_active': False,
        })

    if not executed:
        return

    # Monitor: trailing stop + timeout + 3:50 PM ET exit
    log.info(f"Monitoring {len(executed)} positions...")
    active = list(executed)

    while active:
        now = datetime.now()

        # 12:50 AM IST (3:50 PM ET) hard exit
        if (now.hour == 0 and now.minute >= 50) or (now.hour >= 1 and now.hour < 7):
            log.info("3:50 PM ET — exiting all")
            for e in active:
                current = get_us_price(e['symbol'])
                pnl = ((current - e['entry_price']) / e['entry_price']) * 100 if current > 0 else 0
                log.info(f"  EXIT: {e['symbol']} @ ${current:.2f} ({pnl:+.1f}%)")
            break

        for e in active[:]:
            sym = e['symbol']
            current = get_us_price(sym)
            if not current:
                continue

            entry = e['entry_price']
            elapsed = (time.time() - e['entry_time']) / 60

            if e['call'] == 'BUY':
                move = ((current - entry) / entry) * 100
            else:
                move = ((entry - current) / entry) * 100

            # Update peak
            if e['call'] == 'BUY' and current > e['peak_price']:
                e['peak_price'] = current
            elif e['call'] == 'SELL' and current < e['peak_price']:
                e['peak_price'] = current

            # 30-min timeout: if +1% not hit, exit
            if not e['trail_active'] and elapsed >= TIMEOUT_MIN and move < TRAIL_ACTIVATE:
                log.info(f"  TIMEOUT: {sym} — {TIMEOUT_MIN}min, never hit +{TRAIL_ACTIVATE}%. Exit.")
                if not paper_mode:
                    pass  # TODO: IBKR close
                active.remove(e)
                continue

            # SL check
            if move <= -SL_PCT:
                log.info(f"  SL HIT: {sym} ({move:+.1f}%)")
                active.remove(e)
                continue

            # Trail activation
            if not e['trail_active'] and move >= TRAIL_ACTIVATE:
                e['trail_active'] = True
                log.info(f"  TRAIL ON: {sym} ({move:+.1f}%)")

            # Trail exit
            if e['trail_active']:
                peak = e['peak_price']
                if e['call'] == 'BUY':
                    drop = ((current - peak) / peak) * 100
                else:
                    drop = ((peak - current) / peak) * 100
                if drop <= -TRAIL_PCT:
                    pnl = move
                    log.info(f"  TRAIL EXIT: {sym} ({pnl:+.1f}%)")
                    active.remove(e)

        if active:
            time.sleep(30)

    # Save results
    out_path = LOG_DIR / f'us_news_{date_str}.json'
    with open(out_path, 'w') as f:
        json.dump({'date': date_str, 'trades': [{
            'symbol': e['symbol'], 'call': e['call'], 'projection': e['projection'],
            'entry_price': e['entry_price'],
        } for e in executed]}, f, indent=2, default=str)
    log.info(f"Saved to {out_path}")


if __name__ == '__main__':
    main()
