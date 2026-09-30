"""
Unified News Aggregator — all free Indian stock news sources with deterministic time filtering.

Sources (all free, all with timestamps):
1. Google News RSS — broad coverage, date-filtered
2. ET Markets RSS — real-time market news
3. LiveMint RSS — real-time
4. Business Standard RSS — real-time
5. NDTV Profit RSS — real-time
6. MoneyControl RSS — fast breaking news
7. Zerodha Pulse — 258+ articles/day aggregator
8. NSE Corp Announcements — exchange filings, most authoritative

Time window: previous day 4 PM to current day 9:10 AM IST (pre-market only)
"""
import requests, re, sys, time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.stdout.reconfigure(encoding='utf-8')

H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
IST = timezone(timedelta(hours=5, minutes=30))


def parse_rss_date(date_str):
    """Parse various RSS date formats to IST datetime."""
    if not date_str:
        return None
    date_str = re.sub(r'<!\[CDATA\[|\]\]>', '', date_str).strip()
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(IST)
    except:
        pass
    # Fallback formats
    for fmt in ['%d-%b-%Y %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d %b %Y %H:%M:%S']:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=IST)
        except:
            continue
    return None


def parse_relative_time(rel_str):
    """Parse Zerodha Pulse relative time ('2 hours ago') to IST datetime."""
    now = datetime.now(IST)
    rel_str = rel_str.strip().lower()
    m = re.search(r'(\d+)\s*(minute|hour|day|second)', rel_str)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit == 'second': return now - timedelta(seconds=val)
        if unit == 'minute': return now - timedelta(minutes=val)
        if unit == 'hour': return now - timedelta(hours=val)
        if unit == 'day': return now - timedelta(days=val)
    return now


def in_window(dt, window_start, window_end):
    """Check if datetime falls within the time window."""
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return window_start <= dt <= window_end


def fetch_rss(url, source_name, window_start, window_end):
    """Fetch and filter RSS feed by time window."""
    articles = []
    try:
        r = requests.get(url, headers=H, timeout=15)
        if r.status_code != 200:
            return articles

        titles = re.findall(r'<title[^>]*>(.*?)</title>', r.text, re.DOTALL)
        links = re.findall(r'<link>(https?://[^<]+)</link>', r.text)
        pubdates = re.findall(r'<pubDate>(.*?)</pubDate>', r.text, re.DOTALL)

        for i in range(min(len(pubdates), 100)):
            title = titles[i + 1] if i + 1 < len(titles) else ''
            title = re.sub(r'<!\[CDATA\[|\]\]>', '', title).strip()
            title = re.sub(r'<[^>]+>', '', title).strip()
            if not title:
                continue

            dt = parse_rss_date(pubdates[i])
            if not in_window(dt, window_start, window_end):
                continue

            link = links[i + 1] if i + 1 < len(links) else ''
            articles.append({
                'title': title,
                'published': dt.isoformat() if dt else '',
                'published_dt': dt,
                'source': source_name,
                'url': link,
            })
    except Exception as e:
        pass
    return articles


def fetch_google_news(date_str, window_start, window_end):
    """Google News RSS with date filtering."""
    import urllib.parse
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    after = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
    before = (dt + timedelta(days=1)).strftime('%Y-%m-%d')

    queries = [
        f'India stock market {dt.strftime("%B %d %Y")}',
        f'NSE BSE stock block deal order win after:{after} before:{before}',
        f'stock SEBI RBI regulatory India after:{after} before:{before}',
        f'stock acquisition merger demerger India after:{after} before:{before}',
        f'stock stake sale promoter India after:{after} before:{before}',
    ]

    articles = []
    seen = set()
    for q in queries:
        url = f'https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-IN&gl=IN&ceid=IN:en'
        try:
            r = requests.get(url, headers=H, timeout=15)
            if r.status_code != 200:
                continue
            titles = re.findall(r'<title[^>]*>(.*?)</title>', r.text, re.DOTALL)
            pubdates = re.findall(r'<pubDate>(.*?)</pubDate>', r.text, re.DOTALL)
            links = re.findall(r'<link>(https?://[^<]+)</link>', r.text)

            for i in range(1, min(len(titles), 30)):
                title = re.sub(r'<!\[CDATA\[|\]\]>', '', titles[i]).strip()
                title = re.sub(r'<[^>]+>', '', title).strip()
                if not title or title in seen:
                    continue

                dt_parsed = parse_rss_date(pubdates[i - 1]) if i - 1 < len(pubdates) else None
                if dt_parsed and not in_window(dt_parsed, window_start, window_end):
                    continue

                seen.add(title)
                link = links[i] if i < len(links) else ''
                articles.append({
                    'title': title,
                    'published': dt_parsed.isoformat() if dt_parsed else '',
                    'published_dt': dt_parsed,
                    'source': 'Google News',
                    'url': link,
                })
        except:
            pass
    return articles


