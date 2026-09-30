import sys
sys.path.insert(0, "/home/ai18developer/news-trading")
"""
CAS Paper Trade Bot — Runs at 2:30 PM on expiry days.
1. Calls Gemini with force alignment prompt
2. Logs prediction
3. After 3:35 PM, fetches actual CAS closing price
4. Compares and logs result

Usage:
  python cas_paper_trade.py          # Run prediction at 2:30 PM
  python cas_paper_trade.py --check  # Check result after 3:35 PM
  python cas_paper_trade.py --auto   # Full auto: predict at 2:30, check at 3:40
"""
import json, os, sys, time, re, warnings
from datetime import datetime, timedelta
from pathlib import Path
import argparse

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')
import requests
from live.master_client import request_trade, report_exit

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_KEY}'
LOG_FILE = Path(__file__).parent / 'cas_paper_trades.json'


def get_today_expiry():
    """Check if today is an expiry day and return (index, expiry_type)."""
    today = datetime.now()
    weekday = today.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu

    if weekday == 1:  # Tuesday = Nifty
        # Check if last Tuesday of month (monthly expiry)
        next_tue = today + timedelta(days=7)
        if next_tue.month != today.month:
            return 'NIFTY', 'monthly'
        return 'NIFTY', 'weekly'
    elif weekday == 3:  # Thursday = Sensex
        next_thu = today + timedelta(days=7)
        if next_thu.month != today.month:
            return 'SENSEX', 'monthly'
        return 'SENSEX', 'weekly'
    else:
        return None, None


