"""Gemini API — call with/without Google Search grounding.

Used by: news_orb (analysis), commodity (news check), any agent needing LLM.
Reusable: no trading logic, just API calls.
"""
import json
import os
import re
import time

import requests

from trading.logger import get_logger, audit

log = get_logger('gemini')


def _get_url():
    key = os.environ.get('GEMINI_API_KEY', '')
    if not key:
        raise RuntimeError('GEMINI_API_KEY not set')
    return f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={key}'


def call(prompt: str, grounding: bool = False, max_retries: int = 3,
         temperature: float = 0) -> str:
    """Call Gemini. Returns response text. Raises on total failure."""
    url = _get_url()
    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': temperature, 'maxOutputTokens': 65536},
    }
    if grounding:
        body['tools'] = [{'google_search': {}}]

    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=body, timeout=120)
            if r.status_code == 200:
                return ''.join(p.get('text', '')
                               for p in r.json()['candidates'][0]['content']['parts'])
            elif r.status_code == 429:
                time.sleep(10 * (attempt + 1))
            else:
                log.error(f'Gemini {r.status_code}: {r.text[:200]}')
                time.sleep(5)
        except Exception as e:
            log.error(f'Gemini error: {e}')
            time.sleep(5)

    raise RuntimeError(f'Gemini failed after {max_retries} retries')


def call_safe(prompt: str, **kw) -> str:
    """Like call() but returns '' on failure instead of raising."""
    try:
        return call(prompt, **kw)
    except Exception as e:
        log.warning(f'Gemini call_safe failed: {e}')
        return ''


def filter_headlines(articles: list[dict]) -> list[dict]:
    """Cheap Gemini call (no grounding) to filter stock-relevant headlines."""
    headlines = '\n'.join(f'[{i}] {a["title"]}' for i, a in enumerate(articles))
    prompt = (
        "Filter Indian stock market headlines. Return JSON array of index numbers "
        "where SOMETHING HAPPENED to a SPECIFIC NSE/BSE listed company.\n"
        "Include: orders, M&A, CEO exit, regulatory, earnings, block deals.\n"
        "Exclude: price pages, technical analysis, IPO GMP, watchlists.\n\n"
        f"{headlines}"
    )
    resp = call_safe(prompt, grounding=False)
    if resp:
        match = re.search(r'\[[\d\s,\n]+\]', resp, re.DOTALL)
        if match:
            try:
                indices = json.loads(match.group())
                filtered = [articles[i] for i in indices if i < len(articles)]
                log.info(f'Headlines: {len(articles)} -> {len(filtered)}')
                for a in filtered:
                    audit('gemini', 'HEADLINE_FILTERED', a.get("title", "")[:100],
                          source=a.get("source", ""))
                return filtered
            except:
                pass
    return articles


