"""
US Overnight News Trading Bot — buy after-hours, trail next day.

Strategy (81% WR on 3-projection backtest):
  6:00 PM IST — Fetch US stock news (Yahoo, CNBC, MarketWatch, SeekingAlpha, Google News)
  Gemini reads full articles, gives 3 projections (bull/bear/neutral), trades on avg >= 3%
  Buy in IBKR after-hours (4-8 PM ET)
  -5% SL from entry (active overnight via IBKR)
  Trail: +5% activation, 3% from peak
  Hold until trail exit or EOD

Usage:
  python us_overnight_bot.py              # Live mode
  python us_overnight_bot.py --paper      # Paper mode
  python us_overnight_bot.py --scan-only  # Just scan
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

# Config
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_KEY}'
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
SL_PCT = 5.0
TRAIL_ACTIVATE = 5.0
TRAIL_PCT = 3.0

H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# Logging
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'us_bot_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('us_bot')

# RSS feeds
US_RSS_FEEDS = [
    ('https://finance.yahoo.com/rss/topstories', 'Yahoo'),
    ('https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114', 'CNBC'),
    ('https://feeds.marketwatch.com/marketwatch/topstories/', 'MarketWatch'),
    ('https://seekingalpha.com/market_currents.xml', 'SeekingAlpha'),
]


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"[TG] {msg}")
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    except Exception as e:
        log.warning(f'Error: {e}')


def fetch_us_news():
    """Fetch from all US RSS feeds + Google News."""
    articles = []
    seen = set()

    # Live RSS feeds
    for rss_url, source in US_RSS_FEEDS:
        try:
            r = requests.get(rss_url, headers=H, timeout=10)
            if r.status_code != 200: continue
            items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
            for item in items[:30]:
                tm = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
                if not tm: continue
                title = re.sub(r'<!\[CDATA\[|\]\]>', '', tm.group(1)).strip()
                title = re.sub(r'<[^>]+>', '', title).strip()
                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key not in seen and len(title) > 15:
                    seen.add(key)
                    articles.append({'title': title, 'source': source})
        except Exception as e:
            log.warning(f'Error: {e}')

    # Google News for stock-specific
    date_str = datetime.now().strftime('%Y-%m-%d')
    dt = datetime.now()
    after = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
    before = (dt + timedelta(days=1)).strftime('%Y-%m-%d')
    for q in [f'stock FDA clinical trial after:{after} before:{before}',
              f'stock acquisition deal after:{after} before:{before}',
              f'stock regulatory action after:{after} before:{before}']:
        url = f'https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en'
        try:
            r = requests.get(url, headers=H, timeout=10)
            for item in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)[:8]:
                tm = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                if not tm: continue
                title = re.sub(r'<!\[CDATA\[|\]\]>|<[^>]+>', '', tm.group(1)).strip()
                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key not in seen and len(title) > 15:
                    seen.add(key)
                    articles.append({'title': title, 'source': 'Google News'})
        except Exception as e:
            log.warning(f'Error: {e}')

    return articles


def analyze_news(articles, date_str):
    """Gemini reads full articles, 3 projections (bull/bear/neutral)."""
    prev_dt = datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    hl = '\n'.join(f'{i}. [{a["source"]}] {a["title"]}' for i, a in enumerate(articles[:60]))

    prompt = (
        f'You are a US stock trader. {date_str}. NYSE/NASDAQ.\n\n{hl}\n\n'
        f'For EACH stock with news, read the FULL article via Google Search and look up:\n'
        f'- The company market cap\n'
        f'- Previous close on {prev_date}\n'
        f'- Whether the stock already moved on {prev_date}\n\n'
        f'Give THREE projections:\n\n'
        f'BULL: Best case % move. Maximum impact.\n'
        f'BEAR: Worst case. Priced in? Hidden downside?\n'
        f'NEUTRAL: Most likely realistic move considering market cap and news size.\n\n'
        f'FINAL projection = (bull + bear + neutral) / 3.\n\n'
        f'SKIP quarterly earnings/results. SKIP mega caps >$100B.\n\n'
        f'Market cap context:\n'
        f'- $50M deal for $50B company = nothing\n'
        f'- $50M deal for $500M company = 10% of mcap = big\n\n'
        f'Output ONLY raw JSON array (no markdown):\n'
        f'[{{"symbol":"TICK","bull":"+15%","bear":"+3%","neutral":"+8%","projection":"+8.7%","mcap":"500M","why":"reason"}}]\n'
        f'If nothing: []'
    )

    for attempt in range(3):
        try:
            resp = requests.post(GEMINI_URL,
                json={'contents': [{'parts': [{'text': prompt}]}],
                      'tools': [{'google_search': {}}],
                      'generationConfig': {'temperature': 0, 'maxOutputTokens': 4000}},
                timeout=120)
            if resp.status_code == 200:
                text = ''.join(p.get('text', '') for p in resp.json()['candidates'][0]['content']['parts'])
                return text
            elif resp.status_code == 429:
                time.sleep(15 * (attempt + 1))
        except:
            time.sleep(5)
    return None


def parse_trades(analysis):
    if not analysis: return []
    trades = []
    seen = set()
    clean = analysis.replace('```json', '').replace('```', '').strip()
    jm = re.search(r'\[.*\]', clean, re.DOTALL)
    if jm:
        try:
            items = json.loads(jm.group())
            for item in items:
                sym = item.get('symbol', '').strip().upper()
                skip_syms = {'BUY', 'SELL', 'SEC', 'FDA', 'CEO', 'IPO', 'NYSE', 'ETF'}
                if sym in skip_syms or sym in seen: continue

                # Recalculate avg from bull/bear/neutral
                bm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bull', '0')))
                bem = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bear', '0')))
                nm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('neutral', '0')))
                if bm and bem and nm:
                    bull = float(bm.group(1))
                    bear = float(bem.group(1))
                    neutral = float(nm.group(1))
                    proj = (bull + bear + neutral) / 3
                else:
                    proj_m = re.search(r'([+-]?\d+\.?\d*)', str(item.get('projection', '0')))
                    proj = float(proj_m.group(1)) if proj_m else 0

                if abs(proj) < 3: continue
                seen.add(sym)

                # Skip earnings/results
                why = item.get('why', '').lower()
                if any(w in why for w in ['quarterly', 'earnings beat', 'earnings miss', 'eps beat', 'revenue beat', 'q1 ', 'q2 ', 'q3 ', 'q4 ', 'fiscal']):
                    log.info(f"  SKIP {sym}: earnings/results (scheduled event)")
                    continue

                call = 'BUY' if proj > 0 else 'SELL'
                trades.append({
                    'symbol': sym, 'call': call, 'projection': round(proj, 1),
                    'bull': item.get('bull', '?'), 'bear': item.get('bear', '?'),
                    'neutral': item.get('neutral', '?'),
                    'mcap': item.get('mcap', '?'), 'why': item.get('why', ''),
                })
                log.info(f"  TRADE: {sym} {call} proj={proj:+.1f}% (B={item.get('bull','?')} R={item.get('bear','?')} N={item.get('neutral','?')}) | {item.get('why','')[:50]}")
        except Exception as e:
            log.warning(f'Error: {e}')
    return trades



def main():
    scan_only = '--scan-only' in sys.argv
    paper_mode = '--paper' in sys.argv or True

    if not GEMINI_KEY:
        log.error("Set GEMINI_API_KEY")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y-%m-%d')
    log.info(f"{'='*50}")
    log.info(f"US OVERNIGHT BOT — {date_str} {'(SCAN)' if scan_only else '(PAPER)' if paper_mode else '(LIVE)'}")
    log.info(f"{'='*50}")

    # Fetch news
    log.info("Fetching US news...")
    articles = fetch_us_news()
    skip = ['cricket', 'bollywood', 'recipe', 'horoscope']
    articles = [a for a in articles if not any(s in a['title'].lower() for s in skip)]
    log.info(f"Articles: {len(articles)}")

    if len(articles) < 5:
        log.info("Not enough news. Sitting out.")
        send_telegram(f"US Bot {date_str}: Not enough news.")
        return

    # Analyze
    log.info("Gemini analyzing (reading full articles)...")
    analysis = analyze_news(articles, date_str)
    trades = parse_trades(analysis)

    if not trades:
        log.info("No trades found.")
        send_telegram(f"US Bot {date_str}: No trades.")
        return

    for t in trades:
        log.info(f"  TRADE: {t['symbol']} {t['call']} proj={t['projection']:+.1f}% | {t.get('category', 'NEWS')}")
        log.info(f"    Why: {t['why'][:80]}")

    if scan_only:
        msg = f"US Scan {date_str}:\n"
        for t in trades:
            msg += f"  {t['symbol']} {t['call']} {t['projection']:+.1f}% ({t.get('category', 'NEWS')})\n"
        send_telegram(msg)
        return

    # Execute (paper mode)
    for t in trades:
        sym = t['symbol']
        call = t['call']
        log.info(f"[PAPER] {call} {sym} | SL: -{SL_PCT}% | Trail: +{TRAIL_ACTIVATE}%")
        send_telegram(
            f"{'🟢' if call == 'BUY' else '🔴'} US {call}: {sym}\n"
            f"Proj: {t['projection']:+.1f}% | {t.get('category', 'NEWS')}\n"
            f"SL: -{SL_PCT}% | Hold overnight | Sell at open"
        )

    # Save
    out = {'date': date_str, 'trades': trades}
    with open(LOG_DIR / f'us_{date_str}.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    log.info(f"Saved to us_{date_str}.json")


if __name__ == '__main__':
    main()
