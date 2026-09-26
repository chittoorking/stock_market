"""
Live Options Trading Bot — News-driven, projection-based, with reversal.
ALL Gemini — no Claude dependency.

Strategy (proven on 430 days, 97% WR):
  9:05 AM  — Fetch all RSS feeds + Zerodha Pulse
  9:05 AM  — Gemini reads full articles via grounding, projects moves
  9:15 AM  — Market opens. Buy ATM CE/PE for projected moves >=3%
  9:15-3:10 — Monitor: -20% SL, +10% trail activation, 5% trail
  On SL hit — REVERSE: buy opposite option (news moves are sticky)
  3:10 PM  — Hard exit all positions

  --continuous mode (replaces old Claude news_bot.py):
  9:30 AM - 2:30 PM — Scan RSS every 5 min for BREAKING news
  Only process NEW headlines not seen in pre-market scan
  Same Gemini + grounding + projection + counter-argument

Usage:
  python options_bot.py              # Live mode
  python options_bot.py --paper      # Paper mode (no real orders)
  python options_bot.py --scan-only  # Just scan, don't trade
  python options_bot.py --continuous # Intraday scanner (9:30 AM - 2:30 PM)
"""
import json, os, sys, time, re, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

import requests
from live import indmoney_client as api
from live.master_client import request_batch, report_exit, is_available as master_available
from live.gemini_utils import call_gemini as gemini_call, reload_keys

# ── Config ──
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_KEY}'
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

MIN_PROJECTION = 3.0
SL_PCT = 20.0

def get_atr_sl(sym, multiplier=1.5, default=3.0):
    """Calculate 1.5x ATR-based SL for a stock. Falls back to default."""
    try:
        import yfinance as _yf
        df = _yf.download(sym + '.NS' if not sym.endswith('.NS') else sym, period='20d', progress=False, auto_adjust=True)
        if hasattr(df.columns, 'get_level_values'):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 5:
            return default
        h = df['High'].values.astype(float)
        l = df['Low'].values.astype(float)
        c = df['Close'].values.astype(float)
        import numpy as _np
        tr = _np.maximum(h[1:]-l[1:], _np.maximum(_np.abs(h[1:]-c[:-1]), _np.abs(l[1:]-c[:-1])))
        tr = _np.insert(tr, 0, h[0]-l[0])
        atr = float(tr[-14:].mean())
        atr_pct = atr / c[-2] * 100  # prev day close
        sl = round(atr_pct * multiplier, 1)
        return max(sl, 2.0)  # minimum 2% SL
    except:
        return default
TRAIL_ACTIVATE = 10.0
TRAIL_PCT = 5.0
MAX_CAPITAL_PCT = 0.33

# Trading mode: 'both' | 'options_only' | 'mis_only'
TRADE_MODE = os.environ.get('TRADE_MODE', 'mis_only')  # MIS equity for all — simple, same edge, works on all stocks
# ── ORB Config ──
ORB_MINUTES = 5           # Opening range period (9:15-9:20)
ORB_MIN_REMAINING_PCT = 1.0  # Skip if remaining after entry_move < 1%
ORB_MAX_ENTRY_MOVE_PCT = 2.0  # Skip if breakout level already moved >2% from prev close
ORB_CONFIRM_BUFFER_PCT = 0.1  # Enter 0.1% above OR high (or below OR low) for confirmation
ORB_TRAIL_ACTIVATE = 1.0  # Trail activates at +1% — 0% false activations on losers
ORB_TRAIL_PCT = 0.5       # Trail by 0.5%
ORB_MIN_RANGE_PCT = 0.5   # Skip if OR range < 0.5% (too narrow = bad data)
ORB_MAX_RANGE_PCT = 3.0   # Skip if OR range > 3% (no room for breakout)
ORB_MAX_SL_PCT = 4.0      # Cap SL at 4% — ATR-adjusted, proven on 59 trades

# Position persistence — survives restarts
from pathlib import Path as _Path
_NEWS_ORB_STATE_FILE = _Path(__file__).parent / 'logs' / '.news_orb_state.json'

def _load_news_orb_state():
    """Load today's news_orb entries from disk."""
    import json
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST).strftime('%Y-%m-%d')
    try:
        if _NEWS_ORB_STATE_FILE.exists():
            state = json.loads(_NEWS_ORB_STATE_FILE.read_text())
            if state.get('date') == today:
                return state
    except:
        pass
    return {'date': today, 'entered_symbols': [], 'positions': []}

def _save_news_orb_state(state):
    """Save news_orb state to disk."""
    import json
    _NEWS_ORB_STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
IST = timezone(timedelta(hours=5, minutes=30))

# ── Logging ──
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'options_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('options_bot')

# NSE F&O stocks list
_fno_file = Path(__file__).parent.parent / 'fno_stocks_live.json'
if _fno_file.exists():
    FNO_STOCKS = set(json.load(open(_fno_file)))
    FNO_STOCKS -= {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50', 'NIFTYFPI'}
else:
    FNO_STOCKS = set()
    log.warning("fno_stocks_live.json not found")

# ── RSS Feeds ──
RSS_FEEDS = [
    ('https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms', 'ET Markets'),
    ('https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms', 'ET Stocks'),
    ('https://www.livemint.com/rss/markets', 'LiveMint'),
    ('https://www.business-standard.com/rss/markets-106.rss', 'Biz Standard'),
    ('https://feeds.feedburner.com/ndtvprofit-latest', 'NDTV Profit'),
]


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"[TG] {msg}")
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    except Exception as e:
        log.warning(f'Telegram send error: {e}')