def analyze_stocks(articles: list[dict], date_str: str,
                   min_projection: float = 3.0) -> list[dict]:
    """Gemini analyzes articles with grounding. Returns trade list."""
    prev_dt = __import__('datetime').datetime.strptime(date_str, '%Y-%m-%d') - __import__('datetime').timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= __import__('datetime').timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')

    headlines = '\n'.join(f'[{i}] [{a["source"]}] {a["title"]}' for i, a in enumerate(articles))
    prompt = (
        f"You are a stock analyst. {date_str}. Indian NSE/BSE.\n\n"
        f"For EVERY headline:\n"
        f"1. Identify the EXACT NSE ticker symbol (e.g. UJJIVANSFB not 'Ujjivan Small Finance Bank', E2E not 'E2E Networks', HDFCLIFE not 'HDFC Life') — verify via Google\n"
        f"2. Read the FULL article via Google Search\n"
        f"3. Look up via Google: market cap, previous close on {prev_date}, "
        f"what happened to this stock yesterday, PE ratio, 1-month return, 52-week high\n"
        f"4. Give THREE projections: BULL (best case), BEAR (worst case), NEUTRAL (most likely)\n"
        f"5. Calculate FINAL = (bull + bear + neutral) / 3\n"
        f"6. Classify: INTRADAY (will move by 3:10 PM today) or DELIVERY (multi-day/week play)\n"
        f"7. TRADE only INTRADAY moves where |FINAL| >= {min_projection}%\n\n"
        f"SKIP if stock already moved >3% on {prev_date} on this same news.\n"
        f"SKIP if market cap > Rs 1,00,000 cr AND event is not extreme.\n"
        f"SKIP if news impact is too small relative to market cap.\n"
        f"SKIP if the move is DELIVERY (gradual over days/weeks, not same-day).\n\n"
        f"{headlines}\n\n"
        f"Output raw JSON for EVERY stock (both TRADE and SKIP).\n"
        f"CRITICAL: symbol and nse_symbol MUST be the exact NSE ticker (e.g. UJJIVANSFB, E2E, HDFCLIFE, TATACHEM).\n"
        f'[{{"symbol":"TICKER","nse_symbol":"TICKER","decision":"TRADE",'
        f'"bull":"+N%","bear":"+N%","neutral":"+N%","projection":"+N%",'
        f'"move_type":"INTRADAY","mcap":"Ncr","why":"reason"}}]\n'
        f"No markdown fences."
    )

    resp = call_safe(prompt, grounding=True)
    if not resp:
        log.warning('Gemini returned empty response for analyze_stocks')
        audit('gemini', 'EMPTY_RESPONSE', reason='analyze_stocks returned empty')
        return []

    log.info('Gemini response (%d chars): %s', len(resp), resp[:500])
    audit('gemini', 'RAW_RESPONSE', chars=len(resp), preview=resp[:300])

    trades = []
    clean = resp.replace('```json', '').replace('```', '').strip()
    match = re.search(r'\[.*\]', clean, re.DOTALL)
    if not match:
        audit('gemini', 'NO_JSON_MATCH', preview=clean[:200])
        return []

    try:
        for item in json.loads(match.group()):
            sym = item.get('symbol', '').strip().upper()
            if not sym or sym in {'BUY', 'SELL', 'NSE', 'BSE', 'SEBI', 'SKIP'}:
                continue
            if item.get('decision', '').upper() == 'SKIP':
                audit('gemini', 'SIGNAL_SKIP', sym, why=item.get("why", "")[:100],
                      decision='SKIP')
                continue

            # Calculate projection
            bm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bull', '0')))
            bem = re.search(r'([+-]?\d+\.?\d*)', str(item.get('bear', '0')))
            nm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('neutral', '0')))
            if bm and bem and nm:
                proj = (float(bm.group(1)) + float(bem.group(1)) + float(nm.group(1))) / 3
            else:
                pm = re.search(r'([+-]?\d+\.?\d*)', str(item.get('projection', '0')))
                proj = float(pm.group(1)) if pm else 0

            if abs(proj) < min_projection:
                audit('gemini', 'SIGNAL_LOW', sym, projection=round(proj, 1),
                      threshold=min_projection, why=item.get("why", "")[:100])
                continue

            nse_sym = item.get('nse_symbol', sym).strip().upper()

            # Resolve symbol — try nse_symbol first, then symbol, then fuzzy match
            resolved = _resolve_nse_symbol(nse_sym, sym)

            trades.append({
                'symbol': resolved,
                'nse_symbol': resolved,
                'call': 'BUY' if proj > 0 else 'SELL',
                'projection': round(proj, 1),
                'why': item.get('why', ''),
            })
            audit('gemini', 'SIGNAL_TRADE', resolved,
                  direction='BUY' if proj > 0 else 'SELL',
                  projection=round(proj, 1), why=item.get("why", "")[:100])
    except Exception as e:
        log.error(f'Parse: {e}')
        audit('gemini', 'PARSE_ERROR', error=str(e)[:200])

    return trades


# Symbol resolution — loaded once
_SCRIP_CODES = None
_NAME_MAP = None

