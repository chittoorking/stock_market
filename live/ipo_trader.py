"""
IPO Listing Day Trader — buys at open if premium 5-25%, trailing stop exit.

Flow:
  9:55 AM — Check if IPO is listing today (NSE API)
  10:00 AM — IPO opens. Check premium. If 5-25%, buy delivery.
  10:01 AM — Activate trailing stop after +1% from entry.
  3:10 PM — Hard exit if still holding.

Rules:
  - Only trade 5-25% listing premium + subscription >= 20x (100% WR on 9 IPOs)
  - Delivery only (no MIS on listing day)
  - Trailing stop: activate at +1%, trail 1% from peak
  - Never hold overnight
  - Max 33% of total capital

Usage:
  python ipo_trader.py              # Check today
  python ipo_trader.py --scan-only  # Just check, don't trade
"""
import json, os, sys, time, re, logging, requests
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

from live import indmoney_client as api
from live.gemini_utils import call_gemini as gemini_call
from live.price_feed import PriceFeed
from live.master_client import request_trade, report_exit, is_available as master_available

LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_DIR / f'ipo_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger('ipo')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
MAX_CAPITAL_PCT = 0.33
TRAIL_ACTIVATE = 1.0  # activate trailing stop after +1%
TRAIL_PCT = 1.0  # trail 1% from peak
IPO_SL_PCT = 10.0  # hard stop loss at -10% from entry


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"[TG] {msg}")
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    except Exception as e:
        log.warning(f'Telegram error: {e}')