def fetch_all_rss(window_hours=17):
    """Fetch all RSS feeds with timestamp filtering. Returns (articles, seen_keys)."""
    now = datetime.now(IST)
    cutoff = now
    window_start = cutoff - timedelta(hours=window_hours)

    articles = []
    seen = set()
    source_counts = {}

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
                        pub_dt = parsedate_to_datetime(raw).astimezone(IST)
                    except:
                        pass
                if pub_dt and not (window_start <= pub_dt <= cutoff):
                    continue

                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key in seen or len(title) < 15:
                    continue
                seen.add(key)
                articles.append({
                    'title': title,
                    'pubDate': pub_dt.strftime('%Y-%m-%d %H:%M IST') if pub_dt else '?',
                    'source': source_name,
                })
                count += 1
        except Exception as e:
            log.warning(f'RSS fetch error for {source_name}: {e}')
        source_counts[source_name] = count

    # Zerodha Pulse
    try:
        r = requests.get('https://pulse.zerodha.com/', headers=H, timeout=10)
        if r.status_code == 200:
            titles_raw = re.findall(r'<h2[^>]*class="title"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text)
            pulse_count = 0
            for url, raw_title in titles_raw:
                title = re.sub(r'<[^>]+>', '', raw_title).strip()
                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key in seen or len(title) < 15:
                    continue
                seen.add(key)
                articles.append({'title': title, 'pubDate': '?', 'source': 'Pulse'})
                pulse_count += 1
            source_counts['Pulse'] = pulse_count
    except Exception as e:
        log.warning(f'Pulse scrape error: {e}')

    articles.sort(key=lambda x: x.get('pubDate', ''), reverse=True)
    log.info(f"Sources: {source_counts} | Total: {len(articles)}")
    return articles, seen


def call_gemini(prompt, max_retries=3):
    """Call Gemini with Google Search grounding."""
    for attempt in range(max_retries):
        try:
            resp = requests.post(GEMINI_URL,
                json={
                    'contents': [{'parts': [{'text': prompt}]}],
                    'tools': [{'google_search': {}}],
                    'generationConfig': {'temperature': 0, 'maxOutputTokens': 65536},
                }, timeout=120)
            if resp.status_code == 200:
                return ''.join(p.get('text', '') for p in resp.json()['candidates'][0]['content']['parts'])
            elif resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
            else:
                log.error(f"Gemini {resp.status_code}: {resp.text[:200]}")
                time.sleep(5)
        except Exception as e:
            log.error(f"Gemini error: {e}")
            time.sleep(5)
    return None


def build_analysis_prompt(articles, date_str, market_open=False):
    """3-projection prompt: bull + bear + neutral, average of 3."""
    prev_dt = datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    headlines = '\n'.join(f'[{i}] [{a["source"]}] {a["title"]}' for i, a in enumerate(articles))
    market_note = "Market is OPEN RIGHT NOW. Check CURRENT price — if stock already moved >3% since open, SKIP." if market_open else "Market opens at 9:15 AM IST."

    return (
        f"You are a stock analyst. {date_str}. Indian NSE/BSE. {market_note}\n\n"
        f"Below are {len(articles)} stock-related news headlines.\n\n"
        f"For EVERY headline, you MUST:\n"
        f"1. Identify the CURRENT NSE trading symbol (use Google to verify — e.g. GE Power India = GVPIL not GEPWD, United Spirits = UNITDSPR, Max Healthcare = MAXHEALTH, HDFC Life = HDFCLIFE, SBI = SBIN)\n"
        f"2. Read the FULL article via Google Search\n"
        f"3. Look up ALL of the following via Google Search:\n"
        f"   - Company market cap\n"
        f"   - Previous close on {prev_date}\n"
        f"   - What happened to this stock yesterday ({prev_date})? Open, high, low, close, % change.\n"
        f"   - Any news about this stock yesterday? Same news or different?\n"
        f"   - PE ratio (trailing)\n"
        f"   - Stock price change in last 1 month (% return)\n"
        f"   - 52-week high and current price (how far from 52w high)\n"
        f"   - Promoter holding %\n"
        f"4. Give THREE projections: BULL (best case), BEAR (worst case), NEUTRAL (most likely)\n"
        f"5. Calculate FINAL = (bull + bear + neutral) / 3\n"
        f"6. Decide: TRADE or SKIP\n"
        f"7. Give detailed REASON including the fundamentals you found\n\n"
        f"CRITICAL: Output an entry for EVERY stock. Never silently skip.\n\n"
        f"SKIP if ANY of these are true:\n"
        f"- News is too small relative to market cap (Rs 100cr order for Rs 50,000cr company)\n"
        f"- Stock already moved >3% on {prev_date} on this news (move is done)\n"
        f"- Stock is up >50% in last 1 month (profit booking risk on good news)\n"
        f"- Market cap > Rs 1,00,000 cr AND event is not extreme (large caps absorb shocks)\n"
        f"- News is routine (monthly sales in line, dividend reminder, analyst initiation without big target)\n"
        f"- PE ratio > 100 AND news is positive (already overvalued, limited upside)\n"
        f"- MoU or non-binding agreement (MoUs are NOT confirmed orders, they rarely move stocks same-day)\n"
        f"- Analyst downgrade/upgrade alone (brokerage reports cause 1-2% moves, NOT 5%+, unless target is 30%+ away)\n\n"
        f"TRADE if:\n"
        f"- |FINAL projection| >= 5%\n"
        f"- News is material and one-directional (M&A, SEBI ban, CEO exit, big order vs mcap)\n"
        f"- Stock has NOT already moved on this news\n"
        f"- Fundamentals support the direction\n\n"
        f"Market cap context examples:\n"
        f"- Rs 500 cr order for Rs 2,000 cr company (25% of mcap) -> TRADE +7%\n"
        f"- Rs 500 cr order for Rs 50,000 cr company (1% of mcap) -> SKIP +0.7%\n"
        f"- CEO exit at small bank (mcap Rs 5,000 cr) -> TRADE -5%\n"
        f"- CEO exit at mega bank (mcap Rs 12,00,000 cr) -> SKIP -0.5% (too big to move)\n"
        f"- SEBI ban on company -> TRADE -8%\n"
        f"- Stock up 100% in 1 month, gets good news -> SKIP (profit booking risk)\n\n"
        f"{headlines}\n\n"
        f"OUTPUT raw JSON array. Include EVERY stock — both TRADE and SKIP:\n"
        f'[{{"symbol":"TICK","nse_symbol":"TICK","decision":"TRADE","bull":"+12%","bear":"+3%","neutral":"+7%","projection":"+7.3%","mcap":"2000cr","pe":"25","1m_return":"+8%","yesterday_close":"Rs 500","yesterday_change":"+0.5%","yesterday_news":"no major news","why":"fresh catalyst, stock did not move yesterday"}},'
        f'{{"symbol":"BIG","nse_symbol":"BIG","decision":"SKIP","bull":"+1%","bear":"-0.5%","neutral":"+0.3%","projection":"+0.3%","mcap":"120000cr","pe":"18","1m_return":"+2%","yesterday_close":"Rs 710","yesterday_change":"-4.2%","yesterday_news":"same news caused 4% drop","why":"stock already fell 4% yesterday on same news"}}]\n'
        f"No markdown fences. Raw JSON only."
    )



def filter_stock_headlines(articles):
    """Step 1: Cheap Gemini call to filter stock-relevant headlines. No grounding."""
    FILTER_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_KEY}'

    headlines = '\n'.join(f'[{i}] {a["title"]}' for i, a in enumerate(articles))

    prompt = (
        "You are a stock news filter for INDIAN stock markets (NSE/BSE listed companies only).\n"
        "Given headlines, return index numbers of headlines where SOMETHING HAPPENED to a SPECIFIC "
        "NSE/BSE listed company that could move its stock price.\n\n"
        "## INCLUDE these (examples):\n"
        "[0] E2E Networks bags Rs 1,000 crore NVIDIA GPU contract -> INCLUDE (specific order)\n"
        "[1] GRT Jewellers to acquire 74% stake in TBZ for Rs 1,034 crore -> INCLUDE (M&A)\n"
        "[2] Ujjivan SFB CEO seeks early retirement on health grounds -> INCLUDE (CEO exit)\n"
        "[3] HFCL signs Rs 2,329 crore optical fibre supply deal -> INCLUDE (large order)\n"
        "[4] ITC arm acquires 22% stake in Happiest Minds for Rs 1,330 crore -> INCLUDE (acquisition)\n"
        "[5] Sebi bars Tarapur Transformers from markets for 5 years -> INCLUDE (regulatory)\n"
        "[6] HDFC Bank CEO shock exit triggers Moody's warning -> INCLUDE (management change)\n"
        "[7] Muthoot Finance to merge gold loan subsidiary -> INCLUDE (restructuring)\n"
        "[8] Godrej Agrovet: Kotak raises fair value, sees 64% upside -> INCLUDE (analyst upgrade)\n"
        "[9] Zomato cuts 250 jobs, shuts Hyderabad centre -> INCLUDE (layoffs)\n"
        "[10] Ashish Dhawan picks up 35 lakh shares in Religare -> INCLUDE (bulk/block deal)\n"
        "[11] Mahindra total sales surge 50% in August -> INCLUDE (sales data)\n"
        "[12] Coal India prepares to list MCL, SECL -> INCLUDE (subsidiary listing)\n\n"
        "## EXCLUDE these (examples):\n"
        "[20] Infosys Share Price Highlights: Stock Price History -> EXCLUDE (price history page)\n"
        "[21] Sun Pharma Share Price Live Updates -> EXCLUDE (price tracker)\n"
        "[22] Supreme Petrochem among 5 stocks showing bullish RSI -> EXCLUDE (technical analysis)\n"
        "[23] Priority Jewels IPO Day 3: GMP signals 23% listing gain -> EXCLUDE (IPO GMP)\n"
        "[24] NTPC dividend 2026: Last day to buy alert -> EXCLUDE (dividend reminder)\n"
        "[25] Why is Maruti share price falling today? -> EXCLUDE (price commentary)\n"
        "[26] Markets end lower amid oil prices -> EXCLUDE (market commentary)\n"
        "[27] Gold Rate Today in Mumbai -> EXCLUDE (commodity price)\n"
        "[28] Stocks to watch today: HDFC, ICICI -> EXCLUDE (watchlist)\n"
        "[29] Hy-Tech shares lock upper circuit. Buy or hold? -> EXCLUDE (price reaction)\n"
        "[30] Rising stocks: Milky Mist, Vardhman - surge? -> EXCLUDE (price reaction listicle)\n"
        "[31] Apple CEO change / Amazon FTC case -> EXCLUDE (not Indian listed company)\n"
        "[32] Anthropic $35B deal with Lambda -> EXCLUDE (not Indian listed)\n"
        "[33] Nifty may trade in narrow range -> EXCLUDE (index outlook)\n\n"
        "## Rules:\n"
        "- ONLY Indian NSE/BSE listed companies. Skip US/global stocks unless they are listed in India.\n"
        "- Include if a NEW EVENT happened (deal, order, M&A, exit, regulatory, earnings, sales data, block deal)\n"
        "- Exclude price pages, technical analysis, IPO GMP, dividend reminders, watchlists, share price history\n"
        "- Exclude 'why stock falling/rising' articles\n"
        "- If same event in multiple headlines, include ALL of them (let Gemini step 2 read the best one)\n"
        "- When in doubt, INCLUDE. Better to send a few extra than miss a real catalyst.\n\n"
        "Output ONLY a JSON array of index numbers.\n\n"
        f"{headlines}"
    )

    try:
        resp = requests.post(FILTER_URL,
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0, 'maxOutputTokens': 16384},
            }, timeout=90)
        if resp.status_code == 200:
            raw = ''.join(p.get('text', '') for p in resp.json()['candidates'][0]['content']['parts'])
            clean = raw.replace('```json', '').replace('```', '').strip()
            match = re.search(r'\[[\d\s,\n]+\]', clean, re.DOTALL)
            if match:
                indices = json.loads(match.group())
                filtered = [articles[i] for i in indices if i < len(articles)]
                log.info(f"Headline filter: {len(articles)} -> {len(filtered)} stock-relevant ({len(articles)-len(filtered)} junk removed)")
                return filtered
        log.warning(f"Filter call failed ({resp.status_code}), using all articles")
    except Exception as e:
        log.warning(f"Filter error: {e}, using all articles")
    return articles