def _load_scrips():
    global _SCRIP_CODES, _NAME_MAP
    if _SCRIP_CODES is not None:
        return

    import json
    from pathlib import Path

    _SCRIP_CODES = set()
    _NAME_MAP = {}

    # Try multiple paths to find scrip data
    base = Path(__file__).parent.parent.parent  # trading_v2/
    project = base.parent  # news-trading/

    # Load all_nse_scrips.json
    for scrip_path in [project / 'data' / 'all_nse_scrips.json',
                        project / 'live' / 'data' / 'all_nse_scrips.json',
                        base / 'data' / 'all_nse_scrips.json']:
        if scrip_path.exists():
            try:
                data = json.loads(scrip_path.read_text())
                if isinstance(data, dict):
                    _SCRIP_CODES = set(data.keys())
                elif isinstance(data, list):
                    _SCRIP_CODES = set(data)
                log.info(f'Loaded {len(_SCRIP_CODES)} scrips from {scrip_path}')
                break
            except:
                pass

    # Fallback: try broker import
    if not _SCRIP_CODES:
        try:
            import sys
            sys.path.insert(0, str(project))
            from live import indmoney_client as api
            _SCRIP_CODES = set(api.SCRIP_CODES.keys())
            log.info(f'Loaded {len(_SCRIP_CODES)} scrips from broker')
        except:
            log.warning('Could not load scrip codes for symbol resolution')

    # Name to symbol mapping
    for name_path in [project / 'data' / 'name_to_symbol.json',
                       project / 'live' / 'data' / 'name_to_symbol.json']:
        if name_path.exists():
            try:
                _NAME_MAP = json.loads(name_path.read_text())
                log.info(f'Loaded {len(_NAME_MAP)} name mappings')
                break
            except:
                pass


def _resolve_nse_symbol(nse_sym: str, raw_sym: str) -> str:
    """Resolve to valid NSE ticker. Tries exact match, then fuzzy."""
    _load_scrips()

    # 1. Exact match on nse_symbol
    if nse_sym in _SCRIP_CODES:
        return nse_sym

    # 2. Exact match on raw symbol
    if raw_sym in _SCRIP_CODES:
        return raw_sym

    # 3. Remove spaces/suffixes: "E2E NETWORKS" -> "E2E"
    for candidate in [nse_sym, raw_sym]:
        # Try first word
        first = candidate.split()[0] if ' ' in candidate else candidate
        if first in _SCRIP_CODES:
            return first

        # Try without common suffixes
        for suffix in [' LTD', ' LIMITED', ' INDIA', ' CORP', ' PHARMA',
                       ' BANK', ' FINANCE', ' TECHNOLOGIES', ' NETWORKS',
                       ' SMALL FINANCE BANK', ' INDUSTRIES']:
            cleaned = candidate.replace(suffix, '').strip()
            if cleaned in _SCRIP_CODES:
                return cleaned

    # 4. Name map lookup
    if _NAME_MAP:
        for candidate in [nse_sym, raw_sym]:
            mapped = _NAME_MAP.get(candidate) or _NAME_MAP.get(candidate.title())
            if mapped and mapped in _SCRIP_CODES:
                return mapped

    # 5. Partial match — ONLY if exactly 1 match (no ambiguity)
    key = nse_sym.split()[0] if ' ' in nse_sym else nse_sym
    if len(key) >= 4:  # min 4 chars to avoid too-broad matches
        matches = [s for s in _SCRIP_CODES if key in s]
        if len(matches) == 1:
            log.info(f'Symbol resolved: {nse_sym} -> {matches[0]}')
            return matches[0]
        elif len(matches) > 1:
            # Try exact start match
            starts = [s for s in matches if s.startswith(key)]
            if len(starts) == 1:
                log.info(f'Symbol resolved (prefix): {nse_sym} -> {starts[0]}')
                return starts[0]

    # 6. Ask Gemini for exact NSE ticker
    resolved = _gemini_resolve(nse_sym, raw_sym)
    if resolved and resolved in _SCRIP_CODES:
        log.info(f'Gemini resolved: {nse_sym} -> {resolved}')
        return resolved

    log.warning(f'Could not resolve symbol: {nse_sym} / {raw_sym}')
    return nse_sym


def _gemini_resolve(nse_sym: str, raw_sym: str) -> str:
    """Last resort: ask Gemini for exact NSE ticker."""
    resp = call_safe(
        f'What is the exact NSE equity ticker symbol for "{raw_sym}" or "{nse_sym}"? '
        f'It must be the main company stock listed on NSE in EQ series (not T2T/BE). '
        f'Reply with ONLY the ticker, nothing else. If not listed on NSE EQ, reply NONE.',
        grounding=False
    )
    if resp:
        ticker = resp.strip().upper().split()[0]
        if ticker == 'NONE':
            return ''
        if 3 <= len(ticker) <= 15 and ticker.isalnum():
            return ticker
    return ''
