"""News Scanner — fetch stock news from RSS feeds + NSE/BSE exchange filings.

Exchange filings are THE fastest public source:
- Direct from NSE/BSE, before Twitter, before Moneycontrol
- Orders, M&A, resignations, SEBI actions appear here FIRST
- Most filings happen 5 PM - 9 AM (after market to before market)

Used by: news_orb agent.
"""
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from trading.logger import get_logger

log = get_logger('rss')
IST = timezone(timedelta(hours=5, minutes=30))
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

FEEDS = [
    ('https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms', 'ET Markets'),
    ('https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms', 'ET Stocks'),
    ('https://www.livemint.com/rss/markets', 'LiveMint'),
    ('https://www.business-standard.com/rss/markets-106.rss', 'BizStandard'),
    ('https://feeds.feedburner.com/ndtvprofit-latest', 'NDTV'),
]


def fetch_all(window_hours: int = 17) -> list[dict]:
    """Fetch RSS + Zerodha Pulse + NSE/BSE filings. Deduplicated."""
    now = datetime.now(IST)
    cutoff = now - timedelta(hours=window_hours)
    articles, seen = [], set()

    # ── NSE Exchange Filings (FASTEST source) ──
    nse_count = _fetch_nse_filings(articles, seen, cutoff, now)
    bse_count = _fetch_bse_filings(articles, seen, cutoff, now)
    log.info(f'Exchange filings: NSE={nse_count} BSE={bse_count}')

    # ── RSS Feeds ──
    for url, source in FEEDS:
        try:
            r = requests.get(url, headers=H, timeout=15)
            if r.status_code != 200:
                continue
            for item in re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL):
                title_m = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
                if not title_m:
                    continue
                title = re.sub(r'<!\[CDATA\[|\]\]>|<[^>]+>', '', title_m.group(1)).strip()

                pub_dt = None
                pub_m = re.search(r'<pubDate>(.*?)</pubDate>', item, re.DOTALL)
                if pub_m:
                    try:
                        raw = re.sub(r'<!\[CDATA\[|\]\]>', '', pub_m.group(1)).strip()
                        pub_dt = parsedate_to_datetime(raw).astimezone(IST)
                    except:
                        pass
                if pub_dt and not (cutoff <= pub_dt <= now):
                    continue

                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key in seen or len(title) < 15:
                    continue
                seen.add(key)
                articles.append({'title': title, 'source': source,
                                 'pubDate': pub_dt.strftime('%Y-%m-%d %H:%M') if pub_dt else '?'})
        except Exception as e:
            log.warning(f'{source}: {e}')

    # ── Zerodha Pulse ──
    try:
        r = requests.get('https://pulse.zerodha.com/', headers=H, timeout=10)
        if r.status_code == 200:
            for _, raw in re.findall(r'<h2[^>]*class="title"[^>]*>\s*<a[^>]*>(.*?)</a>', r.text):
                title = re.sub(r'<[^>]+>', '', raw).strip()
                key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
                if key not in seen and len(title) >= 15:
                    seen.add(key)
                    articles.append({'title': title, 'source': 'Pulse', 'pubDate': '?'})
    except:
        pass

    # Skip junk
    skip = ['cricket', 'bollywood', 'weather', 'horoscope', 'recipe', 'movie']
    articles = [a for a in articles if not any(s in a['title'].lower() for s in skip)]

    log.info(f'Fetched {len(articles)} articles (incl exchange filings)')
    return articles


# ── NSE Filings ──────────────────────────────────────────

def _fetch_nse_filings(articles: list, seen: set, cutoff, now) -> int:
    """Fetch corporate announcements directly from NSE API."""
    count = 0
    try:
        s = requests.Session()
        s.headers.update(H)
        # Get session cookie
        s.get('https://www.nseindia.com', timeout=10)
        time.sleep(1)

        r = s.get('https://www.nseindia.com/api/corporate-announcements?index=equities',
                   timeout=30)
        if r.status_code != 200:
            log.warning(f'NSE API: {r.status_code}')
            return 0

        for item in r.json():
            sym = item.get('symbol', '')
            desc = item.get('desc', '')
            subj = item.get('attchmntText', item.get('subject', ''))

            # Skip boring filings
            skip_types = ['book closure', 'record date', 'agm', 'annual report',
                          'newspaper', 'compliance certificate', 'scrutiniser',
                          'shareholders meeting', 'trading window']
            if any(s in desc.lower() for s in skip_types):
                continue
            if any(s in subj.lower() for s in skip_types):
                continue

            # Build headline from filing
            title = f'{sym}: {desc}'
            if subj and len(subj) > len(desc):
                # Extract the key info from attachment text
                # "XYZ Limited has informed the Exchange about Order Win of Rs 500 crore"
                title = f'{sym}: {_clean_filing_text(subj)}'

            key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
            if key in seen or len(title) < 10:
                continue
            seen.add(key)

            dt_str = item.get('an_dt', '')
            articles.append({
                'title': title,
                'source': 'NSE Filing',
                'pubDate': dt_str[:16] if dt_str else '?',
            })
            count += 1
    except Exception as e:
        log.warning(f'NSE filings: {e}')
    return count


def _fetch_bse_filings(articles: list, seen: set, cutoff, now) -> int:
    """Fetch corporate announcements from BSE API."""
    count = 0
    try:
        today = now.strftime('%Y%m%d')
        yesterday = (now - timedelta(days=1)).strftime('%Y%m%d')

        bse_headers = {
            **H,
            'Referer': 'https://www.bseindia.com/',
            'Origin': 'https://www.bseindia.com/',
            'Accept': 'application/json',
        }

        url = (f'https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w?'
               f'Pageno=1&strCat=-1&strPrevDate={yesterday}&strToDate={today}'
               f'&strScrip=&strSearch=P&strType=C')

        r = requests.get(url, headers=bse_headers, timeout=30)
        if r.status_code != 200:
            return 0

        data = r.json()
        if isinstance(data, str):
            try:
                data = __import__('json').loads(data)
            except:
                return 0

        items = []
        if isinstance(data, dict) and 'Table' in data:
            items = data['Table']
        elif isinstance(data, list):
            items = data

        skip_types = ['book closure', 'record date', 'agm', 'annual report',
                      'newspaper', 'compliance', 'scrutiniser', 'shareholders meeting']

        for item in items[:50]:
            subj = item.get('NEWSSUB', item.get('NEWS_SUBJECT', ''))
            sym = item.get('SCRIP_CD', item.get('SLONGNAME', ''))

            if any(s in subj.lower() for s in skip_types):
                continue

            title = f'{sym}: {subj}'
            key = re.sub(r'[^a-z0-9]', '', title.lower())[:50]
            if key in seen or len(title) < 10:
                continue
            seen.add(key)

            dt = item.get('NEWS_DT', item.get('DT_TM', ''))
            articles.append({
                'title': title,
                'source': 'BSE Filing',
                'pubDate': dt[:16] if dt else '?',
            })
            count += 1
    except Exception as e:
        log.warning(f'BSE filings: {e}')
    return count


def _clean_filing_text(text: str) -> str:
    """Extract key info from verbose NSE filing text."""
    # "XYZ Limited has informed the Exchange about Order Win worth Rs 500 crore"
    # → "Order Win worth Rs 500 crore"
    text = re.sub(r'^.*?has informed.*?(?:about|regarding|that)\s+', '', text, flags=re.I)
    # Remove HTML
    text = re.sub(r'<[^>]+>', '', text)
    # Truncate
    text = text.strip()[:150]
    return text
