"""
Fast MoE — Single LLM call, 15 expert perspectives in one prompt.
Instead of 16 sequential API calls (~65s), this does 1 call (~3s).
The LLM role-plays all 15 experts and synthesizes.
"""
import json
import os
from typing import Dict, List, Tuple
import requests


def moe_scan_fast(
    context: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Tuple[List[Dict], Dict]:
    """
    Single API call MoE. LLM analyzes from all 15 expert perspectives
    and outputs a synthesized trade decision.
    """
    system_prompt = """You are a trading decision system with 15 domain expert perspectives.
For each signal, you MUST analyze from ALL 15 angles before deciding.

YOUR 15 EXPERT LENSES:
1. VOLUME: RVOL, sustainability, block deal detection, delivery %
2. SECTOR: Leader flow, breadth, laggard confirmation
3. PRICE: Candle quality, VWAP position, noise ratio, gaps
4. MOMENTUM: Morning move alignment, consecutive bars, EMA trend
5. MARKET REGIME: Broad breadth (% stocks up/down), trending vs choppy
6. MACRO: VIX level, US overnight, Nifty previous day
7. RISK: Stop distance, R:R ratio, can this survive Rs 84 charges?
8. TIMING: Bar number, time of day, day of week, expiry effects
9. HISTORY: Has this setup worked before? Stock reliability?
10. CONTRARIAN: What could go wrong? Structural flaws?
11. RELATIVE STRENGTH: Is this stock leading or lagging today?
12. GAP: Gap direction, size, alignment with trade
13. VOLATILITY: ATR%, ORB range, squeeze or expansion?
14. KEY LEVELS: Camarilla S3/R3, prev day H/L, VWAP distance
15. CANDLESTICK: Entry bar pattern, body ratio, wick analysis

PROCESS:
1. Read all signals and context data
2. For each signal, quickly assess all 15 angles
3. Pick the BEST trade(s) — rank by confidence
4. You can pick 1 to 3 trades. Size by confidence:
   - 90%+ confidence: 50% of capital
   - 80-89%: 30% of capital
   - 70-79%: 20% of capital
   - Below 70%: SKIP

OUTPUT FORMAT (JSON only, no markdown):
{"trades": [
  {"symbol":"STOCKNAME","direction":"LONG","strategy":"stratname",
   "confidence":85,"capital_pct":30,
   "reasoning":"1-2 sentence synthesis across expert lenses"},
  ...
], "skipped_reason": "why other signals were skipped"}

If no trade qualifies: {"trades": [], "skipped_reason": "why"}"""

    user_prompt = context

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": 800,
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]

            # Parse JSON from response
            start = raw.find('{')
            end = raw.rfind('}') + 1
            if start >= 0 and end > start:
                parsed = json.loads(raw[start:end])
                trades = parsed.get("trades", [])
                return trades, parsed
            else:
                print(f"  MoE: no JSON in response: {raw[:200]}")
                return [], {"raw": raw[:500]}
        else:
            print(f"  MoE API error {resp.status_code}: {resp.text[:200]}")
            return [], {"error": resp.status_code}
    except Exception as e:
        print(f"  MoE exception: {e}")
        return [], {"error": str(e)}


def moe_monitor_fast(
    position_context: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Dict:
    """
    Called EVERY bar with full live state. LLM manages the position like a prop trader.
    Position is split into 3 lots (A=40%, B=30%, C=30%).
    """
    system_prompt = """You are a prop trader managing an open intraday position. Called every 5 min with LIVE data.

POSITION STRUCTURE: Split into 5 lots (L1=30%, L2=25%, L3=20%, L4=15%, L5=10%).
You see which lots are still open. Book them one by one as price moves.

GOLDEN RULE: Once price moves in your favor, PROTECT those gains.
A trade that peaked at +0.8% should NEVER become a full loss.
Book lots progressively, trail stop behind price.

YOUR ACTIONS:
- hold — no change, thesis intact
- book_l1 — close Lot 1 (30%) at market
- book_l2 — close Lot 2 (25%) at market
- book_l3 — close Lot 3 (20%) at market
- book_l4 — close Lot 4 (15%) at market
- book_l5 — close Lot 5 (10%) at market
- tighten — move stop (set new_stop to price or "breakeven")
- extend — move target (set new_target to new price)
- close_all — exit everything NOW

You can combine: book a lot AND tighten stop in one call.
Goal: capture maximum of MFE. If peak was +1%, capture at least +0.7%.

Output JSON only:
{"action":"hold|book_l1|book_l2|tighten|extend|close_all","new_stop":"price_or_unchanged","new_target":"price_or_unchanged","reason":"1 sentence"}"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": position_context},
        ],
        "temperature": 0,
        "max_tokens": 300,
    }
    try:
        resp = requests.post("https://api.openai.com/v1/chat/completions",
                           headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            start = raw.find('{'); end = raw.rfind('}') + 1
            if start >= 0: return json.loads(raw[start:end])
    except:
        pass
    return {"action": "hold", "reason": "parse_error"}
