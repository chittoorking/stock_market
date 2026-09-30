"""VWAP Mean Reversion Bot — Production Ready.
Polls Market Lens every 60s. Fades stocks 1%+ from VWAP. Smart exit.
Paper trades + Telegram alerts. Deploy on GCP."""
import json, re, time, requests, logging, os, sys
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

# === CONFIG ===
TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'
VWAP_DIST_MIN = 1.0       # minimum % distance from VWAP
SIX_MONTH_MAX = 15.0      # max 6-month return
ONE_MONTH_MIN = -5.0       # min 1-month return
MIN_COMPANY_AGE = 10       # at least 10 years old (older companies revert better)
HOLD_BARS = 7              # 7 scans = ~35 min
TRAIL_ACTIVATE = 0.3       # activate trail if profit > 0.3%
TRAIL_PCT = 0.10           # 0.10% trailing stop
CATASTROPHE_SL = 3.0       # 3% hard stop
POLL_INTERVAL = 60         # seconds
MIN_PRICE = 50             # skip penny stocks
TOTAL_CAPITAL = 10000      # fallback if broker API fails
MIS_LEVERAGE = 5           # 5x for equity MIS
FUT_LEVERAGE = 10          # 10x for futures MIS
MAX_CAPITAL_PER_TRADE = 0.30  # max 30% of capital per trade
CAPITAL_BUFFER = 0.05         # keep 5% buffer, deploy only 95%

# Auto-scale positions by capital
if TOTAL_CAPITAL <= 10000:
    MAX_POSITIONS = 1
elif TOTAL_CAPITAL <= 25000:
    MAX_POSITIONS = 2
elif TOTAL_CAPITAL <= 50000:
    MAX_POSITIONS = 3
elif TOTAL_CAPITAL <= 75000:
    MAX_POSITIONS = 4
elif TOTAL_CAPITAL <= 100000:
    MAX_POSITIONS = 5
elif TOTAL_CAPITAL <= 150000:
    MAX_POSITIONS = 6
elif TOTAL_CAPITAL <= 200000:
    MAX_POSITIONS = 7
elif TOTAL_CAPITAL <= 300000:
    MAX_POSITIONS = 8
elif TOTAL_CAPITAL <= 500000:
    MAX_POSITIONS = 9
else:
    MAX_POSITIONS = 10
LOG_DIR = Path(__file__).parent / 'vwap_logs'

# F&O stocks — trade futures on these, MIS equity on the rest
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

LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s',
    handlers=[logging.StreamHandler(),
              logging.FileHandler(LOG_DIR / f'vwap_{datetime.now().strftime("%Y%m%d")}.log')])
log = logging.getLogger('vwap')