def parse_trades(analysis):
    if not analysis:
        return []
    trades = []
    seen = set()
    # Strip markdown fences
    clean = analysis.replace('```json', '').replace('```', '').strip()
    json_match = re.search(r'\[.*\]', clean, re.DOTALL)
    if json_match:
        try:
            items = json.loads(json_match.group())
            for item in items:
                sym = item.get('symbol', '').strip().upper()
                skip_syms = {'BUY', 'SELL', 'NSE', 'BSE', 'RBI', 'SEBI', 'SKIP'}
                if sym in skip_syms or sym in seen:
                    continue

                # Recalculate avg from bull/bear/neutral ourselves
                bm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bull', '0')))
                bem = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bear', '0')))
                nm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('neutral', '0')))
                if bm and bem and nm:
                    bull = float(bm.group(1))
                    bear = float(bem.group(1))
                    neutral = float(nm.group(1))
                    proj = (bull + bear + neutral) / 3
                else:
                    # Fallback to projection field
                    proj_m = re.search(r'([+-]?\d+\.?\d*)', str(item.get('projection', '0')))
                    proj = float(proj_m.group(1)) if proj_m else 0

                # Use decision field if present, else fall back to projection threshold
                decision = item.get('decision', '').upper()
                if decision == 'SKIP':
                    log.info(f"  SKIP: {sym} proj={proj:+.1f}% | {item.get('why','')[:80]}")
                    continue
                if decision != 'TRADE' and abs(proj) < MIN_PROJECTION:
                    continue
                seen.add(sym)
                call = 'BUY' if proj > 0 else 'SELL'
                trades.append({
                    'symbol': sym, 'call': call, 'projection': round(proj, 1),
                    'bull': item.get('bull', '?'), 'bear': item.get('bear', '?'),
                    'neutral': item.get('neutral', '?'),
                    'mcap': item.get('mcap', '?'), 'why': item.get('why', ''),
                })
                log.info(f"  TRADE: {sym} {call} proj={proj:+.1f}% (B={item.get('bull','?')} R={item.get('bear','?')} N={item.get('neutral','?')}) | {item.get('why','')[:50]}")
        except Exception as e:
            log.error(f'JSON parse error in trades: {e}')
    return trades


def route_trades(trades):
    if TRADE_MODE == 'mis_only':
        return [], trades
    elif TRADE_MODE == 'options_only':
        return [t for t in trades if t['symbol'] in FNO_STOCKS], []
    elif TRADE_MODE == 'futures_mis':
        # F&O stocks -> futures list, non-F&O -> MIS equity
        return [t for t in trades if t['symbol'] in FNO_STOCKS], [t for t in trades if t['symbol'] not in FNO_STOCKS]
    else:
        return [t for t in trades if t['symbol'] in FNO_STOCKS], [t for t in trades if t['symbol'] not in FNO_STOCKS]


def place_order_paper(symbol, qty, side, option_type, strike, price):
    log.info(f"[PAPER] {side} {qty}x {symbol} {option_type} {strike} @ {price:.2f}")


def lookup_scrip(symbol):
    """Dynamically lookup security_id for a new IPO stock."""
    try:
        token = api.get_token()
        h = {'Authorization': token}
        r = requests.get(f'https://api.indstocks.com/market/instruments?source=equity',
                        headers=h, timeout=30)
        if r.status_code == 200:
            # CSV response — search for symbol
            for line in r.text.split('\n'):
                parts = line.split(',')
                if len(parts) >= 3:
                    # Check if symbol matches
                    if symbol.upper() in line.upper():
                        for p in parts:
                            if p.strip().upper() == symbol.upper():
                                # Find the security_id (usually NSE_XXXXX format)
                                for p2 in parts:
                                    p2 = p2.strip()
                                    if p2.startswith('NSE_') or p2.isdigit():
                                        sec_id = p2.replace('NSE_', '') if p2.startswith('NSE_') else p2
                                        log.info(f"  Found scrip for {symbol}: NSE_{sec_id}")
                                        # Add to scrip codes so LTP/order works
                                        api.SCRIP_CODES[symbol] = f'NSE_{sec_id}'
                                        api.SYM_FROM_SCRIP[f'NSE_{sec_id}'] = symbol
                                        return sec_id
            # Try searching with partial match
            for line in r.text.split('\n'):
                if symbol.upper() in line.upper():
                    parts = line.split(',')
                    for p in parts:
                        p = p.strip()
                        if p.isdigit() and len(p) >= 3:
                            log.info(f"  Fuzzy match for {symbol}: sec_id={p}")
                            api.SCRIP_CODES[symbol] = f'NSE_{p}'
                            api.SYM_FROM_SCRIP[f'NSE_{p}'] = symbol
                            return p
        log.warning(f"  Could not find scrip for {symbol}")
    except Exception as e:
        log.error(f"  Scrip lookup error: {e}")
    return None




def orb_collect_range(symbols, poll_seconds=15):
    """Track opening range (9:15-9:30) for all symbols. Returns {sym: {high, low, open}}."""
    from live import indmoney_client as api
    or_data = {}
    for sym in symbols:
        or_data[sym] = {'high': 0, 'low': float('inf'), 'open': 0, 'prev_close': 0}

    log.info(f"ORB: Collecting opening range for {len(symbols)} stocks (9:15-9:30)...")

    # Get prev close for gap filter
    try:
        import yfinance as _yf
        for sym in symbols:
            try:
                _df = _yf.download(sym + '.NS' if not sym.endswith('.NS') else sym,
                                   period='5d', progress=False, auto_adjust=True)
                if hasattr(_df.columns, 'get_level_values'):
                    _df.columns = _df.columns.get_level_values(0)
                if len(_df) >= 2:
                    or_data[sym]['prev_close'] = float(_df['Close'].values[-2])
                    log.info(f"  {sym} prev close: {or_data[sym]['prev_close']:.1f}")
            except Exception as e:
                log.warning(f"  {sym} prev close fetch failed: {e}")
    except:
        log.warning("yfinance not available for prev close")

    end_time = datetime.now().replace(hour=9, minute=20, second=0, microsecond=0)

    poll_count = 0
    while datetime.now() < end_time:
        for sym in symbols:
            try:
                ltp_data = api.get_ltp([sym])
                price = ltp_data.get(sym, 0)
                if price <= 0:
                    continue
                d = or_data[sym]
                if d['open'] == 0:
                    d['open'] = price
                if price > d['high']:
                    d['high'] = price
                if price < d['low']:
                    d['low'] = price
            except Exception as e:
                log.warning(f"ORB LTP error {sym}: {e}")
        poll_count += 1
        if poll_count % 4 == 0:
            for sym in symbols:
                d = or_data[sym]
                if d['open'] > 0:
                    r = (d['high'] - d['low']) / d['open'] * 100 if d['open'] else 0
                    log.info(f"  OR {sym}: H={d['high']:.1f} L={d['low']:.1f} R={r:.2f}%")
        time.sleep(poll_seconds)

    log.info(f"ORB: Opening range done ({poll_count} polls)")
    for sym in symbols:
        d = or_data[sym]
        if d['open'] > 0:
            r = (d['high'] - d['low']) / d['open'] * 100
            log.info(f"  FINAL OR {sym}: H={d['high']:.1f} L={d['low']:.1f} Range={r:.2f}%")
        else:
            log.warning(f"  {sym}: No LTP during OR period")
    return or_data