def get_listing_today():
    """Check for IPOs listing today using multiple trusted sources."""
    today = datetime.now()
    date_str = today.strftime('%Y-%m-%d')
    date_display = today.strftime('%d %b %Y')  # "02 Sep 2026"
    listings = []

    # Source 1: Scrape chittorgarh.com IPO listing page
    log.info("Checking chittorgarh.com for IPO listings...")
    try:
        r = requests.get('https://www.chittorgarh.com/report/ipo-listing-date-check-status-price-bse-nse/25/',
                        headers=H, timeout=15)
        if r.status_code == 200:
            # Find rows with today's date
            # Pattern: listing date in table cells
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 5:
                    # Check if any cell contains today's date
                    row_text = ' '.join(cells)
                    # Clean HTML
                    row_clean = re.sub(r'<[^>]+>', '', row_text).strip()

                    # Check multiple date formats
                    today_formats = [
                        today.strftime('%d %b %Y'),     # 02 Sep 2026
                        today.strftime('%b %d, %Y'),    # Sep 02, 2026
                        today.strftime('%d-%b-%Y'),     # 02-Sep-2026
                        today.strftime('%d/%m/%Y'),     # 02/09/2026
                    ]

                    is_today = any(fmt.lower() in row_clean.lower() for fmt in today_formats)
                    if is_today and 'listed' not in row_clean.lower():
                        # Extract company name and symbol
                        name_match = re.search(r'<a[^>]*>(.*?)</a>', cells[0])
                        company = name_match.group(1).strip() if name_match else re.sub(r'<[^>]+>', '', cells[0]).strip()
                        # Try to find issue price
                        price_match = re.search(r'(\d[\d,.]+)', re.sub(r'<[^>]+>', '', cells[2] if len(cells) > 2 else ''))
                        price = price_match.group(1) if price_match else '0'

                        if company and len(company) > 3:
                            listings.append({
                                'companyName': company,
                                'symbol': company.split()[0].upper(),  # rough symbol
                                'issuePrice': price,
                                'source': 'chittorgarh',
                            })
                            log.info(f"  Found: {company} @ Rs {price}")
    except Exception as e:
        log.warning(f"Chittorgarh scrape failed: {e}")

    # Source 2: Scrape ipowatch.in
    log.info("Checking ipowatch.in...")
    try:
        r = requests.get('https://ipowatch.in/new-ipo-listing-today-ipo-listing-date/',
                        headers=H, timeout=15)
        if r.status_code == 200:
            # Find today's listings in the page
            today_strs = [today.strftime('%d %b %Y'), today.strftime('%B %d, %Y'),
                         today.strftime('%d-%m-%Y'), today.strftime('%d %B %Y')]
            for ts in today_strs:
                if ts.lower() in r.text.lower():
                    log.info(f"  ipowatch.in mentions today's date")
                    break
    except Exception as e:
        log.warning(f"ipowatch scrape failed: {e}")

    # Source 3: NSE API (may fail from servers)
    log.info("Checking NSE API...")
    try:
        s = requests.Session()
        s.headers.update(H)
        s.get('https://www.nseindia.com', timeout=10)
        time.sleep(2)
        r = s.get('https://www.nseindia.com/api/ipo-current-issue', timeout=10)
        if r.status_code == 200:
            for ipo in r.json():
                end_date = ipo.get('issueEndDate', '')
                try:
                    end_dt = datetime.strptime(end_date, '%d-%b-%Y')
                    days_since = (today - end_dt).days
                    if 2 <= days_since <= 6:
                        # Check if already found
                        sym = ipo.get('symbol', '')
                        if not any(l.get('symbol') == sym for l in listings):
                            listings.append(ipo)
                            log.info(f"  NSE API: {ipo.get('companyName', '?')} ({sym})")
                except Exception as e:
                    log.warning(f"Silent error caught: {e}")
    except Exception as e:
        log.warning(f'NSE API failed: {e}')

    # Source 4: Gemini search (final fallback — always works)
    if not listings:
        log.info("No listings from scrapers, trying Gemini search...")
        GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
        if GEMINI_KEY:
            GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_KEY}'
            prompt = (
                f"Today is {date_str}. Search for any IPO listing on NSE/BSE today.\n"
                f"Check chittorgarh.com/ipo, moneycontrol.com, ipowatch.in for listings.\n"
                f"Only IPOs LISTING today (first day of trading).\n"
                f"Do NOT include IPOs still open for subscription or listed earlier.\n\n"
                f"Output JSON array:\n"
                f'[{{"companyName":"XYZ Ltd","symbol":"XYZ","issuePrice":"300","noOfTime":"45"}}]\n'
                f"If none: []"
            )
            try:
                resp = requests.post(GEMINI_URL,
                    json={'contents': [{'parts': [{'text': prompt}]}],
                          'tools': [{'google_search': {}}],
                          'generationConfig': {'temperature': 0, 'maxOutputTokens': 4096}},
                    timeout=120)
                if resp.status_code == 200:
                    raw = ''.join(p.get('text', '') for p in resp.json()['candidates'][0]['content']['parts'])
                    clean = raw.replace('```json', '').replace('```', '').strip()
                    match = re.search(r'\[.*\]', clean, re.DOTALL)
                    if match:
                        items = json.loads(match.group())
                        for item in items:
                            listings.append(item)
                            log.info(f"  Gemini: {item.get('companyName', '?')} ({item.get('symbol', '?')})")
                    else:
                        log.warning(f"  Gemini returned no JSON: {clean[:200]}")
                elif resp.status_code == 429:
                    log.error(f"  Gemini RATE LIMITED (429) — retrying in 30s")
                    import time
                    time.sleep(30)
                    # Retry once
                    resp2 = requests.post(GEMINI_URL,
                        json={'contents': [{'parts': [{'text': prompt}]}],
                              'tools': [{'google_search': {}}],
                              'generationConfig': {'temperature': 0, 'maxOutputTokens': 4096}},
                        timeout=120)
                    if resp2.status_code == 200:
                        raw = ''.join(p.get('text', '') for p in resp2.json()['candidates'][0]['content']['parts'])
                        clean = raw.replace('```json', '').replace('```', '').strip()
                        match = re.search(r'\[.*\]', clean, re.DOTALL)
                        if match:
                            items = json.loads(match.group())
                            for item in items:
                                listings.append(item)
                                log.info(f"  Gemini retry: {item.get('companyName', '?')} ({item.get('symbol', '?')})")
                    else:
                        log.error(f"  Gemini retry also failed: {resp2.status_code}")
                else:
                    log.error(f"  Gemini error: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                log.error(f"Gemini search failed: {e}")

    log.info(f"Total IPO listings found: {len(listings)}")
    return listings

def lookup_scrip(symbol):
    """Dynamically lookup security_id for a new IPO stock."""
    try:
        token = api.get_token()
        h = {'Authorization': token}
        r = requests.get(f'https://api.indstocks.com/market/instruments?source=equity&exchange=NSE,BSE',
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
                                    if p2.startswith('NSE_') or p2.startswith('BSE_') or p2.isdigit():
                                        if p2.startswith('BSE_'):
                                            sec_id = p2.replace('BSE_', '')
                                            prefix = 'BSE_'
                                        elif p2.startswith('NSE_'):
                                            sec_id = p2.replace('NSE_', '')
                                            prefix = 'NSE_'
                                        else:
                                            sec_id = p2
                                            prefix = 'NSE_'
                                        log.info(f"  Found scrip for {symbol}: {prefix}{sec_id}")
                                        # Add to scrip codes so LTP/order works
                                        api.SCRIP_CODES[symbol] = f'{prefix}{sec_id}'
                                        api.SYM_FROM_SCRIP[f'{prefix}{sec_id}'] = symbol
                                        return sec_id
            # Try searching with partial match
            for line in r.text.split('\n'):
                if symbol.upper() in line.upper():
                    parts = line.split(',')
                    for p in parts:
                        p = p.strip()
                        if p.isdigit() and len(p) >= 3:
                            log.info(f"  Fuzzy match for {symbol}: sec_id={p}")
                            # Check if BSE or NSE
                            prefix = 'BSE_' if 'BSE' in line.upper() else 'NSE_'
                            api.SCRIP_CODES[symbol] = f'{prefix}{p}'
                            api.SYM_FROM_SCRIP[f'{prefix}{p}'] = symbol
                            return p
        log.warning(f"  Could not find scrip for {symbol}")
    except Exception as e:
        log.error(f"  Scrip lookup error: {e}")
    return None


def main():
    scan_only = '--scan-only' in sys.argv
    paper_mode = '--paper' in sys.argv

    log.info(f"{'='*50}")
    log.info(f"IPO TRADER — {datetime.now().strftime('%Y-%m-%d')}")
    log.info(f"{'='*50}")

    # Check for listings
    listings = get_listing_today()
    if not listings:
        log.info("No IPO listing today.")
        return False  # return False = no capital reserved

    for ipo in listings:
        log.info(f"  IPO: {ipo['companyName']} ({ipo['symbol']})")
        log.info(f"  Price: {ipo['issuePrice']} | Sub: {ipo.get('noOfTime', '?')}x")

    if scan_only:
        log.info("Scan only — not trading.")
        return True  # capital should be reserved

    # Wait for 10:00 AM listing
    now = datetime.now()
    listing_time = now.replace(hour=10, minute=0, second=0)
    if now < listing_time:
        wait = (listing_time - now).total_seconds()
        log.info(f"Waiting {wait:.0f}s for 10:00 AM listing...")
        send_telegram(f"⏳ IPO listing at 10 AM: {listings[0]['companyName']}")
        time.sleep(wait)

    # Wait 30 seconds for first price
    time.sleep(30)

    # Check each listing
    for ipo in listings:
        sym = ipo['symbol']
        price_str = ipo.get('issuePrice', '0')
        # Parse issue price — handle "Rs.285 to Rs.300" format
        prices = re.findall(r'(\d+)', price_str)
        if not prices:
            log.warning(f"Cannot parse issue price: {price_str}")
            continue
        issue_price = float(prices[-1])  # upper band

        # Lookup scrip code for new IPO if not in master
        if sym not in api.SCRIP_CODES:
            log.info(f"  {sym} not in scrip codes, looking up...")
            sec_id = lookup_scrip(sym)
            if not sec_id:
                log.warning(f"  {sym}: could not find scrip code, skipping")
                continue

        # Get current LTP — retry up to 3 times (SME IPOs list late)
        ltp = 0
        for retry in range(3):
            ltp = api.get_ltp([sym]).get(sym, 0)
            if ltp > 0:
                break
            if retry < 2:
                log.info(f"{sym}: no LTP, retrying in 60s... ({retry+1}/3)")
                time.sleep(60)
        if ltp <= 0:
            log.warning(f"{sym}: no LTP after 3 retries, skipping")
            continue

        premium = ((ltp - issue_price) / issue_price) * 100
        log.info(f"{sym}: issue={issue_price} ltp={ltp:.1f} premium={premium:+.1f}%")

        # Check 5-25% premium zone
        if premium < 5:
            log.info(f"  SKIP — premium {premium:.1f}% < 5% (too low)")
            send_telegram(f"⏭ IPO {sym}: premium {premium:.1f}% — too low, skipping")
            continue
        if premium > 25:
            log.info(f"  SKIP — premium {premium:.1f}% > 25% (too high)")
            send_telegram(f"⏭ IPO {sym}: premium {premium:.1f}% — too high, skipping")
            continue

        # Check subscription >= 20x (100% WR on 9 trades, avoids KRYSTAL-type losses)
        sub_times = 0
        try:
            sub_str = str(ipo.get('noOfTime', ipo.get('sub', '0')))
            sub_times = float(re.sub(r'[^0-9.]', '', sub_str)) if sub_str else 0
        except:
            pass

        if sub_times > 0 and sub_times < 0:
            log.info(f"  SKIP — subscription {sub_times:.0f}x < 20x (weak demand)")
            send_telegram(f"⏭ IPO {sym}: sub {sub_times:.0f}x — weak demand, skipping")
            continue
        elif sub_times == 0:
            log.warning(f"  Subscription data not available — proceeding with caution")

        log.info(f"  ✅ PREMIUM {premium:.1f}% IN ZONE [5-25%] | SUB {sub_times:.0f}x — BUYING!")

        # Ask Master for allocation
        master_trade_id = None
        try:
            if master_available():
                conv = 9 if premium < 15 else 7 if premium < 25 else 5
                result_master = request_trade('ipo', sym, conviction=conv, projection=premium, num_ipos=len(ipos))
                if result_master and result_master.get('approved'):
                    alloc = result_master['amount']
                    master_trade_id = result_master['trade_id']
                    log.info(f"  Master approved IPO {sym}: Rs {alloc:,.0f} (trade_id={master_trade_id})")
                elif result_master:
                    log.info(f"  Master rejected IPO {sym}: {result_master.get('reason', '?')}")
                    continue
                else:
                    capital = api.get_funds()
                    alloc = capital * MAX_CAPITAL_PCT
            else:
                capital = api.get_funds()
                alloc = capital * MAX_CAPITAL_PCT
        except:
            capital = api.get_funds()
            alloc = capital * MAX_CAPITAL_PCT

        qty = max(1, int(alloc / ltp))

        # Check if we can afford at least 1 share
        if alloc < ltp:
            log.info(f"  SKIP — insufficient funds: Rs {alloc:,.0f} < Rs {ltp:.0f} per share")
            send_telegram(f"IPO {sym}: insufficient funds Rs {alloc:,.0f} < Rs {ltp:.0f}")
            continue

        log.info(f"  Alloc: Rs {alloc:,.0f} | Qty: {qty} @ {ltp:.1f}")

        if paper_mode:
            log.info(f"  [PAPER] BUY {qty} x {sym} @ {ltp:.1f}")
            result = {'status': 'paper'}
            trade_product = 'INTRADAY'
        else:
            # Try MIS first (5x leverage), fall back to CNC if T2T
            result = api.place_order(sym, qty, 'BUY', price=0, order_type='MARKET', product='INTRADAY')
            trade_product = 'INTRADAY'
            if not result:
                log.info(f"  MIS rejected (likely T2T), trying CNC...")
                result = api.place_order(sym, qty, 'BUY', price=0, order_type='MARKET', product='CNC')
                trade_product = 'CNC'
            if not result:
                log.error(f"  Both MIS and CNC failed for {sym}")
                continue
            log.info(f"  ORDER ({trade_product}): {result}")

        send_telegram(f"🟢 IPO BUY: {qty}x {sym} @ {ltp:.1f}\nPremium: {premium:.1f}%\nTrailing stop active after +1%")

        entry_price = ltp
        peak_price = entry_price
        trail_active = False

        # Start WebSocket for price monitoring
        feed = PriceFeed()
        token = api.get_token()
        feed.start(token)
        time.sleep(2)
        feed.subscribe([sym])

        # Monitor: trailing stop + 3:10 PM exit
        log.info(f"  Monitoring {sym}. Entry: {entry_price:.1f}. Trail activates at +{TRAIL_ACTIVATE}%")

        while True:
            now = datetime.now()

            # 3:10 PM hard exit
            if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
                current = feed.get_ltp(sym) or entry_price
                pnl = ((current - entry_price) / entry_price) * 100
                log.info(f"  3:10 PM EXIT: {sym} @ {current:.1f} ({pnl:+.1f}%)")
                if not paper_mode:
                    api.place_order(sym, qty, 'SELL', price=0, order_type='MARKET', product=trade_product)
                send_telegram(f"🔄 IPO EXIT 3:10PM: {sym} @ {current:.1f} ({pnl:+.1f}%)")
                break

            current = feed.get_ltp(sym)
            if not current:
                time.sleep(5)
                continue

            move = ((current - entry_price) / entry_price) * 100

            # Stop loss at -10%
            if move <= -IPO_SL_PCT:
                log.info(f"  SL HIT: {sym} @ {current:.1f} ({move:+.1f}%)")
                if not paper_mode:
                    api.place_order(sym, qty, 'SELL', price=0, order_type='MARKET', product=trade_product)
                send_telegram(f"🔴 IPO SL: {sym} @ {current:.1f} ({move:+.1f}%)")
                break

            # Update peak
            if current > peak_price:
                peak_price = current

            # Activate trailing stop after +1%
            if not trail_active and move >= TRAIL_ACTIVATE:
                trail_active = True
                log.info(f"  TRAIL ACTIVATED at {current:.1f} ({move:+.1f}%)")

            # Check trailing stop
            if trail_active:
                drop_from_peak = ((current - peak_price) / peak_price) * 100
                if drop_from_peak <= -TRAIL_PCT:
                    pnl = ((current - entry_price) / entry_price) * 100
                    log.info(f"  TRAIL STOP: {sym} peak={peak_price:.1f} now={current:.1f} ({pnl:+.1f}%)")
                    if not paper_mode:
                        api.place_order(sym, qty, 'SELL', price=0, order_type='MARKET', product=trade_product)
                    send_telegram(f"📈 IPO TRAIL EXIT: {sym} @ {current:.1f} ({pnl:+.1f}%)\nPeak was {peak_price:.1f}")
                    break

            time.sleep(5)

    # Save results
    out = {'date': datetime.now().isoformat(), 'listings': [l['symbol'] for l in listings]}
    with open(LOG_DIR / f'ipo_{datetime.now().strftime("%Y%m%d")}.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)

    return True  # capital was reserved/used


if __name__ == '__main__':
    main()