def call_gemini(prompt):
    """Call Gemini Flash with Google Search grounding."""
    for attempt in range(3):
        try:
            resp = requests.post(GEMINI_URL,
                json={
                    'contents': [{'parts': [{'text': prompt}]}],
                    'tools': [{'google_search': {}}],
                    'generationConfig': {'temperature': 0, 'maxOutputTokens': 4000},
                },
                timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
                text = ''.join(p.get('text', '') for p in parts)
                return text
            elif resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
            else:
                print(f"  Gemini error {resp.status_code}: {resp.text[:200]}")
                time.sleep(5)
        except Exception as e:
            print(f"  Gemini request error: {e}")
            time.sleep(5)
    return None


def build_cas_prompt(date_str, index_name, expiry_type):
    """Build the CAS direction prediction prompt."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    prev_dt = dt - timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    return f"""You are an institutional derivatives strategist specializing in CAS (Closing Auction Session) on Indian expiry days. Date: {date_str}. Time: 2:30 PM IST.
Today is {expiry_type.upper()} EXPIRY for {index_name}.

CAS runs 3:15-3:35 PM. Institutions push closing price to maximize their expiring option positions. The direction depends on WHERE the largest open interest is concentrated.

SEARCH GOOGLE FOR ALL OF THE FOLLOWING (be thorough):

MACRO FORCES:
1. FPI (Foreign Portfolio Investor) cash market data for {date_str} — net buy or sell amount in Crores
2. DII (Domestic Institutional Investor) cash market data for {date_str} — net buy or sell amount
3. Global cues: US futures (S&P, Nasdaq), European markets, Asian close
4. Crude oil price and direction today
5. USD/INR movement today
6. India VIX level and change today
7. Any RBI announcement, geopolitical event, or macro news today

MICRO FORCES (OPTION POSITIONING — this is what actually drives CAS):
8. {index_name} current level right now
9. {index_name} weekly expiry MAX PAIN strike level
10. {index_name} Put-Call Ratio (PCR) for today's expiry
11. Which strikes have highest CALL OI and highest PUT OI for today's expiry
12. Is {index_name} currently ABOVE or BELOW max pain? (If above max pain, institutions push DOWN to max pain. If below, push UP.)
13. Net change in OI at key strikes in last 2 days (call writing = bearish, put writing = bullish)

ANALYSIS:
- If index is ABOVE max pain → CAS likely pushes DOWN toward max pain
- If index is BELOW max pain → CAS likely pushes UP toward max pain
- If FPI+DII both buying heavily → overrides max pain, pushes UP
- If FPI+DII both selling → overrides max pain, pushes DOWN
- If PCR > 1.2 → bullish (more puts sold = support)
- If PCR < 0.8 → bearish (more calls sold = resistance)

You MUST reply in EXACTLY this format:
DIRECTION: UP or DOWN
CONFIDENCE: decimal between 1.0 and 10.0 (7.5=borderline, 8.0=clear, 9.0=very clear)
MAX_PAIN: the max pain strike level you found
CURRENT: current index level
POSITION: ABOVE or BELOW max pain
PCR: the PCR value you found
FPI: net amount (positive=buying, negative=selling)
DII: net amount
REASON: one line combining macro + micro forces"""


def predict(date_str=None):
    """Run prediction for today or given date."""
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')

    index_name, expiry_type = get_today_expiry()
    if not index_name:
        # Allow manual override
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        if dt.weekday() == 1:
            index_name, expiry_type = 'NIFTY', 'weekly'
        elif dt.weekday() == 3:
            index_name, expiry_type = 'SENSEX', 'weekly'
        else:
            print(f"  {date_str} is not an expiry day (not Tue/Thu)")
            return None

    print(f"\n{'='*60}")
    print(f"  CAS PAPER TRADE — {date_str}")
    print(f"  {index_name} {expiry_type} expiry")
    print(f"{'='*60}")

    prompt = build_cas_prompt(date_str, index_name, expiry_type)
    response = call_gemini(prompt)

    if not response:
        print("  ERROR: No Gemini response")
        return None

    # Parse JSON
    json_match = re.search(r'\{[^{}]*"prediction"[^{}]*\}', response, re.DOTALL)
    if not json_match:
        json_match = re.search(r'\{.*?\}', response, re.DOTALL)

    prediction = None
    if json_match:
        try:
            prediction = json.loads(json_match.group())
        except json.JSONDecodeError:
            cleaned = re.sub(r',\s*}', '}', json_match.group())
            try:
                prediction = json.loads(cleaned)
            except:
                pass

    if not prediction:
        text_upper = response.upper()
        if 'SKIP' in text_upper:
            prediction = {'prediction': 'SKIP', 'confidence': 0}
        elif 'DOWN' in text_upper:
            prediction = {'prediction': 'DOWN', 'confidence': 5}
        elif 'UP' in text_upper:
            prediction = {'prediction': 'UP', 'confidence': 5}
        else:
            prediction = {'prediction': 'UNKNOWN', 'confidence': 0}

    # Handle both JSON and text format responses
    if isinstance(prediction, dict):
        pred = prediction.get('prediction', 'UNKNOWN').upper()
        conf = float(prediction.get('confidence', 0))
    elif isinstance(prediction, str):
        import re as _re
        d = _re.search(r'DIRECTION:\s*(UP|DOWN)', prediction, _re.I)
        c = _re.search(r'CONFIDENCE:\s*([\d.]+)', prediction, _re.I)
        pred = d.group(1).upper() if d else 'UNKNOWN'
        conf = float(c.group(1)) if c else 0
    else:
        pred = 'UNKNOWN'
        conf = 0
    proj = prediction.get('projection_pct', '?') if isinstance(prediction, dict) else '?'
    force = prediction.get('dominant_force', '?') if isinstance(prediction, dict) else '?'
    bull = prediction.get('bullish_score', '?') if isinstance(prediction, dict) else '?'
    bear = prediction.get('bearish_score', '?') if isinstance(prediction, dict) else '?'
    fpi_dii = prediction.get('fpi_dii_signal', '?') if isinstance(prediction, dict) else '?'
    # Try to extract from text format
    if isinstance(prediction, str):
        mp = _re.search(r'MAX_PAIN:\s*(\d+)', prediction, _re.I)
        pcr = _re.search(r'PCR:\s*([\d.]+)', prediction, _re.I)
        reason = _re.search(r'REASON:\s*(.+)', prediction, _re.I)
        force = reason.group(1).strip() if reason else '?'
        fpi_dii = f'MaxPain={mp.group(1) if mp else "?"} PCR={pcr.group(1) if pcr else "?"}' 

    print(f"\n  PREDICTION: {pred}")
    print(f"  CONFIDENCE: {conf}")
    print(f"  PROJECTION: {proj}")
    print(f"  FORCE:      {force}")
    print(f"  BULL/BEAR:  {bull} vs {bear}")
    print(f"  FPI/DII:    {fpi_dii}")

    # Decision
    if pred == 'SKIP' or conf < 7.5:
        action = 'NO TRADE'
        reason = f"{'SKIP signal' if pred == 'SKIP' else f'Confidence {conf} < 7.5'}"
        print(f"\n  ACTION: NO TRADE ({reason})")
    else:
        option_type = 'CE' if pred == 'UP' else 'PE'
        action = f"BUY ATM {index_name} {option_type} at 3:15 PM"
        print(f"\n  ACTION: {action}")
        print(f"  EXIT:   Hold through CAS, exit/settle by 3:40 PM")

        # Ask Master for capital
        master_trade_id = None
        try:
            master_resp = request_trade('cas', index_name, conviction=conf, projection=0)
            if master_resp and master_resp.get('approved'):
                master_trade_id = master_resp['trade_id']
                cas_amount = master_resp['amount']
                print(f"  MASTER: Approved Rs {cas_amount:,} (trade_id={master_trade_id})")
            elif master_resp:
                print(f"  MASTER: Rejected — {master_resp.get('reason', '?')}")
            else:
                print(f"  MASTER: Unavailable, proceeding with default Rs 5,000")
                cas_amount = 5000
        except:
            print(f"  MASTER: Error, proceeding with default Rs 5,000")
            cas_amount = 5000

    # Log
    trade_log = {
        'date': date_str,
        'index': index_name,
        'expiry_type': expiry_type,
        'prediction': pred,
        'confidence': conf,
        'projection': proj,
        'dominant_force': force,
        'bullish_score': bull,
        'bearish_score': bear,
        'fpi_dii': fpi_dii,
        'action': action,
        'reasoning': str(prediction.get('reasoning', ''))[:300],
        'timestamp_predict': datetime.now().isoformat(),
        'actual_direction': None,
        'actual_gap_pct': None,
        'result': None,
    }

    # Load existing log
    trades = []
    if LOG_FILE.exists():
        trades = json.loads(LOG_FILE.read_text())

    # Update or append
    existing = next((i for i, t in enumerate(trades) if t['date'] == date_str and t['index'] == index_name), None)
    if existing is not None:
        trades[existing].update(trade_log)
    else:
        trades.append(trade_log)

    LOG_FILE.write_text(json.dumps(trades, indent=2, default=str))
    print(f"\n  Logged to {LOG_FILE}")
    return trade_log


def check_result(date_str=None):
    """Check actual CAS result and compare with prediction."""
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')

    if not LOG_FILE.exists():
        print("No trades logged yet")
        return

    trades = json.loads(LOG_FILE.read_text())
    trade = next((t for t in trades if t['date'] == date_str), None)

    if not trade:
        print(f"No prediction found for {date_str}")
        return

    index_name = trade['index']
    print(f"\n{'='*60}")
    print(f"  CHECKING RESULT — {date_str} {index_name}")
    print(f"{'='*60}")

    # Fetch actual closing price via Google search
    response = call_gemini(f"""Search for the {index_name} closing price on {date_str}.
I need:
1. The {index_name} level at 3:15 PM (before CAS)
2. The official CAS closing price
3. The difference in points and percentage

Search: "{index_name} closing price {date_str}", "{index_name} CAS {date_str}"

OUTPUT JSON only:
{{"index": "{index_name}", "price_315pm": 24500.00, "cas_close": 24650.00, "gap_pts": 150.0, "gap_pct": 0.61}}""")

    if not response:
        print("  Could not fetch actual data")
        return

    # Parse
    json_match = re.search(r'\{[^{}]*"cas_close"[^{}]*\}', response, re.DOTALL)
    if not json_match:
        json_match = re.search(r'\{.*?\}', response, re.DOTALL)

    actual = None
    if json_match:
        try:
            actual = json.loads(json_match.group())
        except:
            pass

    if not actual:
        print(f"  Could not parse result. Raw: {response[:200]}")
        return

    price_315 = actual.get('price_315pm', 0)
    cas_close = actual.get('cas_close', 0)
    gap_pts = actual.get('gap_pts', 0)
    gap_pct = actual.get('gap_pct', 0)

    actual_dir = 'UP' if gap_pts > 0 else 'DOWN' if gap_pts < 0 else 'FLAT'

    print(f"  3:15 PM:    {price_315}")
    print(f"  CAS Close:  {cas_close}")
    print(f"  Gap:        {gap_pts:+.0f} pts ({gap_pct:+.2f}%)")
    print(f"  Direction:  {actual_dir}")

    pred = trade['prediction']
    conf = trade['confidence']

    if trade['action'] == 'NO TRADE':
        was_small = abs(gap_pct) < 0.3
        result = 'CORRECT_SKIP' if was_small else 'MISSED_OPPORTUNITY'
    elif pred == actual_dir:
        result = 'WIN'
    else:
        result = 'LOSS'

    # Option PnL estimate
    if result == 'WIN' and abs(gap_pts) > 0:
        # Near-expiry ATM option: premium ~₹15-30, moves ~= index gap
        est_entry = 25  # conservative ATM premium at 3:15
        est_exit = abs(gap_pts) if abs(gap_pts) > est_entry else est_entry * 1.5
        est_return = est_exit / est_entry
        print(f"\n  Est Option: ₹{est_entry} → ₹{est_exit:.0f} = {est_return:.1f}x")

    print(f"\n  PREDICTION: {pred} (conf {conf})")
    print(f"  ACTUAL:     {actual_dir} ({gap_pts:+.0f} pts)")
    print(f"  RESULT:     {'*** ' + result + ' ***'}")

    # Update log
    for t in trades:
        if t['date'] == date_str and t['index'] == index_name:
            t['actual_direction'] = actual_dir
            t['actual_gap_pct'] = gap_pct
            t['actual_gap_pts'] = gap_pts
            t['result'] = result
            t['timestamp_check'] = datetime.now().isoformat()

    LOG_FILE.write_text(json.dumps(trades, indent=2, default=str))

    # Print running summary
    print(f"\n{'='*60}")
    print(f"  RUNNING SUMMARY")
    print(f"{'='*60}")
    completed = [t for t in trades if t.get('result')]
    wins = [t for t in completed if t['result'] == 'WIN']
    losses = [t for t in completed if t['result'] == 'LOSS']
    skips = [t for t in completed if 'SKIP' in (t['result'] or '')]
    missed = [t for t in completed if t['result'] == 'MISSED_OPPORTUNITY']

    print(f"  Total days: {len(completed)}")
    print(f"  Trades:     {len(wins)}W / {len(losses)}L")
    if wins or losses:
        wr = len(wins) / (len(wins) + len(losses)) * 100
        print(f"  Win Rate:   {wr:.0f}%")
    print(f"  Skips:      {len(skips)} correct, {len(missed)} missed")

    for t in sorted(completed, key=lambda x: x['date']):
        r = t.get('result', '?')
        gap = t.get('actual_gap_pts', 0) or 0
        print(f"    {t['date']} {t['index']:>7} conf={t['confidence']} "
              f"pred={t['prediction']:>4} actual={t.get('actual_direction','?'):>4} "
              f"gap={gap:+.0f} → {r}")


def main():
    parser = argparse.ArgumentParser(description='CAS Paper Trade Bot')
    parser.add_argument('--check', action='store_true', help='Check result after 3:35 PM')
    parser.add_argument('--auto', action='store_true', help='Auto mode: predict + wait + check')
    parser.add_argument('--date', type=str, help='Override date (YYYY-MM-DD)')
    parser.add_argument('--summary', action='store_true', help='Show all results')
    args = parser.parse_args()

    if not GEMINI_KEY:
        print("Set GEMINI_API_KEY in .env")
        sys.exit(1)

    date_str = args.date or datetime.now().strftime('%Y-%m-%d')

    if args.summary:
        if LOG_FILE.exists():
            trades = json.loads(LOG_FILE.read_text())
            completed = [t for t in trades if t.get('result')]
            print(f"\n  ALL CAS PAPER TRADES")
            print(f"  {'='*60}")
            for t in sorted(completed, key=lambda x: x['date']):
                gap = t.get('actual_gap_pts', 0) or 0
                print(f"  {t['date']} {t['index']:>7} conf={t['confidence']} "
                      f"pred={t['prediction']:>4} actual={t.get('actual_direction','?'):>4} "
                      f"gap={gap:+.0f} → {t.get('result','?')}")
            wins = len([t for t in completed if t['result'] == 'WIN'])
            total = len([t for t in completed if t['result'] in ('WIN', 'LOSS')])
            if total:
                print(f"\n  Win Rate: {wins}/{total} = {wins/total*100:.0f}%")
        return

    if args.check:
        check_result(date_str)
    elif args.auto:
        # Predict
        trade = predict(date_str)
        if trade:
            now = datetime.now()
            check_time = now.replace(hour=15, minute=40, second=0)
            if now < check_time:
                wait = (check_time - now).total_seconds()
                print(f"\n  Waiting {wait/60:.0f} minutes until 3:40 PM to check result...")
                time.sleep(wait)
            check_result(date_str)
    else:
        predict(date_str)


if __name__ == '__main__':
    main()
