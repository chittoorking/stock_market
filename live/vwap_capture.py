"""VWAP Capture Bot — NO TRADING. Captures all 94 ML fields every scan.
Sends signal alerts to Telegram. Saves full data for backtesting."""
import json, re, time, requests, logging, os, sys, gzip, fcntl
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

# PID lock — prevent duplicate instances
_LOCK_FILE = os.path.expanduser('~/vwap_capture.lock')
_lock_fp = open(_LOCK_FILE, 'w')
try:
    fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fp.write(str(os.getpid()))
    _lock_fp.flush()
except BlockingIOError:
    print('Capture bot already running. Exiting.')
    sys.exit(0)
from curl_cffi import requests as cf_req

# === CONFIG ===
TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'
VWAP_DIST_MIN = 2.0       # 2% threshold from convergence
POLL_INTERVAL = 60
MIN_PRICE = 50
DELIVERY_MIN = 43.55       # from convergence: deliveryPercentage > 43.55

LOG_DIR = Path(__file__).parent / 'vwap_logs'
LOG_DIR.mkdir(exist_ok=True)

today = datetime.now().strftime('%Y%m%d')
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s',
    handlers=[logging.StreamHandler(),
              logging.FileHandler(LOG_DIR / f'capture_{today}.log')])
log = logging.getLogger('capture')

DATA_FILE = LOG_DIR / f'ml_data_{today}.jsonl'
SIGNAL_FILE = LOG_DIR / f'signals_{today}.jsonl'

FNO_STOCKS = {
    'RELIANCE','TCS','HDFCBANK','INFY','ICICIBANK','SBIN','BHARTIARTL','ITC',
    'KOTAKBANK','LT','AXISBANK','TATAMOTORS','MARUTI','SUNPHARMA','TITAN',
    'WIPRO','BAJFINANCE','HCLTECH','ADANIENT','TATASTEEL','ONGC','NTPC',
    'POWERGRID','COALINDIA','BPCL','HINDALCO','JSWSTEEL','TECHM','INDUSINDBK',
    'GRASIM','BAJAJFINSV','NESTLEIND','ULTRACEMCO','APOLLOHOSP','DRREDDY',
    'CIPLA','TATACONSUM','ASIANPAINT','DIVISLAB','EICHERMOT','HEROMOTOCO',
    'BRITANNIA','SHRIRAMFIN','PIDILITIND','SBILIFE','HDFCLIFE','DABUR',
    'GODREJCP','HAVELLS','SIEMENS','ABB','ACC','AMBUJACEM','AUROPHARMA',
    'BANKBARODA','BEL','BHEL','BIOCON','BOSCHLTD','CANBK','CHOLAFIN','COLPAL',
    'CONCOR','COROMANDEL','CROMPTON','CUMMINSIND','DLF','ESCORTS','EXIDEIND',
    'FEDERALBNK','GAIL','GLENMARK','GMRINFRA','GNFC','GODREJPROP','GRANULES',
    'GUJGASLTD','HAL','HINDCOPPER','HINDPETRO','IDFCFIRSTB','IEX','INDHOTEL',
    'INDUSTOWER','IRCTC','JINDALSTEL','JUBLFOOD','LALPATHLAB','LICHSGFIN',
    'LTF','LTIM','LUPIN','MANAPPURAM','MCX','MFSL','MGL','MOTHERSON',
    'MPHASIS','MUTHOOTFIN','NATIONALUM','NAUKRI','NAVINFLUOR','NMDC',
    'OBEROIRLTY','OFSS','OIL','PAGEIND','PEL','PERSISTENT','PETRONET',
    'PFC','PIIND','PNB','POLYCAB','PVRINOX','RAMCOCEM','RBLBANK','RECLTD',
    'SAIL','SBICARD','SHREECEM','SRF','SUNTV','SYNGENE','TATACHEM',
    'TATACOMM','TATAPOWER','TORNTPHARM','TRENT','UBL','UNIONBANK','UPL',
    'VEDL','VOLTAS','ZYDUSLIFE','BHARATFORG','DELHIVERY','IRFC','JKCEMENT',
    'KAYNES','KPITTECH','LICI','MAXHEALTH','NHPC','RVNL','SUPREMEIND',
    'TVSMOTOR','ZOMATO','POLICYBZR','MARICO','BERGEPAINT','DIXON',
    'DEEPAKNTR','COFORGE','ASTRAL','ATUL','AFFLE','ABCAPITAL','AUBANK',
    'BANDHANBNK','WHIRLPOOL','BSE','NOCIL','CHENNPETRO','SSWL','ZEEL',
    'PROTEAN','APTUS','GREAVESCOT',
}


