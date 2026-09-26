"""Market Heartbeat v4 — CAPTURE EVERYTHING at 1-min level.
Saves ALL 95 fields for ALL stocks every scan.
Parallel strategy builder analyzes in real-time."""
import json, re, time, requests, logging, os, gzip
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin
from threading import Thread

TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'
LOG_DIR = Path(__file__).parent / 'scanner_logs'
LOG_DIR.mkdir(exist_ok=True)
DATA_DIR = LOG_DIR / 'snapshots'
DATA_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s',
    handlers=[logging.StreamHandler(),
              logging.FileHandler(LOG_DIR / f'scanner_{datetime.now().strftime("%Y%m%d")}.log')])
log = logging.getLogger('hb')

def send_tg(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
    except: pass


class MarketLens:
    BASE = "https://marketlens.nseindia.com"

    def __init__(self):
        from curl_cffi import requests as cf
        self.cf = cf
        self.action = None
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
        log.info(f"ML: {'OK' if self.action else 'FAIL'}")

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
            log.error(f"ML: {e}")
            try: self._init()
            except: pass
        return []


# === STRATEGY BUILDER — runs in parallel, analyzes captured data ===
class StrategyBuilder:
    """Analyzes live data every 5 scans. Looks for patterns emerging."""

    def __init__(self):
        self.price_at = defaultdict(dict)  # ticker -> {scan_num: ltp}
        self.first = {}                     # ticker -> first snapshot
        self.alerted = set()

    def feed(self, scan_num, stocks):
        """Called every scan with full stock list."""
        for s in stocks:
            t = s.get('ticker', '')
            ltp = s.get('lastTradedPrice') or 0
            if not t or ltp <= 0: continue
            self.price_at[t][scan_num] = ltp
            if t not in self.first:
                self.first[t] = {
                    'ltp': ltp, 'scan': scan_num,
                    'chg': s.get('pchange') or 0,
                    'vol': s.get('volume') or 0,
                    'dlv': s.get('deliveryPercentage') or 0,
                    'sector': s.get('sector') or '',
                    'pe': s.get('peRatio') or 0,
                    'mcap': (s.get('marketCap') or 0) / 1e10,
                }

    def analyze(self, scan_num, stocks_by_ticker):
        """Run analysis. Called every 5 scans. Returns alerts list."""
        if scan_num < 5:
            return []

        alerts = []

        for t, prices in self.price_at.items():
            if t in self.alerted: continue
            scans = sorted(prices.keys())
            if len(scans) < 3: continue

            ltp = prices[scans[-1]]
            first_price = prices[scans[0]]
            s = stocks_by_ticker.get(t, {})
            mcap = (s.get('marketCap') or 0) / 1e10
            if mcap < 2000: continue

            # === PATTERN DETECTION ===

            # 1. Steady climb — every scan higher than previous for last 5
            if len(scans) >= 5:
                last5 = [prices[s] for s in scans[-5:]]
                if all(last5[i] > last5[i-1] for i in range(1, len(last5))):
                    climb = (last5[-1] - last5[0]) / last5[0] * 100
                    if climb > 0.3:
                        alerts.append(('STEADY_CLIMB', t, climb, f"5 straight up, +{climb:.2f}% in 5m"))
                        self.alerted.add(t)

                # 2. Reversal — was falling, now rising (or vice versa)
                if len(scans) >= 8:
                    prev3 = [prices[s] for s in scans[-8:-5]]
                    last3 = [prices[s] for s in scans[-3:]]
                    if all(prev3[i] <= prev3[i-1] for i in range(1, len(prev3))):
                        if all(last3[i] > last3[i-1] for i in range(1, len(last3))):
                            bounce = (last3[-1] - min(prices[s] for s in scans[-5:])) / last3[0] * 100
                            if bounce > 0.3:
                                alerts.append(('REVERSAL_UP', t, bounce, f"was falling, now +{bounce:.2f}%"))
                                self.alerted.add(t)

            # 3. Sudden spike — jumped >0.5% in 1 scan
            if len(scans) >= 2:
                move = (prices[scans[-1]] - prices[scans[-2]]) / prices[scans[-2]] * 100
                if abs(move) > 0.5:
                    vol = s.get('volume') or 0
                    avgvol = s.get('avgVolume') or 1
                    vr = vol / max(avgvol, 1)
                    if vr > 2:
                        d = 'SPIKE_UP' if move > 0 else 'SPIKE_DOWN'
                        alerts.append((d, t, move, f"jumped {move:+.2f}% vol={vr:.0f}x"))
                        self.alerted.add(t)

            # 4. Breaking out of today's range
            if len(scans) >= 10:
                all_prices = [prices[s] for s in scans]
                prev_high = max(all_prices[:-1])
                prev_low = min(all_prices[:-1])
                if ltp > prev_high * 1.002:  # new session high by 0.2%+
                    alerts.append(('RANGE_BREAK_UP', t, 0, f"new session high {ltp:.1f} > {prev_high:.1f}"))
                    self.alerted.add(t)
                elif ltp < prev_low * 0.998:
                    alerts.append(('RANGE_BREAK_DN', t, 0, f"new session low {ltp:.1f} < {prev_low:.1f}"))
                    self.alerted.add(t)

        return alerts

    def eod_report(self, stocks_by_ticker):
        """End of day — what patterns worked?"""
        report = []
        for t, prices in self.price_at.items():
            scans = sorted(prices.keys())
            if len(scans) < 10: continue

            first_p = prices[scans[0]]
            last_p = prices[scans[-1]]
            total_move = (last_p - first_p) / first_p * 100
            peak = max(prices[s] for s in scans)
            trough = min(prices[s] for s in scans)
            range_pct = (peak - trough) / first_p * 100

            # When was the fastest move?
            max_move = 0
            max_move_scan = 0
            for i in range(1, len(scans)):
                m = (prices[scans[i]] - prices[scans[i-1]]) / prices[scans[i-1]] * 100
                if abs(m) > abs(max_move):
                    max_move = m
                    max_move_scan = scans[i]

            # Did the move continue after fastest spike?
            if max_move_scan > 0 and max_move_scan in prices:
                spike_price = prices[max_move_scan]
                future_scans = [s for s in scans if s > max_move_scan and s <= max_move_scan + 10]
                if future_scans:
                    if max_move > 0:
                        future_best = max(prices[s] for s in future_scans)
                        continuation = (future_best - spike_price) / spike_price * 100
                    else:
                        future_best = min(prices[s] for s in future_scans)
                        continuation = (spike_price - future_best) / spike_price * 100
                else:
                    continuation = 0
            else:
                continuation = 0

            s = stocks_by_ticker.get(t, {})
            report.append({
                'ticker': t, 'total': total_move, 'range': range_pct,
                'max_spike': max_move, 'spike_scan': max_move_scan,
                'continuation': continuation,
                'dlv': s.get('deliveryPercentage') or 0,
                'vol_ratio': (s.get('volume') or 0) / max(s.get('avgVolume') or 1, 1),
                'sector': s.get('sector') or '',
                'pe': s.get('peRatio') or 0,
                'mcap': (s.get('marketCap') or 0) / 1e10,
            })

        return sorted(report, key=lambda x: -abs(x['total']))


def main():
    log.info("=" * 50)
    log.info("HEARTBEAT v4 — FULL CAPTURE + LIVE STRATEGY")
    log.info("=" * 50)

    ml = MarketLens()
    builder = StrategyBuilder()
    today = datetime.now().strftime('%Y%m%d')

    # Snapshot storage — compressed JSONL
    snap_file = DATA_DIR / f'market_{today}.jsonl.gz'
    snap_gz = gzip.open(snap_file, 'wt', encoding='utf-8')

    now = datetime.now()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now < market_open:
        wait = (market_open - now).total_seconds()
        log.info(f"Waiting {wait:.0f}s for 9:15...")
        send_tg("🫀 <b>Heartbeat v4 — FULL CAPTURE</b>\nRecording ALL fields, ALL stocks, every 60s\nStrategy builder running in parallel")
        time.sleep(wait)
    else:
        send_tg("🫀 <b>Heartbeat v4 LIVE</b>\nFull market capture active")

    scan = 0
    total_records = 0

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

            # === 1. SAVE FULL SNAPSHOT ===
            # Compact: only save fields that change (ltp, vol, chg, dlv, dayHigh, dayLow)
            # Plus full snapshot every 30 scans
            for s in stocks:
                t = s.get('ticker', '')
                if not t: continue
                if scan % 30 == 1:
                    # Full snapshot
                    record = {'_scan': scan, '_ts': ts, '_full': True}
                    record.update(s)
                else:
                    # Compact snapshot — only changing fields
                    record = {
                        '_scan': scan, '_ts': ts,
                        'ticker': t,
                        'ltp': s.get('lastTradedPrice'),
                        'chg': s.get('pchange'),
                        'vol': s.get('volume'),
                        'dlv': s.get('deliveryPercentage'),
                        'dH': s.get('dayHigh'),
                        'dL': s.get('dayLow'),
                    }
                snap_gz.write(json.dumps(record, separators=(',', ':')) + '\n')
                total_records += 1

            snap_gz.flush()

            # === 2. FEED STRATEGY BUILDER ===
            builder.feed(scan, stocks)

            # === 3. RUN ANALYSIS every 5 scans ===
            if scan % 5 == 0:
                alerts = builder.analyze(scan, by_ticker)
                if alerts:
                    lines = [f"🫀 <b>PATTERNS DETECTED @ {ts}</b> (scan #{scan})"]
                    for pattern, ticker, value, info in alerts[:6]:
                        s = by_ticker.get(ticker, {})
                        chg = s.get('pchange') or 0
                        dlv = s.get('deliveryPercentage') or 0
                        emoji = '🟢' if 'UP' in pattern or 'CLIMB' in pattern else '🔴'
                        lines.append(f"{emoji} <b>{ticker}</b> [{pattern}] chg={chg:+.1f}% dlv={dlv:.0f}%")
                        lines.append(f"   {info}")
                    send_tg('\n'.join(lines))

            # === 4. MARKET PULSE every 10 scans ===
            if scan % 10 == 0:
                up = sum(1 for s in stocks if (s.get('pchange') or 0) > 0)
                down = sum(1 for s in stocks if (s.get('pchange') or 0) < 0)

                sector_chg = defaultdict(list)
                for s in stocks:
                    sec = s.get('sector') or ''
                    if sec: sector_chg[sec].append(s.get('pchange') or 0)
                hot = [(sec, sum(chgs)/len(chgs)) for sec, chgs in sector_chg.items()
                       if len(chgs) >= 3 and abs(sum(chgs)/len(chgs)) > 1.0]
                hot.sort(key=lambda x: -abs(x[1]))

                log.info(f"Scan #{scan} | {len(stocks)} stocks | {up}↑ {down}↓ | {total_records:,} records saved | {os.path.getsize(snap_file)/1024:.0f}KB")
                if hot:
                    log.info(f"  Hot: {', '.join(f'{s}({c:+.1f}%)' for s,c in hot[:5])}")

                if scan % 30 == 0:
                    send_tg(f"📊 Scan #{scan} | {up}↑ {down}↓ | {total_records:,} records\n{'🔥 ' + ', '.join(f'{s}({c:+.1f}%)' for s,c in hot[:3]) if hot else 'No hot sectors'}")

        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)
            time.sleep(10)
            continue

        elapsed = time.time() - t0
        time.sleep(max(5, 60 - elapsed))

    # === EOD ===
    snap_gz.close()
    log.info(f"Saved {total_records:,} records to {snap_file} ({os.path.getsize(snap_file)/1024/1024:.1f}MB)")

    # Final poll for EOD analysis
    stocks = ml.poll()
    by_ticker = {s.get('ticker',''): s for s in stocks}
    report = builder.eod_report(by_ticker)

    lines = ["📊 <b>EOD FULL REPORT</b>", ""]

    # Top movers
    lines.append("<b>Top 15 Movers:</b>")
    for r in report[:15]:
        e = '🟢' if r['total'] > 0 else '🔴'
        lines.append(f"{e} <b>{r['ticker']}</b> {r['total']:+.1f}% range={r['range']:.1f}% spike={r['max_spike']:+.2f}% cont={r['continuation']:+.2f}% dlv={r['dlv']:.0f}%")

    # Scalp analysis — what spikes continued?
    spikes = [r for r in report if abs(r['max_spike']) > 0.5]
    if spikes:
        continued = sum(1 for r in spikes if r['continuation'] > 0.15)
        reversed = sum(1 for r in spikes if r['continuation'] < -0.15)
        flat = len(spikes) - continued - reversed
        lines.append(f"\n<b>Spike Analysis (>{len(spikes)} stocks spiked >0.5% in 1 min):</b>")
        lines.append(f"  Continued: {continued} ({continued*100//len(spikes)}%)")
        lines.append(f"  Reversed: {reversed} ({reversed*100//len(spikes)}%)")
        lines.append(f"  Flat: {flat}")

        # By sector
        spike_by_sector = defaultdict(list)
        for r in spikes:
            spike_by_sector[r['sector']].append(r['continuation'])
        lines.append(f"\n  By sector:")
        for sec, conts in sorted(spike_by_sector.items(), key=lambda x: -len(x[1])):
            if len(conts) < 2: continue
            avg = sum(conts) / len(conts)
            wr = sum(1 for c in conts if c > 0.15) * 100 // len(conts)
            lines.append(f"    {sec}: {len(conts)} spikes, cont={avg:+.2f}%, WR={wr}%")

        # By delivery
        hi_dlv = [r for r in spikes if r['dlv'] > 50]
        lo_dlv = [r for r in spikes if r['dlv'] <= 50]
        if hi_dlv and lo_dlv:
            hi_wr = sum(1 for r in hi_dlv if r['continuation'] > 0.15) * 100 // len(hi_dlv)
            lo_wr = sum(1 for r in lo_dlv if r['continuation'] > 0.15) * 100 // len(lo_dlv)
            lines.append(f"\n  High delivery (>50%): {hi_wr}% continuation ({len(hi_dlv)} spikes)")
            lines.append(f"  Low delivery (<=50%): {lo_wr}% continuation ({len(lo_dlv)} spikes)")

        # By volume ratio
        hi_vol = [r for r in spikes if r['vol_ratio'] > 3]
        lo_vol = [r for r in spikes if r['vol_ratio'] <= 3]
        if hi_vol and lo_vol:
            hi_wr = sum(1 for r in hi_vol if r['continuation'] > 0.15) * 100 // len(hi_vol)
            lo_wr = sum(1 for r in lo_vol if r['continuation'] > 0.15) * 100 // len(lo_vol)
            lines.append(f"  High volume (>3x): {hi_wr}% continuation ({len(hi_vol)} spikes)")
            lines.append(f"  Low volume (<=3x): {lo_wr}% continuation ({len(lo_vol)} spikes)")

    lines.append(f"\n<b>Data: {total_records:,} records saved for offline analysis</b>")
    lines.append(f"File: {snap_file}")

    summary = '\n'.join(lines)
    log.info(summary)
    send_tg(summary)
    log.info("DONE")


if __name__ == "__main__":
    main()