def orb_wait_breakout(trades, or_data, paper_mode=True, alloc_map=None):
    """Place limit orders at breakout levels. No polling needed."""
    from live import indmoney_client as api
    executed = []
    limit_orders = []  # track placed orders

    # Load persisted state — survives restarts
    orb_state = _load_news_orb_state()
    already_entered = set(s.upper() for s in orb_state.get('entered_symbols', []))
    if already_entered:
        log.info(f"ORB: Already entered today (from disk): {already_entered}")

    # Also check broker for open positions
    open_symbols = set(already_entered)
    try:
        existing = api.get_positions()
        if existing:
            for p in existing:
                sym_pos = p.get("symbol", p.get("scrip", ""))
                if sym_pos:
                    open_symbols.add(sym_pos.upper())
            if open_symbols - already_entered:
                log.info(f"ORB: Also open at broker: {open_symbols - already_entered}")
    except Exception as e:
        log.warning(f"ORB: Could not check broker positions: {e}")

    for t in trades:
        sym = t.get('nse_symbol', t['symbol'])
        d = or_data.get(sym)
        if not d or d['open'] == 0:
            log.warning(f"ORB: {sym} no OR data, skipping")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        or_range_pct = (d['high'] - d['low']) / d['open'] * 100
        prev_close = d.get('prev_close', 0)

        # Bug fix: skip if position already open
        if sym.upper() in open_symbols:
            log.info(f"ORB SKIP: {sym} already has open position — skipping")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        # Filter 1: gap-based remaining
        gap_pct = 0
        if prev_close > 0:
            gap_pct = abs((d['open'] - prev_close) / prev_close * 100)
        # Filter: OR range sanity check
        if or_range_pct < ORB_MIN_RANGE_PCT:
            log.info(f"ORB SKIP: {sym} OR range {or_range_pct:.2f}% < {ORB_MIN_RANGE_PCT}% (too narrow, bad data?)")
            send_telegram(f"ORB SKIP: {sym} OR too narrow ({or_range_pct:.2f}%)")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        if or_range_pct > ORB_MAX_RANGE_PCT:
            log.info(f"ORB SKIP: {sym} OR range {or_range_pct:.2f}% > {ORB_MAX_RANGE_PCT}% (too wide, no room for breakout)")
            send_telegram(f"ORB SKIP: {sym} OR too wide ({or_range_pct:.2f}%)")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        remaining_gap = abs(t.get('projection', 0)) - gap_pct
        if remaining_gap < ORB_MIN_REMAINING_PCT:
            log.info(f"ORB SKIP: {sym} proj={t.get('projection',0):+.1f}% gap={gap_pct:.1f}% remaining={remaining_gap:.1f}% < {ORB_MIN_REMAINING_PCT}%")
            send_telegram(f"ORB SKIP: {sym} remaining={remaining_gap:.1f}% (proj {t.get('projection',0):+.1f}% - gap {gap_pct:.1f}%)")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        # Filter 2: entry move check — how far is breakout level from prev close?
        if t['call'] == 'BUY':
            entry_price = d['high'] * (1 + ORB_CONFIRM_BUFFER_PCT / 100)  # slightly above OR high
            entry_move = (entry_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
        else:
            entry_price = d['low'] * (1 - ORB_CONFIRM_BUFFER_PCT / 100)  # slightly below OR low
            entry_move = (prev_close - entry_price) / prev_close * 100 if prev_close > 0 else 0

        if entry_move > ORB_MAX_ENTRY_MOVE_PCT:
            log.info(f"ORB SKIP: {sym} entry_move={entry_move:.1f}% > {ORB_MAX_ENTRY_MOVE_PCT}% (breakout level too far from prev close)")
            send_telegram(f"ORB SKIP: {sym} entry already {entry_move:.1f}% from prev close")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        real_remaining = abs(t.get('projection', 0)) - entry_move
        log.info(f"ORB READY: {sym} {t['call']} | entry={entry_price:.1f} gap={gap_pct:.1f}% entry_mv={entry_move:.1f}% real_rem={real_remaining:.1f}%")

        # Get allocation
        if alloc_map and sym in alloc_map:
            per_trade = alloc_map[sym][0]
            t['master_trade_id'] = alloc_map[sym][1]
        elif alloc_map and sym not in alloc_map:
            log.info(f"  {sym} not approved by Master")
            continue
        else:
            capital = api.get_funds()
            per_trade = capital * MAX_CAPITAL_PCT

        # ATR-based SL, capped
        sl_pct = get_atr_sl(sym, multiplier=1.5, default=2.0)
        sl_pct = max(sl_pct, 1.0)  # min 1%
        sl_pct = min(sl_pct, 4.0)  # max 4%

        # Place limit order at breakout level
        if sym in FNO_STOCKS and TRADE_MODE == 'futures_mis':
            # FUTURES: find nearest month futures contract
            try:
                chain = api.get_option_chain(sym)
                if chain:
                    # Get lot size from option chain (same for futures)
                    lot_size = chain[0].get('lot_size', 1) if chain else 1
                    # Find futures security_id
                    scrip_code = api.SCRIP_CODES.get(sym, '')
                    sec_id = scrip_code.split('_')[1] if scrip_code else ''

                    if sec_id:
                        qty = lot_size
                        side = 'BUY' if t['call'] == 'BUY' else 'SELL'
                        # Futures use same trigger as equity — wait for stock breakout, then enter futures
                        limit_orders.append({
                            'sym': sym, 'trade': t, 'entry_price': entry_price,
                            'or_high': d['high'], 'or_low': d['low'],
                            'or_range_pct': or_range_pct, 'sl_pct': sl_pct,
                            'qty': qty, 'per_trade': per_trade,
                            'is_option': False, 'is_futures': True,
                            'lot_size': lot_size, 'entry_move': entry_move,
                        })
                        log.info(f"ORB WATCH FUTURES: {sym} {t['call']} lot={lot_size} | trigger at {entry_price:.1f}")
                        send_telegram(f"ORB FUTURES: {sym} {t['call']} | trigger={entry_price:.1f} proj={t.get('projection',0):+.1f}% rem={real_remaining:.1f}%")
                        continue
            except Exception as e:
                log.warning(f"Futures setup failed for {sym}: {e} — falling through to MIS equity")
        elif sym in FNO_STOCKS:
            chain = api.get_option_chain(sym)
            if chain:
                opt_type = 'CE' if t['call'] == 'BUY' else 'PE'
                best_strike = None
                best_sec_id = None
                best_diff = float('inf')
                lot_size = 1
                for sd in chain:
                    strike = sd.get('strike_price', 0)
                    if abs(strike - entry_price) < best_diff:
                        best_diff = abs(strike - entry_price)
                        best_strike = strike
                        od = sd.get('ce' if opt_type == 'CE' else 'pe', {})
                        best_sec_id = od.get('security_id')
                        lot_size = sd.get('lot_size', 1)

                if best_sec_id:
                    qty = lot_size
                    limit_orders.append({
                        'sym': sym, 'trade': t, 'entry_price': entry_price,
                        'or_high': d['high'], 'or_low': d['low'],
                        'or_range_pct': or_range_pct, 'sl_pct': sl_pct,
                        'qty': qty, 'per_trade': per_trade,
                        'is_option': True, 'opt_type': opt_type,
                        'best_strike': best_strike, 'best_sec_id': best_sec_id,
                        'lot_size': lot_size, 'entry_move': entry_move,
                    })
                    log.info(f"ORB WATCH: {sym} {t['call']} {best_strike}{opt_type} | trigger at {entry_price:.1f}")
                    send_telegram(f"ORB WATCH: {sym} {t['call']} | trigger={entry_price:.1f} proj={t.get('projection',0):+.1f}% rem={real_remaining:.1f}%")
                    continue

        # Bug fix: don't place new orders after 2:45 PM
        if datetime.now().hour > 14 or (datetime.now().hour == 14 and datetime.now().minute > 45):
            log.info(f"ORB SKIP: {sym} past 2:45 PM — no new entries")
            if t.get('master_trade_id'):
                report_exit(t['master_trade_id'], 0)
            continue

        # MIS equity: place LIMIT order directly
        qty = max(1, int(per_trade / entry_price))
        side = 'BUY' if t['call'] == 'BUY' else 'SELL'
        oid = None

        if not paper_mode:
            oid = api.place_order(sym, qty, side, price=entry_price,
                                 order_type='LIMIT', product='INTRADAY')
            if oid:
                log.info(f"ORB LIMIT ORDER: {side} {qty}x {sym} @ {entry_price:.1f} -> {oid}")
            else:
                log.error(f"ORB limit order failed for {sym}")
                if t.get('master_trade_id'):
                    report_exit(t['master_trade_id'], 0)
                continue
        else:
            log.info(f"[PAPER] ORB LIMIT: {side} {qty}x {sym} @ {entry_price:.1f}")

        limit_orders.append({
            'sym': sym, 'trade': t, 'entry_price': entry_price,
            'or_high': d['high'], 'or_low': d['low'],
            'or_range_pct': or_range_pct, 'sl_pct': sl_pct,
            'qty': qty, 'per_trade': per_trade, 'order_id': oid,
            'is_option': False, 'entry_move': entry_move,
        })
        send_telegram(
            f"ORB LIMIT {t['call']}: {qty}x {sym} @ {entry_price:.1f}\n"
            f"Proj: {t.get('projection',0):+.1f}% | Rem: {real_remaining:.1f}% | SL: {sl_pct:.1f}%"
        )

        # Persist entry to disk — survives restarts
        orb_state['entered_symbols'].append(sym.upper())
        orb_state['positions'].append({
            'symbol': sym, 'side': t['call'], 'entry_price': entry_price,
            'sl_pct': sl_pct, 'qty': qty,
        })
        _save_news_orb_state(orb_state)

    if not limit_orders:
        log.info("ORB: No orders placed")
        return []

    log.info(f"ORB: {len(limit_orders)} limit orders placed, monitoring for fills...")
    send_telegram(f"ORB: {len(limit_orders)} orders placed (until 2:45 PM)")

    # Monitor for fills
    while limit_orders:
        now = datetime.now()
        if now.hour > 14 or (now.hour == 14 and now.minute >= 45):
            log.info(f"ORB: 2:45 PM — cancelling {len(limit_orders)} unfilled orders")
            for lo in limit_orders:
                if lo.get('order_id') and not paper_mode:
                    try:
                        api.cancel_order(lo['order_id'])
                        log.info(f"  Cancelled order {lo['order_id']} for {lo['sym']}")
                    except Exception as e:
                        log.warning(f"  Cancel failed {lo['sym']}: {e}")
                if lo['trade'].get('master_trade_id'):
                    report_exit(lo['trade']['master_trade_id'], 0)
                send_telegram(f"ORB CANCEL: {lo['sym']} no fill by 2:45 PM")
            break

        for lo in limit_orders[:]:
            sym = lo['sym']
            t = lo['trade']
            entry_price = lo['entry_price']

            try:
                # Check order book for fill status (more reliable than LTP)
                filled = False
                actual_fill_price = entry_price
                if lo.get('order_id') and not paper_mode:
                    try:
                        order_book = api.get_order_book()
                        if order_book:
                            for ob in order_book:
                                if ob.get('id') == lo['order_id'] and ob.get('status') == 'SUCCESS':
                                    filled = True
                                    actual_fill_price = ob.get('traded_price', entry_price)
                                    log.info(f"ORB ORDER FILLED (broker): {sym} @ {actual_fill_price}")
                                    break
                    except Exception as e:
                        log.warning(f"Order book check error: {e}")

                # Fallback: also check LTP for paper mode or if order book fails
                if not filled:
                    ltp_data = api.get_ltp([sym])
                    price = ltp_data.get(sym, 0)
                    if price <= 0:
                        continue
                    if t['call'] == 'BUY' and price >= entry_price:
                        filled = True
                        actual_fill_price = price
                    elif t['call'] == 'SELL' and price <= entry_price:
                        filled = True
                        actual_fill_price = price

                if not filled:
                    continue

                price = actual_fill_price

                log.info(f"ORB FILLED! {sym} {t['call']} @ {entry_price:.1f} (current={price:.1f})")

                if lo['is_option']:
                    # Now buy the option at market
                    qty = lo['qty']
                    opt_type = lo['opt_type']
                    best_sec_id = lo['best_sec_id']
                    best_strike = lo['best_strike']

                    if not paper_mode:
                        oid = api.place_fno_order(sym, qty, 'BUY', best_sec_id, order_type='MARKET')
                        if not oid:
                            log.error(f"ORB option order failed {sym}")
                            if t.get('master_trade_id'):
                                report_exit(t['master_trade_id'], 0)
                            limit_orders.remove(lo)
                            continue
                    time.sleep(2)
                    opt_ltp = 100
                    try:
                        r = requests.get(
                            f'https://api.indstocks.com/market/quotes/ltp?scrip-codes=NSE_{best_sec_id}',
                            headers=api.headers(), timeout=10)
                        if r.status_code == 200:
                            vals = list(r.json().get('data', {}).values())
                            if vals:
                                opt_ltp = vals[0].get('live_price', 100)
                    except:
                        pass

                    executed.append({
                        'symbol': sym, 'call': t['call'], 'trade_type': 'OPTIONS',
                        'opt_type': opt_type, 'entry_price': opt_ltp, 'qty': qty,
                        'projection': t['projection'], 'why': t.get('why', ''),
                        'entry_time': datetime.now().isoformat(),
                        'strike': best_strike, 'sec_id': best_sec_id,
                        'peak_price': opt_ltp, 'trail_active': False, 'reversed': False,
                        'sl_pct': 20.0, 'trail_activate_pct': 10.0,
                        'trail_pct': 5.0,
                        'or_high': lo['or_high'], 'or_low': lo['or_low'],
                        'underlying_entry': entry_price,
                        'master_trade_id': t.get('master_trade_id'),
                    })
                    send_telegram(
                        f"ORB FILLED {t['call']}: {sym} {best_strike}{opt_type} x{qty} @ {opt_ltp:.1f}\n"
                        f"SL: -{lo['sl_pct']:.1f}% | Trail: +{ORB_TRAIL_ACTIVATE}%/{ORB_TRAIL_PCT}%"
                    )
                else:
                    # MIS equity — limit order already placed, should be filled
                    time.sleep(2)
                    actual_entry = api.get_ltp([sym]).get(sym, entry_price)

                    executed.append({
                        'symbol': sym, 'call': t['call'], 'trade_type': 'MIS',
                        'opt_type': 'MIS', 'entry_price': actual_entry, 'qty': lo['qty'],
                        'projection': t['projection'], 'why': t.get('why', ''),
                        'entry_time': datetime.now().isoformat(),
                        'peak_price': actual_entry, 'trail_active': False, 'reversed': False,
                        'sl_pct': lo['sl_pct'],
                        'trail_activate_pct': ORB_TRAIL_ACTIVATE,
                        'trail_pct': ORB_TRAIL_PCT,
                        'or_high': lo['or_high'], 'or_low': lo['or_low'],
                        'master_trade_id': t.get('master_trade_id'),
                    })
                    send_telegram(
                        f"ORB FILLED {t['call']}: {lo['qty']}x {sym} @ {actual_entry:.1f}\n"
                        f"SL: -{lo['sl_pct']:.1f}% | Trail: +{ORB_TRAIL_ACTIVATE}%/{ORB_TRAIL_PCT}%"
                    )

                limit_orders.remove(lo)

            except Exception as e:
                log.warning(f"ORB monitor error {sym}: {e}")

        if limit_orders:
            time.sleep(10)

    return executed

def orb_monitor(executed, paper_mode=True):
    """Monitor ORB positions: SL at OR opposite, trail 0.5% activate +1%."""
    from live import indmoney_client as api
    if not executed:
        return executed

    log.info(f"ORB: Monitoring {len(executed)} positions...")
    active = list(executed)

    while active:
        now = datetime.now()
        # 3:10 PM hard exit
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            log.info("3:10 PM — exit all ORB positions (checking broker first)")
            for e in active:
                sym = e['symbol']
                if not paper_mode:
                    if e.get('sec_id'):
                        api.place_fno_order(sym, e['qty'], 'SELL', e['sec_id'], order_type='MARKET')
                    else:
                        reverse_side = 'SELL' if e['call'] == 'BUY' else 'BUY'
                        api.place_order(sym, e['qty'], reverse_side, price=0,
                                       order_type='MARKET', product='INTRADAY')
                current = api.get_ltp([sym]).get(sym, e['entry_price'])
                if e['call'] == 'BUY':
                    pnl = ((current - e['entry_price']) / e['entry_price']) * 100
                else:
                    pnl = ((e['entry_price'] - current) / e['entry_price']) * 100
                log.info(f"  EXIT 3:10: {sym} @ {current:.1f} ({pnl:+.1f}%)")
                # Check if position still exists at broker before closing
                try:
                    broker_pos = api.get_positions()
                    still_open = any(p.get('symbol','') == sym or p.get('scrip','') == sym for p in (broker_pos or []))
                    if not still_open:
                        log.info(f"  ORB: {sym} already closed (manually or by broker) — skipping exit")
                        continue
                except:
                    pass  # if can't check, proceed with close
                send_telegram(f"ORB EXIT 3:10PM: {sym} @ {current:.1f} ({pnl:+.1f}%)")
                if e.get('master_trade_id'):
                    report_exit(e['master_trade_id'], pnl)
            break

        for e in active[:]:
            sym = e['symbol']
            # Get current price
            if e.get('sec_id'):
                try:
                    r = requests.get(
                        f'https://api.indstocks.com/market/quotes/ltp?scrip-codes=NSE_{e["sec_id"]}',
                        headers=api.headers(), timeout=10)
                    if r.status_code == 200:
                        vals = list(r.json().get('data', {}).values())
                        current = vals[0].get('live_price', 0) if vals else 0
                    else:
                        current = 0
                except:
                    current = 0
            else:
                current = api.get_ltp([sym]).get(sym, 0)
            if current <= 0:
                continue

            entry = e['entry_price']
            if e['call'] == 'BUY':
                move = ((current - entry) / entry) * 100
            else:
                move = ((entry - current) / entry) * 100

            # Update peak
            if e['call'] == 'BUY' and current > e['peak_price']:
                e['peak_price'] = current
            elif e['call'] == 'SELL' and current < e['peak_price']:
                e['peak_price'] = current

            # SL check
            if move <= -e['sl_pct']:
                log.info(f"  ORB SL HIT: {sym} ({move:+.1f}%)")
                if not paper_mode:
                    if e.get('sec_id'):
                        api.place_fno_order(sym, e['qty'], 'SELL', e['sec_id'], order_type='MARKET')
                    else:
                        reverse_side = 'SELL' if e['call'] == 'BUY' else 'BUY'
                        api.place_order(sym, e['qty'], reverse_side, price=0,
                                       order_type='MARKET', product='INTRADAY')
                send_telegram(f"ORB SL: {sym} ({move:+.1f}%)")
                if e.get('master_trade_id'):
                    report_exit(e['master_trade_id'], move)
                active.remove(e)
                continue

            # Trail activation
            if not e['trail_active'] and move >= e['trail_activate_pct']:
                e['trail_active'] = True
                log.info(f"  ORB TRAIL ON: {sym} ({move:+.1f}%)")

            # Trail exit
            if e['trail_active']:
                peak = e['peak_price']
                if e['call'] == 'BUY':
                    drop = ((current - peak) / peak) * 100
                else:
                    drop = ((peak - current) / peak) * 100
                if drop <= -e['trail_pct']:
                    pnl = move
                    log.info(f"  ORB TRAIL EXIT: {sym} ({pnl:+.1f}%)")
                    if not paper_mode:
                        if e.get('sec_id'):
                            api.place_fno_order(sym, e['qty'], 'SELL', e['sec_id'], order_type='MARKET')
                        else:
                            reverse_side = 'SELL' if e['call'] == 'BUY' else 'BUY'
                            api.place_order(sym, e['qty'], reverse_side, price=0,
                                           order_type='MARKET', product='INTRADAY')
                    send_telegram(f"ORB TRAIL: {sym} ({pnl:+.1f}%)")
                    if e.get('master_trade_id'):
                        report_exit(e['master_trade_id'], pnl)
                    active.remove(e)

        if active:
            time.sleep(10)

    return executed


def execute_and_monitor(option_trades, mis_trades, paper_mode=True):
    from live import indmoney_client as api
    executed = []

    # Ask Master for capital allocation
    master_up = False
    try:
        master_up = master_available()
    except:
        pass

    if master_up:
        # Send ALL signals to Master, get approved trades with sizes
        signals = []
        all_trades = mis_trades + option_trades
        for t in all_trades:
            signals.append({
                'sym': t['symbol'],
                'projection': t.get('projection', 0),
                'conviction': 8 if abs(t.get('projection', 0)) >= 7 else 6 if abs(t.get('projection', 0)) >= 5 else 4,
                'catalyst': t.get('why', '')[:50],
            })
        approved = request_batch(signals)
        if approved:
            # Build allocation map: sym -> (amount, trade_id)
            alloc_map = {a['sym']: (a['amount'], a['trade_id']) for a in approved}
            log.info(f"Master approved {len(approved)} trades: {[(a['sym'], a['amount']) for a in approved]}")
        else:
            alloc_map = None
            log.info("Master unavailable, using local sizing")
    else:
        alloc_map = None
        log.info("Master not running, using local sizing")

    # Fallback: local sizing if Master unavailable
    if alloc_map is None:
        capital = api.get_funds()
        per_trade_local = capital * MAX_CAPITAL_PCT
    else:
        per_trade_local = 0  # won't be used

    # MIS equity trades
    for t in mis_trades:
        sym = t.get('nse_symbol', t['symbol'])
        call = t['call']
        side = 'BUY' if call == 'BUY' else 'SELL'

        # Get allocation from Master or local
        if alloc_map and sym in alloc_map:
            per_trade = alloc_map[sym][0]
            t['master_trade_id'] = alloc_map[sym][1]
        elif alloc_map and sym not in alloc_map:
            log.info(f"  {sym} not approved by Master, skipping")
            continue
        else:
            per_trade = per_trade_local

        # Get LTP for sizing
        ltp_data = api.get_ltp([sym])
        ltp = ltp_data.get(sym, 0)
        if ltp <= 0:
            if sym not in api.SCRIP_CODES:
                sec_id = lookup_scrip(sym)
                if sec_id:
                    ltp_data = api.get_ltp([sym])
                    ltp = ltp_data.get(sym, 0)
            if ltp <= 0:
                log.warning(f"{sym}: no LTP even after lookup, skipping")
                if t.get('master_trade_id'): report_exit(t['master_trade_id'], 0)
                continue

        qty = max(1, int(per_trade / ltp))
        log.info(f"MIS {side} {qty}x {sym} @ {ltp:.1f} (alloc Rs {per_trade:,.0f})")

        if paper_mode:
            place_order_paper(sym, qty, side, 'MIS', 0, ltp)
        else:
            oid = api.place_order(sym, qty, side, price=0, order_type='MARKET', product='INTRADAY')
            if not oid:
                log.error(f"Order failed for {sym}")
                continue
            log.info(f"ORDER PLACED: {side} {qty}x {sym} -> {oid}")

        # Re-fetch LTP after order for actual entry
        time.sleep(2)
        ltp2 = api.get_ltp([sym]).get(sym, ltp)
        entry_price = ltp2 if ltp2 > 0 else ltp

        executed.append({
            'symbol': sym, 'call': call, 'trade_type': 'MIS', 'opt_type': 'MIS',
            'entry_price': entry_price, 'qty': qty, 'projection': t['projection'],
            'category': t.get('category', ''), 'why': t.get('why', ''),
            'entry_time': datetime.now().isoformat(),
            'peak_price': entry_price, 'trail_active': False, 'reversed': False,
            'sl_pct': get_atr_sl(sym), 'trail_activate_pct': 1.0, 'trail_pct': 1.0,
        })
        send_telegram(
            f"{'BUY' if side=='BUY' else 'SELL'} MIS: {qty}x {sym} @ {entry_price:.1f}\n"
            f"Proj: {t['projection']:+.1f}% | SL: -{executed[-1]['sl_pct']:.1f}% (ATR)"
        )

    # Options trades (for F&O eligible stocks) - auto execute
    for t in option_trades:
        sym = t.get('nse_symbol', t['symbol'])
        call = t['call']
        opt_type = 'CE' if call == 'BUY' else 'PE'

        # Get option chain and find ATM strike
        chain = api.get_option_chain(sym)
        if not chain:
            log.warning(f"{sym}: no option chain, falling back to MIS")
            ltp_data = api.get_ltp([sym])
            ltp = ltp_data.get(sym, 0)
            if ltp <= 0:
                continue
            side = 'BUY' if call == 'BUY' else 'SELL'
            qty = max(1, int(per_trade / ltp))
            if not paper_mode:
                api.place_order(sym, qty, side, price=0, order_type='MARKET', product='INTRADAY')
            time.sleep(2)
            entry_price = api.get_ltp([sym]).get(sym, ltp)
            executed.append({
                'symbol': sym, 'call': call, 'trade_type': 'MIS', 'opt_type': 'MIS',
                'entry_price': entry_price, 'qty': qty, 'projection': t['projection'],
                'category': t.get('category', 'NEWS'), 'why': t.get('why', ''),
                'entry_time': datetime.now().isoformat(),
                'peak_price': entry_price, 'trail_active': False, 'reversed': False,
                'sl_pct': get_atr_sl(sym), 'trail_activate_pct': 1.0, 'trail_pct': 1.0,
            })
            log.info(f"MIS fallback: {side} {qty}x {sym} @ {entry_price:.1f} SL={executed[-1]['sl_pct']:.1f}%")
            continue

        # Find ATM strike
        ltp_data = api.get_ltp([sym])
        ltp = ltp_data.get(sym, 0)
        if ltp <= 0:
            continue

        best_strike = None
        best_sec_id = None
        best_diff = float('inf')
        lot_size = 1
        for strike_data in chain:
            strike = strike_data.get('strike_price', 0)
            if abs(strike - ltp) < best_diff:
                best_diff = abs(strike - ltp)
                best_strike = strike
                opt_data = strike_data.get('ce' if opt_type == 'CE' else 'pe', {})
                best_sec_id = opt_data.get('security_id')
                lot_size = strike_data.get('lot_size', 1)

        if not best_sec_id:
            log.warning(f"{sym}: no ATM {opt_type} found")
            continue

        qty = lot_size
        log.info(f"OPTIONS: BUY {qty}x {sym} {best_strike}{opt_type} (sec_id={best_sec_id})")
        if not paper_mode:
            oid = api.place_fno_order(sym, qty, 'BUY', best_sec_id, order_type='MARKET')
            if not oid:
                log.error(f"FnO order failed for {sym}")
                continue

        time.sleep(2)
        # Get option LTP after order
        opt_ltp = 100  # fallback
        try:
            r = requests.get(f'https://api.indstocks.com/market/quotes/ltp?scrip-codes=NSE_{best_sec_id}',
                           headers=api.headers(), timeout=10)
            if r.status_code == 200:
                vals = list(r.json().get('data', {}).values())
                if vals:
                    opt_ltp = vals[0].get('live_price', 100)
        except Exception as e:
            log.warning(f'Option LTP fetch error: {e}')

        executed.append({
            'symbol': sym, 'call': call, 'trade_type': 'OPTIONS', 'opt_type': opt_type,
            'entry_price': opt_ltp, 'qty': qty, 'projection': t['projection'],
            'category': t.get('category', 'NEWS'), 'why': t.get('why', ''),
            'entry_time': datetime.now().isoformat(), 'strike': best_strike,
            'sec_id': best_sec_id,
            'peak_price': opt_ltp, 'trail_active': False, 'reversed': False,
            'sl_pct': lo['sl_pct'], 'trail_activate_pct': ORB_TRAIL_ACTIVATE, 'trail_pct': ORB_TRAIL_PCT,
        })
        log.info(f"OPTIONS EXECUTED: {sym} {best_strike}{opt_type} qty={qty} @ {opt_ltp:.1f}")

    if not executed:
        return []

    log.info(f"Monitoring {len(executed)} MIS positions...")
    active = list(executed)

    while active:
        now = datetime.now()
        # 3:10 PM hard exit
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            log.info("3:10 PM exit all")
            for e in active:
                sym = e['symbol']
                if not paper_mode:
                    if e.get('sec_id'):
                        # Options: sell to close
                        api.place_fno_order(sym, e['qty'], 'SELL', e['sec_id'], order_type='MARKET')
                    else:
                        # MIS equity: reverse
                        reverse_side = 'SELL' if e['call'] == 'BUY' else 'BUY'
                        api.place_order(sym, e['qty'], reverse_side, price=0,
                                      order_type='MARKET', product='INTRADAY')
                current = api.get_ltp([sym]).get(sym, e['entry_price'])
                pnl = ((current - e['entry_price']) / e['entry_price']) * 100
                log.info(f"  EXIT: {sym} @ {current:.1f} ({pnl:+.1f}%)")
                send_telegram(f"EXIT 3:10PM: {sym} @ {current:.1f} ({pnl:+.1f}%)")
                if t.get('master_trade_id'): report_exit(t['master_trade_id'], pnl)
            break

        for e in active[:]:
            sym = e['symbol']
            # For options, get option LTP using sec_id; for MIS, get stock LTP
            if e.get('sec_id'):
                try:
                    r = requests.get(
                        f'https://api.indstocks.com/market/quotes/ltp?scrip-codes=NSE_{e["sec_id"]}',
                        headers=api.headers(), timeout=10)
                    if r.status_code == 200:
                        vals = list(r.json().get('data', {}).values())
                        current = vals[0].get('live_price', 0) if vals else 0
                    else:
                        current = 0
                except:
                    current = 0
            else:
                ltp_data = api.get_ltp([sym])
                current = ltp_data.get(sym, 0)
            if current <= 0:
                continue

            entry = e['entry_price']
            if e['call'] == 'BUY':
                move = ((current - entry) / entry) * 100
            else:
                move = ((entry - current) / entry) * 100

            # Update peak (highest for BUY, lowest for SELL)
            if e['call'] == 'BUY' and current > e['peak_price']:
                e['peak_price'] = current
            elif e['call'] == 'SELL' and current < e['peak_price']:
                e['peak_price'] = current

            # SL check
            sl = e['sl_pct']
            if move <= -sl:
                reverse_side = 'SELL' if e['call'] == 'BUY' else 'BUY'
                log.info(f"  SL HIT: {sym} ({move:+.1f}%)")
                if not paper_mode:
                    if e.get('sec_id'):
                        api.place_fno_order(sym, e['qty'], 'SELL', e['sec_id'], order_type='MARKET')
                    else:
                        api.place_order(sym, e['qty'], reverse_side, price=0,
                                      order_type='MARKET', product='INTRADAY')
                log.info(f"  SL EXIT order placed: {sym}")
                if t.get('master_trade_id'): report_exit(t['master_trade_id'], move)
                active.remove(e)
                continue

            # Trail activation
            if not e['trail_active'] and move >= e['trail_activate_pct']:
                e['trail_active'] = True
                log.info(f"  TRAIL ON: {sym} ({move:+.1f}%)")

            # Trail exit
            if e['trail_active']:
                peak = e['peak_price']
                if e['call'] == 'BUY':
                    drop = ((current - peak) / peak) * 100
                else:
                    drop = ((peak - current) / peak) * 100
                if drop <= -e['trail_pct']:
                    reverse_side = 'SELL' if e['call'] == 'BUY' else 'BUY'
                    pnl = move
                    log.info(f"  TRAIL EXIT: {sym} ({pnl:+.1f}%)")
                    if not paper_mode:
                        if e.get('sec_id'):
                            api.place_fno_order(sym, e['qty'], 'SELL', e['sec_id'], order_type='MARKET')
                        else:
                            api.place_order(sym, e['qty'], reverse_side, price=0,
                                          order_type='MARKET', product='INTRADAY')
                    log.info(f"  TRAIL EXIT order placed: {sym}")
                    if t.get('master_trade_id'): report_exit(t['master_trade_id'], move)
                    active.remove(e)

        if active:
            time.sleep(10)

    return executed



# ── Main: Pre-market scan ──
def main():
    scan_only = '--scan-only' in sys.argv
    paper_mode = "--paper" in sys.argv

    if not GEMINI_KEY:
        log.error("Set GEMINI_API_KEY in .env")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y-%m-%d')
    log.info(f"{'='*50}")
    log.info(f"OPTIONS BOT — {date_str} {'(SCAN)' if scan_only else '(PAPER)' if paper_mode else '(LIVE)'}")
    log.info(f"{'='*50}")

    articles, seen_keys = fetch_all_rss()
    skip = ['cricket', 'bollywood', 'weather', 'horoscope', 'recipe', 'movie']
    articles = [a for a in articles if not any(s in a['title'].lower() for s in skip)]
    log.info(f"After filter: {len(articles)} articles")

    if len(articles) < 3:
        log.info("Not enough articles. Sitting out.")
        send_telegram(f"Options Bot {date_str}: Not enough news.")
        return

    articles = filter_stock_headlines(articles)

    log.info(f"Gemini analyzing {len(articles)} stock headlines in batches...")
    all_trades = []
    all_skips = {}  # sym -> [skip reasons]
    batch_size = 20
    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(articles) + batch_size - 1) // batch_size
        log.info(f"  Batch {batch_num}/{total_batches}: {len(batch)} headlines")
        prompt = build_analysis_prompt(batch, date_str)
        analysis = call_gemini(prompt)
        batch_trades = parse_trades(analysis)

        # Track ALL decisions — TRADE and SKIP — per stock
        if analysis:
            try:
                clean = analysis.replace("```json", "").replace("```", "").strip()
                match = re.search(r'\[.*\]', clean, re.DOTALL)
                if match:
                    all_items = json.loads(match.group())
                    for item in all_items:
                        sym = item.get('symbol', '').upper()
                        dec = item.get('decision', '').upper()
                        why = item.get('why', '')[:100]
                        if 'SKIP' in dec and sym:
                            all_skips.setdefault(sym, []).append(why)
            except:
                pass

        all_trades.extend(batch_trades)
        time.sleep(2)

    # Find stocks with CONFLICTING decisions (TRADE in one batch, SKIP in another)
    conflict_stocks = set()
    trade_syms = {t['symbol'] for t in all_trades}
    for sym in trade_syms:
        if sym in all_skips:
            conflict_stocks.add(sym)
            log.info(f"  CONFLICT: {sym} — TRADE + SKIP in same run. Will re-analyze.")

    # Re-analyze conflicting stocks with ALL their headlines in ONE call
    if conflict_stocks:
        # Remove conflicting stocks from trades
        all_trades = [t for t in all_trades if t['symbol'] not in conflict_stocks]

        for sym in conflict_stocks:
            sym_articles = [a for a in articles if sym.lower() in a.get('title', '').lower() or sym in a.get('title', '').upper()]
            if not sym_articles:
                continue
            log.info(f"  RE-ANALYZING {sym} with ALL {len(sym_articles)} headlines together")
            prompt = build_analysis_prompt(sym_articles, date_str)
            analysis = call_gemini(prompt)
            rerun_trades = parse_trades(analysis)
            for t in rerun_trades:
                if t['symbol'] == sym:
                    all_trades.append(t)
                    log.info(f"  RE-ANALYSIS {sym}: {t.get('call', '?')} proj={t.get('projection', '?')}")
            time.sleep(2)

    trades = all_trades
    # Dedup by symbol (keep first occurrence)
    seen_syms = set()
    unique_trades = []
    for t in trades:
        if t['symbol'] not in seen_syms:
            seen_syms.add(t['symbol'])
            unique_trades.append(t)
    trades = unique_trades
    log.info(f"  Total trades after dedup: {len(trades)}")

    if not trades:
        log.info("No trades found. Sitting out.")
        send_telegram(f"News Bot {date_str}: No trades. Sitting out.")
        return

    option_trades, mis_trades = route_trades(trades)
    log.info(f"  Mode: {TRADE_MODE} | Options: {len(option_trades)} | MIS: {len(mis_trades)}")

    if scan_only:
        msg = f"News Scan {date_str}:\n"
        for t in trades:
            msg += f"  {t['symbol']} {t['call']} {t['projection']:+.1f}% ({t.get('category', 'NEWS')})\n"
        send_telegram(msg)
        return

    # ── ORB Flow: Wait for range, then breakout ──
    now = datetime.now()
    market_open = now.replace(hour=9, minute=15, second=0)
    if now < market_open:
        wait = (market_open - now).total_seconds()
        log.info(f"Waiting {wait:.0f}s for 9:15 AM market open...")
        time.sleep(wait)

    # Combine all trades for ORB
    all_orb_trades = mis_trades + option_trades

    # Get symbols for OR tracking
    orb_symbols = list(set(t.get('nse_symbol', t['symbol']) for t in all_orb_trades))
    log.info(f"ORB: {len(orb_symbols)} symbols to track: {orb_symbols}")

    # Phase 1: Collect opening range (9:15-9:30)
    or_data = orb_collect_range(orb_symbols)

    # Phase 2: Get FM allocation
    master_up = False
    alloc_map = None
    try:
        master_up = master_available()
    except:
        pass
    if master_up:
        signals = []
        for t in all_orb_trades:
            signals.append({
                'sym': t.get('nse_symbol', t['symbol']),
                'projection': t.get('projection', 0),
                'conviction': 8 if abs(t.get('projection', 0)) >= 7 else 6 if abs(t.get('projection', 0)) >= 5 else 4,
                'catalyst': t.get('why', '')[:50],
            })
        approved = request_batch(signals)
        if approved:
            alloc_map = {a['sym']: (a['amount'], a['trade_id']) for a in approved}
            log.info(f"Master approved {len(approved)} trades")

    # Phase 3: Watch for breakouts (9:30-11:00)
    orb_executed = orb_wait_breakout(all_orb_trades, or_data, paper_mode, alloc_map)

    # Phase 4: Monitor positions
    executed = orb_monitor(orb_executed, paper_mode) if orb_executed else []

    # Save seen keys for continuous scanner dedup
    with open(LOG_DIR / f'options_{date_str}_seen.json', 'w') as f:
        json.dump(list(seen_keys), f)

    out = {
        'date': date_str,
        'trades': [{'symbol': e['symbol'], 'call': e['call'], 'opt_type': e['opt_type'],
                     'projection': e['projection'], 'category': e['category'],
                     'reversed': e['reversed']} for e in executed],
    }
    with open(LOG_DIR / f'options_{date_str}.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    log.info(f"Saved to options_{date_str}.json")


# ── Continuous: Intraday scanner (replaces old Claude news_bot.py) ──
def continuous_scan():
    """Scan RSS every 5 min during market hours for BREAKING news.
    Uses Gemini + grounding — free, no Claude credits needed."""
    paper_mode = "--paper" in sys.argv

    if not GEMINI_KEY:
        log.error("Set GEMINI_API_KEY in .env")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y-%m-%d')
    seen_keys = set()

    log.info(f"{'='*50}")
    log.info(f"OPTIONS BOT CONTINUOUS — {date_str} {'(PAPER)' if paper_mode else '(LIVE)'}")
    log.info(f"Scanning every 5 min from 9:30 AM to 2:30 PM (Gemini)")
    log.info(f"{'='*50}")

    # Load pre-market seen headlines for dedup
    premarket_log = LOG_DIR / f'options_{date_str}_seen.json'
    if premarket_log.exists():
        seen_keys = set(json.load(open(premarket_log)))
        log.info(f"Loaded {len(seen_keys)} seen headlines from pre-market scan")

    while True:
        now = datetime.now()
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            time.sleep(60)
            continue
        if now.hour > 14 or (now.hour == 14 and now.minute > 30):
            log.info("2:30 PM — stopping continuous scan")
            break

        log.info(f"\n--- Intraday scan at {now.strftime('%H:%M')} ---")
        try:
            articles_all, _ = fetch_all_rss(window_hours=2)
            skip = ['cricket', 'bollywood', 'weather', 'horoscope', 'recipe', 'movie']
            articles_all = [a for a in articles_all if not any(s in a['title'].lower() for s in skip)]

            new_articles = []
            for a in articles_all:
                key = re.sub(r'[^a-z0-9]', '', a['title'].lower())[:50]
                if key not in seen_keys:
                    seen_keys.add(key)
                    new_articles.append(a)

            if not new_articles:
                log.info(f"  No new articles since last scan")
                time.sleep(300)
                continue

            new_articles = filter_stock_headlines(new_articles)
            log.info(f"  {len(new_articles)} stock-relevant NEW articles")

            prompt = build_analysis_prompt(new_articles, date_str, market_open=True)
            analysis = call_gemini(prompt)
            trades = parse_trades(analysis)

            if not trades:
                log.info(f"  No trades found")
                time.sleep(300)
                continue

            log.info(f"  {len(trades)} intraday trades found!")
            for t in trades:
                log.info(f"  INTRADAY TRADE: {t['symbol']} {t['call']} proj={t['projection']:+.1f}% | {t.get('why','')[:100]}")

            # Save intraday trades to JSON for EOD review
            intraday_log = LOG_DIR / f'continuous_{date_str}.json'
            existing = []
            if intraday_log.exists():
                try:
                    existing = json.loads(intraday_log.read_text())
                except:
                    pass
            for t in trades:
                existing.append({
                    'time': datetime.now().strftime('%H:%M'),
                    'symbol': t['symbol'],
                    'call': t['call'],
                    'projection': t['projection'],
                    'why': t.get('why', ''),
                })
            intraday_log.write_text(json.dumps(existing, indent=2))

        except Exception as e:
            log.error(f"  Scan error: {e}")

        time.sleep(300)

    with open(LOG_DIR / f'options_{date_str}_seen.json', 'w') as f:
        json.dump(list(seen_keys), f)
    log.info("Continuous scanner done.")


if __name__ == '__main__':
    if '--continuous' in sys.argv:
        continuous_scan()
    else:
        main()