MARKET_FILE = LOG_DIR / f'market_context_{today}.jsonl'
PREOPEN_FILE = LOG_DIR / f'preopen_{today}.jsonl'
DEPTH_FILE = LOG_DIR / f'depth_{today}.jsonl'
ARCHIVE_DIR = Path('/mnt/data/ml_archive')


def _archive_day(filepath, logger):
    """Compress file to gz, move to /mnt/data/ml_archive/, delete original."""
    if not filepath.exists() or filepath.stat().st_size == 0:
        return
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        gz_name = filepath.name + '.gz'
        gz_path = ARCHIVE_DIR / gz_name
        with open(filepath, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
            while True:
                chunk = f_in.read(65536)
                if not chunk:
                    break
                f_out.write(chunk)
        orig_size = filepath.stat().st_size
        gz_size = gz_path.stat().st_size
        filepath.unlink()  # delete original
        logger.info(f"Archived {filepath.name} -> {gz_path} ({orig_size//1024}KB -> {gz_size//1024}KB)")
    except Exception as e:
        logger.error(f"Archive failed {filepath.name}: {e}")

def send_tg(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
    except: pass


class MarketContext:
    """Fetch NIFTY, VIX, advance/decline, FII/DII from NSE."""

    def __init__(self):
        self.nse = cf_req.Session(impersonate="chrome")
        try:
            self.nse.get('https://www.nseindia.com', timeout=10)
        except:
            pass
        self.last_data = {}

    def _get(self, url):
        """GET with auto re-init on failure."""
        try:
            r = self.nse.get(url, timeout=10)
            if r.status_code == 200:
                return r.json()
            # Re-init
            self.nse = cf_req.Session(impersonate="chrome")
            self.nse.get('https://www.nseindia.com', timeout=10)
            r = self.nse.get(url, timeout=10)
            return r.json() if r.status_code == 200 else None
        except:
            return None

    def poll(self):
        try:
            ctx = {}

            # 1. ALL INDICES — 139 indices with OHLC, PE, PB, adv/dec, returns
            d = self._get('https://www.nseindia.com/api/allIndices')
            if d:
                ctx['global_adv'] = d.get('advances', 0)
                ctx['global_dec'] = d.get('declines', 0)
                ctx['global_unch'] = d.get('unchanged', 0)

                idx_map = {
                    'NIFTY 50': 'nifty', 'NIFTY BANK': 'banknifty', 'INDIA VIX': 'vix',
                    'NIFTY MIDCAP SELECT': 'midcap', 'NIFTY SMALLCAP 100': 'smallcap',
                    'NIFTY IT': 'nifty_it', 'NIFTY AUTO': 'nifty_auto',
                    'NIFTY METAL': 'nifty_metal', 'NIFTY PHARMA': 'nifty_pharma',
                    'NIFTY REALTY': 'nifty_realty', 'NIFTY ENERGY': 'nifty_energy',
                    'NIFTY FMCG': 'nifty_fmcg', 'NIFTY PSE': 'nifty_pse',
                    'NIFTY FINANCIAL SERVICES': 'nifty_fin',
                    'NIFTY NEXT 50': 'nifty_next50', 'NIFTY MEDIA': 'nifty_media',
                    'NIFTY 500': 'nifty500', 'NIFTY TOTAL MARKET': 'nifty_total',
                }
                for idx in d.get('data', []):
                    name = idx.get('index', '')
                    prefix = idx_map.get(name)
                    if not prefix:
                        continue
                    if name == 'INDIA VIX':
                        ctx['vix'] = idx.get('last', 0)
                        ctx['vix_chg'] = idx.get('percentChange', 0)
                    else:
                        ctx[prefix] = idx.get('last', 0)
                        ctx[f'{prefix}_chg'] = idx.get('percentChange', 0)
                        ctx[f'{prefix}_open'] = idx.get('open', 0)
                        ctx[f'{prefix}_high'] = idx.get('high', 0)
                        ctx[f'{prefix}_low'] = idx.get('low', 0)
                        ctx[f'{prefix}_prev'] = idx.get('previousClose', 0)
                        if name == 'NIFTY 50':
                            ctx['nifty_pe'] = idx.get('pe', 0)
                            ctx['nifty_pb'] = idx.get('pb', 0)
                            ctx['nifty_dy'] = idx.get('dy', 0)
                            ctx['nifty_adv'] = idx.get('advances', 0)
                            ctx['nifty_dec'] = idx.get('declines', 0)
                            ctx['nifty_30d_chg'] = idx.get('perChange30d', 0)
                            ctx['nifty_365d_chg'] = idx.get('perChange365d', 0)

            # 2. MARKET STATUS — market cap, GIFT Nifty, USDINR
            d2 = self._get('https://www.nseindia.com/api/marketStatus')
            if d2:
                mc = d2.get('marketcap', {})
                ctx['market_cap_lakh_cr'] = mc.get('marketCapinLACCRRupees', 0)
                gift = d2.get('giftnifty', {})
                if gift:
                    ctx['gift_nifty'] = gift.get('last', 0)
                    ctx['gift_nifty_chg'] = gift.get('change', 0)
                usd = d2.get('niftyusd', {})
                if usd:
                    ctx['nifty_usd'] = usd.get('last', 0)
                # USDINR from currency futures
                for ms in d2.get('marketState', []):
                    if ms.get('underlying') == 'USDINR':
                        ctx['usdinr'] = float(ms.get('last', 0) or 0)

            # 3. FII/DII flows
            d3 = self._get('https://www.nseindia.com/api/fiidiiTradeReact')
            if d3:
                for item in d3:
                    cat = item.get('category', '')
                    if 'FII' in cat or 'FPI' in cat:
                        ctx['fii_buy'] = float(item.get('buyValue', 0))
                        ctx['fii_sell'] = float(item.get('sellValue', 0))
                        ctx['fii_net'] = float(item.get('netValue', 0))
                    elif 'DII' in cat:
                        ctx['dii_buy'] = float(item.get('buyValue', 0))
                        ctx['dii_sell'] = float(item.get('sellValue', 0))
                        ctx['dii_net'] = float(item.get('netValue', 0))

            # 4. MARKET TURNOVER — equity + FnO volumes
            d4 = self._get('https://www.nseindia.com/api/market-turnover')
            if d4:
                for seg in d4.get('data', []):
                    name = seg.get('name', '')
                    today = seg.get('today', {})
                    yest = seg.get('yesterday', {})
                    src = today if today else yest
                    if name == 'Equities':
                        ctx['eq_volume'] = src.get('volume', 0)
                        ctx['eq_turnover'] = src.get('value', 0)
                    elif name == 'Index Options':
                        ctx['idx_opt_volume'] = src.get('volume', 0)
                        ctx['idx_opt_oi'] = src.get('openInterest', 0)
                    elif name == 'Stock Futures':
                        ctx['stk_fut_volume'] = src.get('volume', 0)
                        ctx['stk_fut_oi'] = src.get('openInterest', 0)
                    elif name == 'Index Futures':
                        ctx['idx_fut_volume'] = src.get('volume', 0)
                        ctx['idx_fut_oi'] = src.get('openInterest', 0)

            # 5. TOP GAINERS/LOSERS — market breadth
            d5 = self._get('https://www.nseindia.com/api/live-analysis-variations?index=gainers')
            if d5:
                all_g = d5.get('allSec', {}).get('data', [])
                ctx['top_gainer_pct'] = all_g[0].get('perChange', 0) if all_g else 0
                ctx['gainers_count'] = len(all_g)
            d6 = self._get('https://www.nseindia.com/api/live-analysis-variations?index=losers')
            if d6:
                all_l = d6.get('allSec', {}).get('data', [])
                ctx['top_loser_pct'] = all_l[0].get('perChange', 0) if all_l else 0
                ctx['losers_count'] = len(all_l)

            # 6. PRE-OPEN DATA — gap prices before/during market
            d7 = self._get('https://www.nseindia.com/api/market-data-pre-open?key=ALL')
            if d7:
                preopen_data = d7.get('data', [])
                ctx['preopen_count'] = len(preopen_data)
                # Aggregate: how many gapping up vs down
                gap_up = 0
                gap_dn = 0
                for po in preopen_data:
                    md = po.get('metadata', {})
                    pchg = md.get('pChange', 0)
                    if isinstance(pchg, (int, float)):
                        if pchg > 0:
                            gap_up += 1
                        elif pchg < 0:
                            gap_dn += 1
                ctx['preopen_gap_up'] = gap_up
                ctx['preopen_gap_dn'] = gap_dn

            # FO pre-open separately
            d8 = self._get('https://www.nseindia.com/api/market-data-pre-open?key=FO')
            if d8:
                fo_data = d8.get('data', [])
                ctx['preopen_fo_count'] = len(fo_data)

            self.last_data = ctx
            return ctx
        except Exception as e:
            log.warning(f"Market context error: {e}")
            return self.last_data


class MarketLens:
    BASE = "https://marketlens.nseindia.com"

    def __init__(self):
        from curl_cffi import requests as cf
        self.cf = cf
        self.action = None
        self.last_init = 0
        self._init()

    def _init(self):
        self.session = self.cf.Session(impersonate="chrome")
        self.session.request("GET", self.BASE, timeout=30)
        self.session.headers.update({"Referer": self.BASE + "/screener"})
        html = self.session.request("GET", self.BASE + "/screener", timeout=30).text
        for src in dict.fromkeys(re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html)):
            u = urljoin(self.BASE, src)
            if "/_next/static/" not in u: continue
            try: js = self.session.request("GET", u, timeout=30).text
            except: continue
            am = re.search(r'createServerReference\)\("([0-9a-f]+)"[^;]{0,400}?"runStockQuery"', js)
            if am: self.action = am.group(1); break
        self.last_init = time.time()
        log.info(f"ML init: {'OK' if self.action else 'FAIL'}")

    def _parse(self, content):
        records = {}; pos = 0
        while pos < len(content):
            if content[pos:pos+1] in (b"\n", b"\r"): pos += 1; continue
            m = re.match(rb"([0-9a-f]+):", content[pos:])
            if not m: break
            ident = m.group(1).decode(); pos += m.end()
            if content[pos:pos+1] == b"T":
                m2 = re.match(rb"T([0-9a-f]+),", content[pos:])
                if not m2: break
                pos += m2.end(); end = pos + int(m2.group(1), 16)
                records[ident] = content[pos:end].decode("utf-8"); pos = end
            else:
                end = content.find(b"\n", pos)
                if end < 0: end = len(content)
                try: records[ident] = json.loads(content[pos:end])
                except: pass
                pos = end + 1
        def resolve(v, seen=frozenset()):
            if isinstance(v, dict): return {k: resolve(val, seen) for k, val in v.items()}
            if isinstance(v, list): return [resolve(val, seen) for val in v]
            if isinstance(v, str) and v.startswith("$"):
                if v.startswith("$$"): return v[1:]
                ref = v.removeprefix("$").removeprefix("@")
                if ref not in records or ref in seen: return v
                t = records[ref]; return t if isinstance(t, str) else resolve(t, seen | {ref})
            return v
        for val in records.values():
            if isinstance(val, dict) and "success" in val: return resolve(val)
        return {"success": False}

    def poll(self):
        if time.time() - self.last_init > 7200:
            log.info("Re-initializing ML session...")
            try: self._init()
            except: pass
        try:
            r = self.session.request("POST", self.BASE + "/screener", headers={
                "Next-Action": self.action, "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8", "Origin": self.BASE,
            }, data=json.dumps(["Market Cap > 0"]), timeout=60)
            p = self._parse(r.content)
            if p.get("success"):
                data = p.get("data", {})
                return data.get("stocks", []) if isinstance(data, dict) else data
        except Exception as e:
            log.error(f"ML poll: {e}")
            try: self._init()
            except: pass
        return []


def main():
    log.info("=" * 50)
    log.info("VWAP CAPTURE BOT — NO TRADING")
    log.info("=" * 50)

    ml = MarketLens()
    if not ml.action:
        log.error("ML init failed. Retrying in 30s...")
        time.sleep(30)
        ml = MarketLens()
        if not ml.action:
            send_tg("CAPTURE Bot FAILED to connect to Market Lens. Exiting.")
            return

    scan = 0
    triggered_today = set()
    all_signals_today = []
    data_file = open(DATA_FILE, 'a', encoding='utf-8')
    market_file = open(MARKET_FILE, 'a', encoding='utf-8')
    mkt = MarketContext()

    now = datetime.now()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=35, second=0, microsecond=0)

    if now < market_open:
        wait = (market_open - now).total_seconds()
        log.info(f"Waiting {wait:.0f}s for 9:15 AM...")
        send_tg("CAPTURE Bot started. Waiting for 9:15 AM.\nMode: CAPTURE ONLY — no trades.\nLogging all 94 fields every scan.")
        time.sleep(wait)

    # === CAPTURE PRE-OPEN DATA (before 9:15) ===
    now = datetime.now()
    if now.hour == 9 and now.minute < 15:
        log.info("Capturing pre-open data...")
        try:
            preopen_all = mkt._get('https://www.nseindia.com/api/market-data-pre-open?key=ALL')
            preopen_fo = mkt._get('https://www.nseindia.com/api/market-data-pre-open?key=FO')
            gift_data = mkt._get('https://www.nseindia.com/api/marketStatus')
            fii_data = mkt._get('https://www.nseindia.com/api/fiidiiTradeReact')

            with open(PREOPEN_FILE, 'w', encoding='utf-8') as pf:
                # Save GIFT Nifty
                if gift_data:
                    gift = gift_data.get('giftnifty', {})
                    ind = gift_data.get('indicativenifty50', {})
                    pf.write(json.dumps({'_type': 'gift_nifty', '_ts': now.strftime('%H:%M:%S'), **gift}) + '\n')
                    pf.write(json.dumps({'_type': 'indicative_nifty', '_ts': now.strftime('%H:%M:%S'), **ind}) + '\n')

                # Save FII/DII
                if fii_data:
                    for item in fii_data:
                        pf.write(json.dumps({'_type': 'fii_dii', '_ts': now.strftime('%H:%M:%S'), **item}) + '\n')

                # Save all pre-open prices
                if preopen_all:
                    for po in preopen_all.get('data', []):
                        md = po.get('metadata', {})
                        det = po.get('detail', {})
                        record = {
                            '_type': 'preopen',
                            '_ts': now.strftime('%H:%M:%S'),
                            'symbol': md.get('symbol', ''),
                            'preopen_price': md.get('lastPrice', 0),
                            'prev_close': md.get('previousClose', 0),
                            'change': md.get('change', 0),
                            'pchange': md.get('pChange', 0),
                            'year_high': md.get('yearHigh', 0),
                            'year_low': md.get('yearLow', 0),
                            'final_qty': md.get('finalQuantity', 0),
                        }
                        record.update({k: v for k, v in det.items() if isinstance(v, (int, float, str))})
                        pf.write(json.dumps(record) + '\n')

                # Save FO pre-open
                if preopen_fo:
                    for po in preopen_fo.get('data', []):
                        md = po.get('metadata', {})
                        pf.write(json.dumps({
                            '_type': 'preopen_fo',
                            '_ts': now.strftime('%H:%M:%S'),
                            'symbol': md.get('symbol', ''),
                            'preopen_price': md.get('lastPrice', 0),
                            'prev_close': md.get('previousClose', 0),
                            'pchange': md.get('pChange', 0),
                        }) + '\n')

            po_count = len(preopen_all.get('data', [])) if preopen_all else 0
            fo_count = len(preopen_fo.get('data', [])) if preopen_fo else 0
            gift_price = gift_data.get('giftnifty', {}).get('LASTPRICE', 0) if gift_data else 0
            fii_net = 0
            if fii_data:
                for item in fii_data:
                    if 'FII' in item.get('category', ''):
                        fii_net = float(item.get('netValue', 0))

            log.info(f"Pre-open: {po_count} stocks, {fo_count} FO, GIFT={gift_price}, FII={fii_net:+,.0f}")
            send_tg(f"Pre-open captured:\n{po_count} stocks, {fo_count} FO\nGIFT Nifty: {gift_price}\nFII net: Rs {fii_net:+,.0f} Cr")
        except Exception as e:
            log.error(f"Pre-open capture error: {e}")

    # === DEPTH WATCHLIST — poll order book for news signal stocks ===
    depth_watchlist = set()  # tickers to poll depth for
    depth_file = open(DEPTH_FILE, 'a', encoding='utf-8')

    def load_depth_watchlist():
        """Load news signal stocks from today's audit trail."""
        audit_path = Path(__file__).parent / 'logs' / f'audit_{today}.jsonl'
        if not audit_path.exists():
            return
        try:
            with open(audit_path) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if r.get('action') in ('SIGNAL_TRADE', 'HEADLINE_FILTERED'):
                            sym = r.get('symbol', '')
                            # Extract stock symbol from headline (e.g., "TCI: buyback..." -> "TCI")
                            if ':' in sym:
                                sym = sym.split(':')[0].strip()
                            if sym and len(sym) <= 20 and sym.isalpha():
                                depth_watchlist.add(sym)
                    except:
                        pass
        except:
            pass

    def poll_depth(scan_num, ts):
        """Poll 5-level depth for watchlist stocks via INDmoney."""
        if not depth_watchlist:
            return
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from live.indmoney_client import get_full_quote
            quotes = get_full_quote(list(depth_watchlist))
            for sym, q in quotes.items():
                record = {'_scan': scan_num, '_ts': ts, 'ticker': sym}
                record['ltp'] = q.get('last_price', 0)
                record['open'] = q.get('open', 0)
                record['high'] = q.get('high', 0)
                record['low'] = q.get('low', 0)
                record['prev_close'] = q.get('prev_close', 0)
                record['volume'] = q.get('volume', 0)
                md = q.get('market_depth', {})
                for scrip_key, scrip_data in md.items():
                    agg = scrip_data.get('aggregate', {})
                    tb = agg.get('total_buy', 0)
                    ts_val = agg.get('total_sell', 0)
                    if isinstance(tb, str): tb = float(tb.replace(',', ''))
                    if isinstance(ts_val, str): ts_val = float(ts_val.replace(',', ''))
                    record['total_buy'] = tb
                    record['total_sell'] = ts_val
                    record['buy_pct'] = agg.get('buy_percentage', 50)
                    record['sell_pct'] = agg.get('sell_percentage', 50)
                    levels = scrip_data.get('depth', [])
                    for i, level in enumerate(levels[:5]):
                        b = level.get('buy', {})
                        s = level.get('sell', {})
                        bq = b.get('quantity', '0')
                        bp = b.get('price', '0')
                        sq = s.get('quantity', '0')
                        sp = s.get('price', '0')
                        if isinstance(bq, str): bq = float(bq.replace(',', ''))
                        if isinstance(bp, str): bp = float(bp.replace(',', ''))
                        if isinstance(sq, str): sq = float(sq.replace(',', ''))
                        if isinstance(sp, str): sp = float(sp.replace(',', ''))
                        record[f'bid{i}_qty'] = bq
                        record[f'bid{i}_price'] = bp
                        record[f'ask{i}_qty'] = sq
                        record[f'ask{i}_price'] = sp
                    break
                depth_file.write(json.dumps(record) + '\n')
                depth_file.flush()
        except Exception as e:
            log.warning(f"Depth poll error: {e}")

    # === PRE-MARKET DEPTH POLLING (9:06-9:15) ===
    now = datetime.now()
    if now.hour == 9 and now.minute < 15:
        log.info("Pre-market depth polling — waiting for news signals...")
        # Wait until 9:06 (news_orb starts at 9:05, signals by 9:06)
        wait_for = now.replace(minute=6, second=30)
        if now < wait_for:
            time.sleep((wait_for - now).total_seconds())

        load_depth_watchlist()
        if depth_watchlist:
            log.info(f"Pre-market depth: watching {len(depth_watchlist)} stocks: {depth_watchlist}")
            send_tg(f"Pre-market depth: {len(depth_watchlist)} news stocks")
            pre_scan = 0
            while datetime.now().hour == 9 and datetime.now().minute < 15:
                pre_scan += 1
                ts = datetime.now().strftime('%H:%M:%S')
                poll_depth(pre_scan, ts)
                log.info(f"Pre-market depth scan #{pre_scan} @ {ts}")
                time.sleep(15)  # poll every 15s during pre-open
        else:
            log.info("No news signals yet for depth watchlist")

    send_tg("CAPTURE Bot LIVE.\nScanning every 60s. All fields saved.\nSignals sent to Telegram (no trading).")

    try:
        while datetime.now() < market_close:
            t0 = time.time()
            try:
                stocks = ml.poll()
                if not stocks:
                    log.warning("Empty poll")
                    time.sleep(30)
                    continue

                scan += 1
                ts = datetime.now().strftime('%H:%M:%S')

                # === SAVE ALL DATA — every stock, every field, every scan ===
                for s in stocks:
                    ticker = s.get('ticker', '')
                    if not ticker: continue
                    # Save ALL fields + scan metadata
                    record = {'_scan': scan, '_ts': ts}
                    for k, v in s.items():
                        if v is not None and v != '' and v != 0:
                            record[k] = v
                    data_file.write(json.dumps(record) + '\n')
                    data_file.flush()

                # === SAVE MARKET CONTEXT ===
                ctx = mkt.poll()
                ctx['_scan'] = scan
                ctx['_ts'] = ts
                market_file.write(json.dumps(ctx) + '\n')
                market_file.flush()

                # === POLL DEPTH for watchlist stocks ===
                if scan % 5 == 1:  # reload watchlist every 5 scans
                    load_depth_watchlist()
                if depth_watchlist:
                    poll_depth(scan, ts)

                log.info(f"Scan #{scan} @ {ts} | {len(stocks)} stocks | depth={len(depth_watchlist)} | NIFTY={ctx.get('nifty',0)} VIX={ctx.get('vix',0)}")

                # === DETECT SIGNALS ===
                signals = []
                for s in stocks:
                    ticker = s.get('ticker', '')
                    if not ticker or ticker in triggered_today: continue

                    ltp = s.get('lastTradedPrice') or 0
                    vwap = s.get('avgPrice') or 0
                    if ltp <= 0 or vwap <= 0 or ltp < MIN_PRICE: continue

                    dist = (ltp - vwap) / vwap * 100
                    if abs(dist) < VWAP_DIST_MIN: continue

                    delivery = s.get('deliveryPercentage') or 0

                    direction = 'SELL' if dist > 0 else 'BUY'
                    is_fno = ticker in FNO_STOCKS
                    instrument = 'FUT' if is_fno else 'MIS'

                    sig = {
                        'scan': scan, 'time': ts,
                        'ticker': ticker, 'dir': direction,
                        'instrument': instrument,
                        'ltp': ltp, 'vwap': vwap, 'dist': round(abs(dist), 2),
                        'delivery': delivery,
                        'volume': s.get('volume') or 0,
                        'sector': s.get('sector', ''),
                        'sixMonthReturn': s.get('sixMonthReturn') or 0,
                        'threeMonthReturn': s.get('threeMonthReturn') or 0,
                        'oneMonthReturn': s.get('oneMonthReturn') or 0,
                        'oneWeekReturn': s.get('oneWeekReturn') or 0,
                        'foundedYear': s.get('foundedYear') or 0,
                        'marketCap': s.get('marketCap') or 0,
                        'peRatio': s.get('peRatio') or 0,
                        'pbRatio': s.get('pbRatio') or 0,
                        'debtToEquity': s.get('debtToEquity') or 0,
                        'promoterHolding': s.get('promoterHolding') or 0,
                        'dma20': s.get('dma20') or 0,
                        'dma50': s.get('dma50') or 0,
                        'dma200': s.get('dma200') or 0,
                        'passes_convergence': delivery >= DELIVERY_MIN,
                    }

                    signals.append(sig)
                    triggered_today.add(ticker)
                    all_signals_today.append(sig)

                    # Log signal
                    with open(SIGNAL_FILE, 'a') as f:
                        f.write(json.dumps(sig) + '\n')

                # Sort by distance and send top signals to Telegram
                if signals:
                    signals.sort(key=lambda x: -x['dist'])
                    lines = [f"VWAP SIGNALS @ {ts} (scan #{scan})"]
                    lines.append(f"{len(signals)} new signals (2%+ from VWAP)\n")

                    for sig in signals[:10]:
                        action = 'SHORT' if sig['dir'] == 'SELL' else 'LONG'
                        conv = 'Y' if sig['passes_convergence'] else 'N'
                        lines.append(
                            f"  {action} [{sig['instrument']}] <b>{sig['ticker']}</b> "
                            f"Rs{sig['ltp']:.1f}"
                        )
                        lines.append(
                            f"    VWAP={sig['vwap']:.1f} dist={sig['dist']:.1f}% "
                            f"dlv={sig['delivery']:.0f}% conv={conv}"
                        )

                    if len(signals) > 10:
                        lines.append(f"\n  +{len(signals) - 10} more")

                    conv_count = sum(1 for s in signals if s['passes_convergence'])
                    buy_count = sum(1 for s in signals if s['dir'] == 'BUY')
                    sell_count = len(signals) - buy_count
                    fno_count = sum(1 for s in signals if s['instrument'] == 'FUT')

                    lines.append(f"\nBUY:{buy_count} SELL:{sell_count} FNO:{fno_count}")
                    lines.append(f"Pass convergence filter: {conv_count}/{len(signals)}")
                    lines.append(f"Total signals today: {len(all_signals_today)}")

                    send_tg('\n'.join(lines))
                    log.info(f"  {len(signals)} signals sent to TG")

                # Status every 10 scans
                if scan % 10 == 0:
                    total_sigs = len(all_signals_today)
                    conv_sigs = sum(1 for s in all_signals_today if s['passes_convergence'])
                    log.info(f"STATUS | Scan #{scan} | Signals: {total_sigs} (conv: {conv_sigs})")

            except Exception as e:
                log.error(f"Error: {e}", exc_info=True)
                time.sleep(10)
                continue

            elapsed = time.time() - t0
            time.sleep(max(5, POLL_INTERVAL - elapsed))

    finally:
        data_file.close()
        market_file.close()
        depth_file.close()
        # Compress and move to /mnt/data at EOD
        _archive_day(DATA_FILE, log)
        _archive_day(MARKET_FILE, log)
        _archive_day(SIGNAL_FILE, log)
        _archive_day(PREOPEN_FILE, log)
        _archive_day(DEPTH_FILE, log)

    # === EOD SUMMARY ===
    total_sigs = len(all_signals_today)
    conv_sigs = sum(1 for s in all_signals_today if s['passes_convergence'])
    buy_sigs = sum(1 for s in all_signals_today if s['dir'] == 'BUY')
    sell_sigs = total_sigs - buy_sigs

    lines = [
        "CAPTURE BOT EOD REPORT", "",
        f"Scans: {scan}",
        f"Total signals: {total_sigs}",
        f"  BUY: {buy_sigs} | SELL: {sell_sigs}",
        f"  Pass convergence: {conv_sigs}/{total_sigs}",
        "",
        f"Data saved: {DATA_FILE.name}",
        f"Signals saved: {SIGNAL_FILE.name}",
    ]

    # Show top signals by distance
    if all_signals_today:
        all_signals_today.sort(key=lambda x: -x['dist'])
        lines.append("\nTop 10 signals by distance:")
        for s in all_signals_today[:10]:
            conv = 'CONV' if s['passes_convergence'] else ''
            lines.append(
                f"  {s['dir']} [{s['instrument']}] {s['ticker']} "
                f"dist={s['dist']:.1f}% dlv={s['delivery']:.0f}% {conv}"
            )

    summary = '\n'.join(lines)
    log.info(summary)
    send_tg(summary)
    log.info("Capture bot stopped.")


if __name__ == "__main__":
    main()