def fetch_zerodha_pulse(window_start, window_end):
    """Zerodha Pulse — aggregates all Indian financial news."""
    articles = []
    try:
        r = requests.get('https://pulse.zerodha.com/', headers=H, timeout=10)
        if r.status_code != 200:
            return articles

        titles_raw = re.findall(r'<h2[^>]*class="title"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text)
        times_raw = re.findall(r'<span[^>]*class="date"[^>]*>(.*?)</span>', r.text)
        sources_raw = re.findall(r'<span[^>]*class="feed"[^>]*>(.*?)</span>', r.text)

        for i in range(min(len(titles_raw), 300)):
            title = re.sub(r'<[^>]+>', '', titles_raw[i][1]).strip()
            url = titles_raw[i][0]
            rel_time = re.sub(r'<[^>]+>', '', times_raw[i]).strip() if i < len(times_raw) else ''
            source = re.sub(r'<[^>]+>', '', sources_raw[i]).strip() if i < len(sources_raw) else 'Pulse'

            dt = parse_relative_time(rel_time)
            if not in_window(dt, window_start, window_end):
                continue

            articles.append({
                'title': title,
                'published': dt.isoformat() if dt else '',
                'published_dt': dt,
                'source': f'Pulse/{source}',
                'url': url,
            })
    except:
        pass
    return articles


def fetch_nse_announcements(window_start, window_end):
    """NSE Corporate Announcements — most authoritative source."""
    articles = []
    try:
        s = requests.Session()
        s.headers.update(H)
        s.get('https://www.nseindia.com', timeout=10)
        time.sleep(2)
        r = s.get('https://www.nseindia.com/api/corporate-announcements?index=equities', timeout=10)
        if r.status_code != 200:
            return articles

        for item in r.json():
            title = item.get('desc', '')
            symbol = item.get('symbol', '')
            an_dt = item.get('an_dt', '')

            dt = parse_rss_date(an_dt)
            if not in_window(dt, window_start, window_end):
                continue

            articles.append({
                'title': f'{symbol}: {title}',
                'published': dt.isoformat() if dt else an_dt,
                'published_dt': dt,
                'source': 'NSE Filing',
                'url': f'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
                'symbol': symbol,
            })
    except:
        pass
    return articles


def fetch_all_news(date_str=None, window_hours=17):
    """
    Fetch news from ALL sources within a time window.

    Default window: previous day 4 PM to current day 9:10 AM IST.
    This captures: evening news + overnight + pre-market morning.

    Args:
        date_str: Target trading date 'YYYY-MM-DD'. None = today.
        window_hours: How many hours back to look (default 17 = prev 4PM to 9AM).

    Returns:
        List of articles sorted by published time, deduplicated.
    """
    if date_str:
        target = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=IST)
    else:
        target = datetime.now(IST)

    # Window: previous day 4 PM to target day 9:10 AM
    window_end = target.replace(hour=9, minute=10, second=0)
    window_start = window_end - timedelta(hours=window_hours)

    all_articles = []

    # 1. Google News RSS (date-filtered, works for backtest)
    if date_str:
        all_articles.extend(fetch_google_news(date_str, window_start, window_end))

    # 2-6. RSS feeds (live only — no historical)
    rss_feeds = [
        ('https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms', 'ET Markets'),
        ('https://www.livemint.com/rss/markets', 'LiveMint'),
        ('https://www.business-standard.com/rss/markets-106.rss', 'Business Standard'),
        ('https://feeds.feedburner.com/ndtvprofit-latest', 'NDTV Profit'),
        ('https://www.moneycontrol.com/rss/latestnews.xml', 'MoneyControl'),
    ]
    for url, source in rss_feeds:
        all_articles.extend(fetch_rss(url, source, window_start, window_end))

    # 7. Zerodha Pulse (live only)
    all_articles.extend(fetch_zerodha_pulse(window_start, window_end))

    # 8. NSE Corporate Announcements (has dates, works for live)
    all_articles.extend(fetch_nse_announcements(window_start, window_end))

    # Deduplicate by title similarity
    seen = set()
    unique = []
    for a in all_articles:
        # Normalize title for dedup
        key = re.sub(r'[^a-zA-Z0-9]', '', a['title'].lower())[:60]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Sort by time (newest first)
    unique.sort(key=lambda x: x.get('published', ''), reverse=True)

    return unique


def filter_stock_news(articles):
    """Light filter — remove obviously non-stock articles."""
    skip = ['cricket', 'bollywood', 'weather', 'election poll', 'horoscope',
            'recipe', 'movie', 'ipl', 'world cup', 'entertainment']
    return [a for a in articles if not any(s in a['title'].lower() for s in skip)]


if __name__ == '__main__':
    # Test: fetch today's news
    articles = fetch_all_news()
    filtered = filter_stock_news(articles)

    print(f'Total: {len(articles)} | Filtered: {len(filtered)}')
    print()

    # By source
    from collections import Counter
    sources = Counter(a['source'] for a in filtered)
    for s, c in sources.most_common():
        print(f'  {s:>25}: {c}')
    print()

    # Show first 20
    for a in filtered[:20]:
        t = a.get('published_dt')
        time_str = t.strftime('%H:%M') if t else '??:??'
        print(f'  [{time_str}] [{a["source"][:15]:>15}] {a["title"][:80]}')