def send_tg(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
    except: pass


# === MARKET LENS CLIENT ===
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
            if content[pos:pos+1] in (b"\n",b"\r"): pos+=1; continue
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
        # Re-init session every 2 hours
        if time.time() - self.last_init > 7200:
            log.info("Re-initializing ML session...")
            try: self._init()
            except: pass

        try:
            r = self.session.request("POST", self.BASE + "/screener", headers={
                "Next-Action": self.action, "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8", "Origin": self.BASE,
            }, data=json.dumps(["Market Cap > 500"]), timeout=30)
            p = self._parse(r.content)
            if p.get("success"):
                data = p.get("data", {})
                return data.get("stocks", []) if isinstance(data, dict) else data
        except Exception as e:
            log.error(f"ML poll: {e}")
            try: self._init()
            except: pass
        return []


# === POSITION TRACKER ===
class Position:
    def __init__(self, ticker, direction, entry_price, entry_scan, vwap, dist):
        self.ticker = ticker
        self.direction = direction
        self.entry_price = entry_price
        self.entry_scan = entry_scan
        self.entry_time = datetime.now().strftime('%H:%M:%S')
        self.vwap = vwap
        self.dist = dist
        self.is_fno = ticker in FNO_STOCKS
        self.instrument = 'FUTURES' if self.is_fno else 'EQUITY_MIS'
        self.capital_used = 0
        self.position_size = 0
        self.scans_held = 0
        self.peak_pnl = 0
        self.trailing_active = False
        self.exited = False
        self.exit_price = 0
        self.exit_reason = ''
        self.pnl = 0

    def update(self, current_price):
        self.scans_held += 1

        if self.direction == 'SELL':
            self.pnl = (self.entry_price - current_price) / self.entry_price * 100
        else:
            self.pnl = (current_price - self.entry_price) / self.entry_price * 100

        if self.pnl > self.peak_pnl:
            self.peak_pnl = self.pnl

        # Catastrophe SL
        if self.pnl <= -CATASTROPHE_SL:
            self.exit(current_price, 'CATASTROPHE_SL')
            return

        # Before 35 min: just hold, but log heartbeat every 5 scans
        if self.scans_held < HOLD_BARS:
            if self.scans_held % 5 == 0 and self.scans_held > 0:
                log.info(f"HEARTBEAT | {self.ticker} {self.direction} [{self.instrument}] | {self.scans_held*5}min | pnl={self.pnl:+.2f}% peak={self.peak_pnl:+.2f}%")
            return

        # At 35 min+: decide (>= handles skipped scans from slow polls)
        if not self.trailing_active and self.scans_held >= HOLD_BARS:
            if self.pnl <= 0:
                self.exit(current_price, 'LOSS_AT_35M')
                return
            if self.pnl < TRAIL_ACTIVATE:
                self.exit(current_price, 'SMALL_PROFIT')
                return
            # Activate trail
            self.trailing_active = True
            self.peak_pnl = self.pnl
            return

        # After trail activated
        if self.trailing_active:
            if self.pnl < self.peak_pnl - TRAIL_PCT:
                self.exit(current_price, 'TRAIL_TRIGGERED')
                return
            # Safety: max hold 60 min (12 scans) even with trail
            if self.scans_held >= 12:
                self.exit(current_price, 'MAX_HOLD')
                return

    def exit(self, price, reason):
        self.exited = True
        self.exit_price = price
        self.exit_reason = reason
        if self.direction == 'SELL':
            self.pnl = (self.entry_price - price) / self.entry_price * 100
        else:
            self.pnl = (price - self.entry_price) / self.entry_price * 100


# === MAIN BOT ===
def main():
    global TOTAL_CAPITAL, MAX_POSITIONS

    log.info("=" * 50)
    log.info("VWAP MEAN REVERSION BOT v5")
    log.info("=" * 50)

    # Fetch live capital from INDmoney
    try:
        from pathlib import Path as P
        token_file = P(__file__).parent.parent / 'data' / 'indmoney_token.txt'
        if token_file.exists():
            ind_token = token_file.read_text().strip()
            r = requests.get('https://api.indstocks.com/funds',
                headers={'Authorization': ind_token}, timeout=10)
            if r.status_code == 200:
                data = r.json().get('data', {})
                avl = data.get('detailed_avl_balance', {})
                balance = avl.get('eq_mis', 0) or data.get('sod_balance', 0)
                if balance > 0:
                    TOTAL_CAPITAL = balance
                    log.info(f"Live capital from INDmoney: Rs {TOTAL_CAPITAL:,.0f}")
                else:
                    log.warning(f"INDmoney returned 0 balance, using fallback Rs {TOTAL_CAPITAL:,}")
            else:
                log.warning(f"INDmoney funds API: {r.status_code}, using fallback Rs {TOTAL_CAPITAL:,}")
    except Exception as e:
        log.warning(f"Could not fetch live capital: {e}, using fallback Rs {TOTAL_CAPITAL:,}")

    # Recalculate slots based on live capital (may have changed from INDmoney)
    if TOTAL_CAPITAL <= 10000: MAX_POSITIONS = 1
    elif TOTAL_CAPITAL <= 25000: MAX_POSITIONS = 2
    elif TOTAL_CAPITAL <= 50000: MAX_POSITIONS = 3
    elif TOTAL_CAPITAL <= 75000: MAX_POSITIONS = 4
    elif TOTAL_CAPITAL <= 100000: MAX_POSITIONS = 5
    elif TOTAL_CAPITAL <= 150000: MAX_POSITIONS = 6
    elif TOTAL_CAPITAL <= 200000: MAX_POSITIONS = 7
    elif TOTAL_CAPITAL <= 300000: MAX_POSITIONS = 8
    elif TOTAL_CAPITAL <= 500000: MAX_POSITIONS = 9
    else: MAX_POSITIONS = 10

    log.info(f"Config: dist>{VWAP_DIST_MIN}% 6M<{SIX_MONTH_MAX}% 1M>{ONE_MONTH_MIN}% age>={MIN_COMPANY_AGE}yr")
    log.info(f"Capital: Rs{TOTAL_CAPITAL:,.0f} | Slots: {MAX_POSITIONS} | Per slot: Rs{TOTAL_CAPITAL/MAX_POSITIONS:,.0f}")
    log.info(f"Exit: hold {HOLD_BARS} scans, trail {TRAIL_PCT}% after {TRAIL_ACTIVATE}% profit, catastrophe SL {CATASTROPHE_SL}%")

    ml = MarketLens()
    if not ml.action:
        log.error("ML init failed. Retrying in 30s...")
        time.sleep(30)
        ml = MarketLens()
        if not ml.action:
            send_tg("VWAP Bot FAILED to connect to Market Lens. Exiting.")
            return
    positions = {}          # ticker -> Position
    closed_trades = []      # completed trades
    triggered_today = set() # tickers already triggered
    scan = 0
    trade_file = LOG_DIR / f'trades_{datetime.now().strftime("%Y%m%d")}.jsonl'
    state_file = LOG_DIR / 'state.json'

    # Restore state from crash
    if state_file.exists():
        try:
            saved = json.loads(state_file.read_text())
            if saved.get('date') == datetime.now().strftime('%Y-%m-%d'):
                triggered_today = set(saved.get('triggered', []))
                scan = saved.get('scan', 0)
                for p in saved.get('positions', []):
                    pos = Position(p['ticker'], p['dir'], p['entry'], p['scan'], p['vwap'], p['dist'])
                    pos.scans_held = p.get('scans_held', 0)
                    pos.peak_pnl = p.get('peak_pnl', 0)
                    pos.trailing_active = p.get('trailing', False)
                    pos.capital_used = p.get('capital', 0)
                    pos.position_size = p.get('pos_size', 0)
                    pos.ml_data = p.get('ml_data', {})
                    positions[pos.ticker] = pos
                log.info(f"RESTORED: {len(positions)} positions, {len(triggered_today)} triggered, scan={scan}")
                send_tg(f"Bot RESTARTED. Restored {len(positions)} open positions.")
        except Exception as e:
            log.error(f"State restore failed: {e}")

    def save_state():
        try:
            state_file.write_text(json.dumps({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'scan': scan,
                'triggered': list(triggered_today),
                'positions': [{
                    'ticker': p.ticker, 'dir': p.direction,
                    'entry': p.entry_price, 'scan': p.entry_scan,
                    'vwap': p.vwap, 'dist': p.dist,
                    'scans_held': p.scans_held, 'peak_pnl': p.peak_pnl,
                    'trailing': p.trailing_active,
                    'capital': p.capital_used, 'pos_size': p.position_size,
                    'ml_data': getattr(p, 'ml_data', {}),
                } for p in positions.values()],
            }))
        except: pass

    now = datetime.now()
    market_open = now.replace(hour=9, minute=50, second=0, microsecond=0)  # start after 9:50
    market_close = now.replace(hour=15, minute=10, second=0, microsecond=0)
    no_new_entry_after = now.replace(hour=14, minute=30, second=0, microsecond=0)  # no new trades after 2:30

    if now < market_open:
        wait = (market_open - now).total_seconds()
        log.info(f"Waiting {wait:.0f}s for 9:50 AM...")
        send_tg("VWAP Bot started. Waiting for 9:50 AM.\n\nStrategy: fade stocks 1%+ from VWAP\nFilters: 6M<15%, 1M>-5%\nHold 35 min, smart exit")
        time.sleep(wait)

    send_tg("VWAP Bot v5 LIVE.\nScanning 2200 stocks every 60s.\nLogging all signals with full ML data.")

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
            by_ticker = {s.get('ticker',''): s for s in stocks}

            # === UPDATE OPEN POSITIONS ===
            to_close = []
            for ticker, pos in positions.items():
                s = by_ticker.get(ticker)
                if not s: continue
                ltp = s.get('lastTradedPrice') or 0
                if ltp <= 0: continue
                pos.update(ltp)
                if pos.exited:
                    to_close.append(ticker)
                    closed_trades.append(pos)
                    status = 'WIN' if pos.pnl > 0 else 'LOSS'
                    emoji = '++' if pos.pnl > 0 else '--'
                    log.info(f"EXIT {status} | {ticker} {pos.direction} [{pos.instrument}] | pnl={pos.pnl:+.2f}% | {pos.exit_reason} | held {pos.scans_held} scans")
                    with open(trade_file, 'a') as f:
                        f.write(json.dumps({
                            'event': 'EXIT', 'time': ts, 'scan': scan,
                            'ticker': ticker, 'dir': pos.direction,
                            'instrument': pos.instrument,
                            'entry': pos.entry_price, 'exit': pos.exit_price,
                            'pnl': pos.pnl, 'reason': pos.exit_reason,
                            'entry_time': pos.entry_time, 'exit_time': ts,
                            'vwap': pos.vwap, 'dist': pos.dist,
                            'scans': pos.scans_held,
                            'peak_pnl': pos.peak_pnl,
                            'capital': pos.capital_used,
                            'position_size': pos.position_size,
                            'rs_pnl': pos.position_size * pos.pnl / 100 if pos.position_size else 0,
                            'ml': getattr(pos, 'ml_data', {}),
                        }) + '\n')
                    rs_pnl = pos.position_size * pos.pnl / 100 if pos.position_size else 0
                    send_tg(f"{emoji} <b>{ticker}</b> {pos.direction} [{pos.instrument}] {pos.pnl:+.2f}% Rs{rs_pnl:+,.0f} [{pos.exit_reason}]\nEntry: Rs{pos.entry_price:.1f} Exit: Rs{pos.exit_price:.1f} Size: Rs{pos.position_size:,.0f}")

            for t in to_close:
                del positions[t]

            # === SCAN FOR NEW SIGNALS (stop new entries after 2:30 PM) ===
            new_signals = []
            past_cutoff = datetime.now() >= no_new_entry_after
            if past_cutoff and scan % 10 == 0:
                log.info("Past 2:30 PM - no new entries. Managing open positions only.")

            # Step 1: Collect ALL candidates
            candidates = []
            if not past_cutoff:
                for s in stocks:
                    ticker = s.get('ticker', '')
                    if not ticker: continue
                    if ticker in triggered_today: continue
                    if ticker in positions: continue

                    ltp = s.get('lastTradedPrice') or 0
                    vwap = s.get('avgPrice') or 0
                    if ltp <= 0 or vwap <= 0: continue
                    if ltp < MIN_PRICE: continue

                    dist = (ltp - vwap) / vwap * 100
                    if abs(dist) < VWAP_DIST_MIN: continue

                    six_m = s.get('sixMonthReturn') or 0
                    one_m = s.get('oneMonthReturn') or 0
                    fy = s.get('foundedYear') or 0
                    if six_m >= SIX_MONTH_MAX: continue
                    if one_m <= ONE_MONTH_MIN: continue
                    if fy <= 0 or (datetime.now().year - fy) < MIN_COMPANY_AGE: continue

                    direction = 'SELL' if dist > 0 else 'BUY'
                    # Capture ALL ML fields for logging
                    ml_data = {k: v for k, v in s.items() if isinstance(v, (int, float, str)) and v is not None}
                    candidates.append({
                        'ticker': ticker, 'dir': direction, 'price': ltp,
                        'vwap': vwap, 'dist': abs(dist),
                        'six_m': six_m, 'one_m': one_m,
                        'sector': s.get('sector', ''),
                        'ml_data': ml_data,
                    })

            # Step 2: Sort by VWAP distance (highest first = strongest reversion)
            candidates.sort(key=lambda x: -x['dist'])

            # Step 3: Take signals — sized by capital
            # Capital allocation: split available capital across positions
            deployable = TOTAL_CAPITAL * (1 - CAPITAL_BUFFER)
            capital_in_use = sum(p.capital_used for p in positions.values())
            capital_available = deployable - capital_in_use
            slots_available = MAX_POSITIONS - len(positions)

            for sig in candidates:
                if slots_available <= 0: break
                if capital_available <= 0: break

                ticker = sig['ticker']
                is_fno = ticker in FNO_STOCKS
                leverage = FUT_LEVERAGE if is_fno else MIS_LEVERAGE

                # Size: equal split of remaining capital, capped at 30%
                capital_per_trade = min(capital_available / max(slots_available, 1),
                                       TOTAL_CAPITAL * MAX_CAPITAL_PER_TRADE)
                position_size = capital_per_trade * leverage

                triggered_today.add(ticker)
                pos = Position(ticker, sig['dir'], sig['price'], scan, sig['vwap'], sig['dist'])
                pos.capital_used = capital_per_trade
                pos.position_size = position_size
                pos.ml_data = sig.get('ml_data', {})
                positions[ticker] = pos
                new_signals.append(sig)

                # Log full entry with all ML fields
                with open(trade_file, 'a') as f:
                    f.write(json.dumps({
                        'event': 'ENTRY', 'time': ts, 'scan': scan,
                        'ticker': ticker, 'dir': sig['dir'],
                        'instrument': pos.instrument,
                        'entry_price': sig['price'], 'vwap': sig['vwap'],
                        'dist': sig['dist'], 'capital': capital_per_trade,
                        'position_size': position_size,
                        'ml': sig.get('ml_data', {}),
                    }) + '\n')

                capital_available -= capital_per_trade
                slots_available -= 1

                instrument = pos.instrument
                log.info(f"ENTRY | {ticker} {sig['dir']} [{instrument}] @ {sig['price']:.1f} | VWAP={sig['vwap']:.1f} dist={sig['dist']:.1f}% | capital=Rs{capital_per_trade:,.0f} pos=Rs{position_size:,.0f} | 6M={sig['six_m']:.1f}% 1M={sig['one_m']:.1f}%")

            # === TELEGRAM ALERTS ===
            if new_signals:
                lines = [f"VWAP SIGNAL @ {ts} (scan #{scan})"]
                for sig in new_signals[:5]:
                    action = 'SHORT' if sig['dir'] == 'SELL' else 'LONG'
                    instrument = 'FUT' if sig['ticker'] in FNO_STOCKS else 'MIS'
                    lines.append(f"  {action} [{instrument}] <b>{sig['ticker']}</b> @ Rs{sig['price']:.1f}")
                    lines.append(f"    VWAP={sig['vwap']:.1f} dist={sig['dist']:.1f}% {sig['sector'][:20]}")
                if len(new_signals) > 5:
                    lines.append(f"  +{len(new_signals)-5} more")
                fno_count = sum(1 for sig in new_signals if sig['ticker'] in FNO_STOCKS)
                mis_count = len(new_signals) - fno_count
                lines.append(f"\nFutures: {fno_count} | Equity MIS: {mis_count} | Open: {len(positions)} | Closed: {len(closed_trades)}")
                send_tg('\n'.join(lines))

            # === STATUS ===
            if scan % 10 == 0:
                wins = sum(1 for t in closed_trades if t.pnl > 0)
                total_pnl = sum(t.pnl for t in closed_trades)
                log.info(f"Scan #{scan} | {len(stocks)} stocks | {len(positions)} open | {len(closed_trades)} closed | W={wins} | PnL={total_pnl:+.2f}%")

                if scan % 30 == 0 and closed_trades:
                    send_tg(f"Status #{scan}: {wins}/{len(closed_trades)} wins | PnL={total_pnl:+.2f}% | {len(positions)} open")

        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)
            time.sleep(10)
            continue

        save_state()
        elapsed = time.time() - t0
        time.sleep(max(5, POLL_INTERVAL - elapsed))

    # === EOD: CLOSE ALL POSITIONS ===
    log.info("Market closing. Closing all positions...")
    stocks = ml.poll()
    by_ticker = {s.get('ticker',''): s for s in stocks}
    for ticker, pos in list(positions.items()):
        s = by_ticker.get(ticker, {})
        ltp = s.get('lastTradedPrice') or pos.entry_price
        pos.exit(ltp, 'EOD')
        closed_trades.append(pos)
        log.info(f"EOD EXIT | {ticker} {pos.direction} [{pos.instrument}] | pnl={pos.pnl:+.2f}%")
        with open(trade_file, 'a') as f:
            f.write(json.dumps({
                'event': 'EXIT', 'time': datetime.now().strftime('%H:%M:%S'),
                'ticker': ticker, 'dir': pos.direction,
                'instrument': pos.instrument,
                'entry': pos.entry_price, 'exit': ltp,
                'pnl': pos.pnl, 'reason': 'EOD',
                'entry_time': pos.entry_time,
                'exit_time': datetime.now().strftime('%H:%M:%S'),
                'vwap': pos.vwap, 'dist': pos.dist,
                'scans': pos.scans_held,
                'peak_pnl': pos.peak_pnl,
                'capital': pos.capital_used,
                'position_size': pos.position_size,
                'rs_pnl': pos.position_size * pos.pnl / 100 if pos.position_size else 0,
                'ml': getattr(pos, 'ml_data', {}),
            }) + '\n')
    positions.clear()

    # === EOD SUMMARY ===
    if closed_trades:
        wins = sum(1 for t in closed_trades if t.pnl > 0)
        total = len(closed_trades)
        total_pnl = sum(t.pnl for t in closed_trades)
        avg = total_pnl / total

        by_reason = defaultdict(list)
        for t in closed_trades:
            by_reason[t.exit_reason].append(t.pnl)

        lines = ["VWAP BOT EOD REPORT", ""]
        lines.append(f"<b>{wins}/{total} = {wins*100//total}% WR</b>")
        lines.append(f"Total PnL: {total_pnl:+.2f}%")
        lines.append(f"Avg: {avg:+.3f}%")
        avg_rs = sum(t.position_size * t.pnl / 100 for t in closed_trades if t.position_size) / total
        lines.append(f"Avg Rs/trade: Rs{avg_rs:+,.0f}")
        # By instrument
        fno_closed = [t for t in closed_trades if t.instrument == 'FUTURES']
        mis_closed = [t for t in closed_trades if t.instrument == 'EQUITY_MIS']
        lines.append("")
        lines.append("By instrument:")
        if fno_closed:
            fw = sum(1 for t in fno_closed if t.pnl > 0)
            fa = sum(t.pnl for t in fno_closed)/len(fno_closed)
            lines.append(f"  FUTURES: {fw}/{len(fno_closed)} = {fw*100//len(fno_closed)}% WR avg={fa:+.3f}%")
        if mis_closed:
            mw = sum(1 for t in mis_closed if t.pnl > 0)
            ma = sum(t.pnl for t in mis_closed)/len(mis_closed)
            lines.append(f"  EQUITY MIS: {mw}/{len(mis_closed)} = {mw*100//len(mis_closed)}% WR avg={ma:+.3f}%")

        lines.append("")
        lines.append("By exit reason:")
        for reason, pnls in sorted(by_reason.items()):
            w = sum(1 for p in pnls if p > 0)
            a = sum(pnls)/len(pnls)
            lines.append(f"  {reason}: {w}/{len(pnls)} avg={a:+.3f}%")

        lines.append("")
        lines.append("Top trades:")
        for t in sorted(closed_trades, key=lambda x: -x.pnl)[:5]:
            lines.append(f"  {t.ticker} {t.direction} [{t.instrument}] {t.pnl:+.2f}%")

        summary = '\n'.join(lines)
        log.info(summary)
        send_tg(summary)
    else:
        send_tg("VWAP Bot: No trades today.")

    log.info("Bot stopped.")


if __name__ == "__main__":
    main()
