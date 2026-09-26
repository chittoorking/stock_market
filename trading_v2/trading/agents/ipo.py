"""IPO Listing Day Agent — Gemini-powered IPO listing day trading.

NSE API has no listingDate field. So we:
  1. Fetch active IPOs from NSE
  2. Use Gemini (Google Search) to find which ones list TODAY + GMP
  3. At 10:00 AM, check listing price vs issue price
  4. Trade if Gemini says BUY (based on GMP, subscription, fundamentals)

Listing happens at 10:00 AM on NSE (T+3 after issue close).
"""
import json
import re
import time
from datetime import datetime

import requests

from trading.base_agent import BaseAgent
from trading.types import TradeRequest, Side
from trading.services import gemini
from trading.logger import audit

NSE_IPO_URL = 'https://www.nseindia.com/api/ipo-current-issue'
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


class IpoAgent(BaseAgent):
    name = 'ipo'

    def run(self):
        """Check for IPO listings today, Gemini analyze, trade."""
        now = datetime.now()
        if now.weekday() >= 5:
            self._log.info('Weekend, skipping')
            return

        self._log.info('Checking for IPO listings today')

        # Phase 1: Get active IPOs from NSE
        ipos = self._fetch_nse_ipos()
        if not ipos:
            self._log.info('No active IPOs on NSE')
            return

        # Phase 2: Ask Gemini which ones list today + analysis
        today_str = now.strftime('%Y-%m-%d')
        listings = self._gemini_check_listings(ipos, today_str)
        if not listings:
            self._log.info('No IPO listings today')
            return

        self._log.info(f'{len(listings)} IPOs listing today')

        # Phase 3: Wait until 10:00 AM (listing time)
        listing_time = now.replace(hour=10, minute=0, second=0)
        if now < listing_time:
            wait = (listing_time - now).total_seconds()
            if wait > 7200:
                self._log.info(f'Too early ({wait:.0f}s to 10 AM), deferring')
                return
            self._log.info(f'Waiting {wait:.0f}s for 10:00 AM listing')
            time.sleep(wait)

        # Phase 4: Check listing price + trade
        time.sleep(60)  # give 1 min for price to settle
        for ipo in listings:
            self._evaluate_and_trade(ipo)

    def _fetch_nse_ipos(self) -> list:
        """Fetch active IPOs from NSE API."""
        try:
            r = requests.get(NSE_IPO_URL, headers=H, timeout=15)
            if r.status_code != 200:
                self._log.warning(f'NSE IPO API: {r.status_code}')
                return []
            data = r.json()
            ipos = []
            for item in data if isinstance(data, list) else []:
                sym = item.get('symbol', '')
                name = item.get('companyName', '')
                price_str = item.get('issuePrice', '')
                sub_times = item.get('noOfTime', '0')

                # Parse issue price: "Rs.40 to Rs.43" → take upper band
                issue_price = self._parse_issue_price(price_str)

                ipos.append({
                    'symbol': sym,
                    'name': name,
                    'issue_price': issue_price,
                    'price_str': price_str,
                    'subscription': float(sub_times) if sub_times else 0,
                    'end_date': item.get('issueEndDate', ''),
                })
            return ipos
        except Exception as e:
            self._log.warning(f'NSE IPO error: {e}')
            return []

    def _parse_issue_price(self, price_str: str) -> float:
        """Parse 'Rs.40 to Rs.43' → 43.0 (upper band)."""
        if not price_str:
            return 0
        nums = re.findall(r'(\d+\.?\d*)', str(price_str))
        if nums:
            return float(nums[-1])  # take highest number
        return 0

    def _gemini_check_listings(self, ipos: list, today: str) -> list:
        """Ask Gemini which IPOs list today + GMP + recommendation."""
        ipo_text = '\n'.join(
            f'[{i}] {p["name"]} ({p["symbol"]}) issue={p["price_str"]} '
            f'sub={p["subscription"]:.1f}x end={p["end_date"]}'
            for i, p in enumerate(ipos)
        )

        prompt = f"""Today is {today}. These IPOs are active on NSE:

{ipo_text}

Use Google Search to find:
1. Which of these IPOs are LISTING TODAY (T+3 after issue close)?
2. GMP (Grey Market Premium) for each listing today
3. Subscription data — total, retail, QIB, NII
4. Company fundamentals — is it a good business?
5. Market sentiment — bullish or bearish for IPO listings today?

For each IPO listing today, recommend:
- BUY (if GMP > 20%, subscription > 3x, good business)
- SELL (if GMP < 0% or very overvalued)
- SKIP (if uncertain)

Output JSON array (only IPOs listing TODAY):
[{{"symbol":"TICKER","listing_today":true,"gmp_pct":N,"subscription_x":N.N,"call":"BUY/SELL/SKIP","issue_price":N,"reason":"brief"}}]
If none list today, return empty array [].
No markdown."""

        resp = gemini.call_safe(prompt, grounding=True)
        if not resp:
            return []

        clean = resp.replace('```json', '').replace('```', '').strip()
        m = re.search(r'\[.*\]', clean, re.DOTALL)
        if not m:
            return []

        try:
            items = json.loads(m.group())
            listings = []
            for item in items:
                if not item.get('listing_today'):
                    continue
                sym = item.get('symbol', '').upper()
                call = item.get('call', 'SKIP').upper()
                if call == 'SKIP':
                    self._log.info(f'IPO SKIP: {sym} | {item.get("reason", "")}')
                    continue

                # Find matching NSE data
                issue_price = item.get('issue_price', 0)
                if not issue_price:
                    for p in ipos:
                        if p['symbol'] == sym:
                            issue_price = p['issue_price']
                            break

                listings.append({
                    'symbol': sym,
                    'issue_price': issue_price,
                    'gmp_pct': item.get('gmp_pct', 0),
                    'subscription': item.get('subscription_x', 0),
                    'call': call,
                    'reason': item.get('reason', ''),
                })
                self._log.info(f'IPO LISTING: {sym} {call} GMP={item.get("gmp_pct", 0)}% '
                               f'sub={item.get("subscription_x", 0)}x | {item.get("reason", "")}')
            return listings
        except Exception as e:
            self._log.error(f'IPO parse error: {e}')
            return []

    def _evaluate_and_trade(self, ipo: dict):
        """Check actual listing price and trade."""
        sym = ipo['symbol']
        issue_price = ipo['issue_price']
        call = ipo['call']

        if not sym or issue_price <= 0:
            return

        # Get listing price
        ltp = self._broker.ltp_safe(sym)
        if ltp <= 0:
            # Retry after 2 min
            self._log.info(f'{sym}: no LTP, retrying in 2 min')
            time.sleep(120)
            ltp = self._broker.ltp_safe(sym)
            if ltp <= 0:
                self._log.warning(f'{sym}: still no LTP, skipping')
                return

        premium_pct = (ltp - issue_price) / issue_price * 100
        self._log.info(f'{sym}: issue={issue_price} ltp={ltp:.1f} premium={premium_pct:+.1f}%')
        audit('ipo', 'LISTING', sym, issue=issue_price, ltp=ltp,
              premium=round(premium_pct, 1), call=call)

        if call == 'SELL':
            # Short if listing at huge premium (likely to fade)
            if premium_pct < 30:
                self._log.info(f'{sym}: premium {premium_pct:.1f}% too low for short')
                return
            side = Side.SELL
            sl = ltp * 1.05  # 5% SL on short
        else:
            # BUY — only if premium is moderate (5-30%)
            if premium_pct < 5:
                self._log.info(f'{sym}: premium {premium_pct:.1f}% too low, skip')
                return
            if premium_pct > 25:
                self._log.info(f'{sym}: premium {premium_pct:.1f}% too high, skip')
                return
            side = Side.BUY
            sl = ltp * 0.95  # 5% SL on long

        req = TradeRequest(
            symbol=sym,
            side=side,
            sl=sl,
            trail_activate_pct=3.0,
            trail_pct=1.5,
            conviction=7,
            reason=f'IPO {sym} premium={premium_pct:+.1f}% GMP={ipo.get("gmp_pct", 0)}%',
        )

        pos_id = self.submit(req)
        if pos_id:
            self._log.info(f'IPO TRADE: {sym} {side.value} pos={pos_id}')
            audit('ipo', 'TRADE', sym, side=side.value, pos_id=pos_id,
                  premium=round(premium_pct, 1))
