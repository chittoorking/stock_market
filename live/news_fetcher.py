"""
Fetch timestamped Indian stock market news from Google News RSS with date filtering.
Scrapes article content for full story — not just headlines.
"""
import requests, re, urllib.parse, sys, time
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def fetch_pulse():
    """Fetch latest news from Zerodha Pulse — aggregates all Indian financial news.
    Returns list of dicts with: title, published, source, url
    """
    all_articles = []
    try:
        r = requests.get('https://pulse.zerodha.com/', headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return all_articles

        articles = re.findall(r'<h2[^>]*class="title"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text)
        times_raw = re.findall(r'<span[^>]*class="date"[^>]*>(.*?)</span>', r.text)
        sources_raw = re.findall(r'<span[^>]*class="feed"[^>]*>(.*?)</span>', r.text)

        for i in range(min(len(articles), 258)):
            title = re.sub(r'<[^>]+>', '', articles[i][1]).strip()
            url = articles[i][0]
            t = re.sub(r'<[^>]+>', '', times_raw[i]).strip() if i < len(times_raw) else ''
            src = re.sub(r'<[^>]+>|&mdash;', '', sources_raw[i]).strip() if i < len(sources_raw) else ''

            if not title:
                continue

            all_articles.append({
                'title': title,
                'published': t,
                'published_dt': None,
                'source': src,
                'url': url,
            })
    except:
        pass

    return all_articles


def fetch_live_rss():
    """Fetch latest news from ET Markets + LiveMint RSS — real-time during market hours.
    Returns list of dicts with: title, published, source, url
    """
    feeds = [
        ('ET Markets', 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms'),
        ('LiveMint', 'https://www.livemint.com/rss/markets'),
    ]
    all_articles = []
    seen_titles = set()

    for source_name, feed_url in feeds:
        try:
            r = requests.get(feed_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            titles = re.findall(r'<title[^>]*>(.*?)</title>', r.text, re.DOTALL)
            links = re.findall(r'<link>(https?://[^<]+)</link>', r.text)
            pubdates = re.findall(r'<pubDate>(.*?)</pubDate>', r.text)

            for i in range(min(len(pubdates), 30)):
                title = titles[i + 1] if i + 1 < len(titles) else ''
                title = re.sub(r'<!\[CDATA\[|\]\]>', '', title).strip()
                title = re.sub(r'<[^>]+>', '', title).strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                pub = pubdates[i] if i < len(pubdates) else ''
                link = links[i + 1] if i + 1 < len(links) else ''
                pub_dt = None
                try:
                    pub_dt = datetime.strptime(pub[:25], '%a, %d %b %Y %H:%M:%S')
                except:
                    pass
                all_articles.append({
                    'title': title, 'published': pub,
                    'published_dt': pub_dt.isoformat() if pub_dt else None,
                    'source': source_name, 'url': link,
                })
        except:
            pass

    all_articles.sort(key=lambda x: x.get('published_dt') or '', reverse=True)
    return all_articles


def fetch_news(date_str, queries=None):
    """Fetch news for a specific date using Google News RSS with date operators.

    Args:
        date_str: Target date 'YYYY-MM-DD'. News from previous evening + pre-market morning.
        queries: List of search queries. Default covers stocks, block deals, results.

    Returns:
        List of dicts with: title, published, source, url
    """
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    # Window: previous day (for evening news) to target date (for pre-market)
    after = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
    before = (dt + timedelta(days=1)).strftime('%Y-%m-%d')

    if queries is None:
        # Broad queries — don't filter, let Claude decide what matters
        dt_str = dt.strftime('%B %d %Y')  # e.g. "August 09 2024"
        queries = [
            f'India stock market {dt_str}',
            f'NSE BSE shares {dt_str}',
            f'Indian stocks news {dt_str}',
            f'moneycontrol stock market {dt_str}',
            f'economic times markets {dt_str}',
            f'livemint stock {dt_str}',
        ]

    all_articles = []
    seen_titles = set()

    for q in queries:
        search = urllib.parse.quote(f'{q} after:{after} before:{before}')
        url = f'https://news.google.com/rss/search?q={search}&hl=en-IN&gl=IN&ceid=IN:en'

        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue

            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', r.text)
            links = re.findall(r'<link>(https://news\.google\.com/rss/articles/.*?)</link>', r.text)
            pubdates = re.findall(r'<pubDate>(.*?)</pubDate>', r.text)
            sources = re.findall(r'<source[^>]*>(.*?)</source>', r.text)

            for i in range(min(len(pubdates), 15)):
                title = titles[i + 1][0] or titles[i + 1][1] if i + 1 < len(titles) else ''
                title = re.sub(r'<[^>]+>', '', title).strip()

                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                pub = pubdates[i] if i < len(pubdates) else ''
                source = sources[i] if i < len(sources) else ''
                link = links[i] if i < len(links) else ''

                # Parse date
                pub_dt = None
                try:
                    pub_dt = datetime.strptime(pub[:25], '%a, %d %b %Y %H:%M:%S')
                except:
                    pass

                # Filter: only include articles from previous evening to target morning
                if pub_dt:
                    # Previous day after 15:30 IST (market close) to target day 09:15 IST (market open)
                    prev_evening = dt - timedelta(days=1)
                    prev_evening = prev_evening.replace(hour=10, minute=0)  # UTC ~15:30 IST
                    target_morning = dt.replace(hour=3, minute=45)  # UTC ~09:15 IST

                    # For pre-market: allow from previous day any time to target date 09:15 AM IST
                    # For post-market reactions: same day after 3:30 PM
                    # Broad filter: anything dated on prev_day or target_day
                    if pub_dt.date() < (dt - timedelta(days=1)).date():
                        continue  # Too old
                    if pub_dt.date() > dt.date():
                        continue  # Future leakage

                all_articles.append({
                    'title': title,
                    'published': pub,
                    'published_dt': pub_dt.isoformat() if pub_dt else None,
                    'source': source,
                    'url': link,
                    'query': q,
                })

        except Exception as e:
            print(f"  Error fetching '{q[:30]}...': {e}")

    # Sort by date
    all_articles.sort(key=lambda x: x.get('published_dt') or '', reverse=True)
    return all_articles


def scrape_articles_by_index(articles, indices):
    """Scrape content only for selected article indices."""
    for i in indices:
        if 0 <= i < len(articles):
            content = scrape_article(articles[i].get('url', ''))
            articles[i]['content'] = content
            time.sleep(0.5)
    return articles


def scrape_article(google_news_url):
    """Follow Google News redirect and scrape article text."""
    if not google_news_url:
        return ''
    try:
        # Google News RSS URLs redirect to the actual article
        r = requests.get(google_news_url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return ''

        html = r.text
        # Remove scripts, styles, nav, footer
        html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<nav[^>]*>[\s\S]*?</nav>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>[\s\S]*?</footer>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<header[^>]*>[\s\S]*?</header>', '', html, flags=re.IGNORECASE)

        # Extract text from paragraph tags
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        text = '\n'.join(re.sub(r'<[^>]+>', '', p).strip() for p in paragraphs if len(p) > 30)

        # Cap at ~1500 chars to keep token budget reasonable
        if len(text) > 1500:
            text = text[:1500] + '...'

        return text
    except Exception:
        return ''


def filter_stock_news(articles):
    """Light filter — remove obviously non-stock articles. Let Claude decide the rest."""
    skip = ['cricket', 'bollywood', 'weather', 'election poll', 'horoscope', 'recipe']
    return [a for a in articles if not any(s in a['title'].lower() for s in skip)]


if __name__ == '__main__':
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y-%m-%d')
    print(f'Fetching news for: {date}')
    print('=' * 70)

    articles = fetch_news(date)
    print(f'Total articles found: {len(articles)}')

    filtered = filter_stock_news(articles)
    print(f'Stock-relevant articles: {len(filtered)}')
    print()

    for a in filtered:
        print(f"  [{a['published'][:25]}] [{a['source']}]")
        print(f"  {a['title']}")
        print()
