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

CRITICAL RULES FROM 40,000 TRADE ANALYSIS:
1. DO NOT tighten stop if trade hasn't moved +0.3% in your favor yet. Be PATIENT.
   Early tightening killed 70% of trades that would have been winners.
2. Only start booking lots AFTER P&L > +0.5%. Before that, HOLD everything.
3. When P&L is between 0% and +0.3%, the ONLY correct action is HOLD.
4. When P&L is negative, NEVER tighten — you'll just get stopped out on noise.
   The original wide stop is correct. Let it breathe.

WHEN TO ACT:
- P&L < 0%: HOLD. Do nothing. Let the stop handle risk.
- P&L 0% to +0.3%: HOLD. Trade is working, don't touch it.
- P&L +0.3% to +0.5%: Move stop to breakeven only. Nothing else.
- P&L > +0.5%: Book L1, trail stop to +0.2%
- P&L > +1.0%: Book L2, trail stop to +0.5%
- P&L > +1.5%: Book L3, trail stop to +1.0%
- P&L > +2.0%: Book L4, trail stop aggressively
- Let L5 (10%) ride to target or EOD as the runner

WHEN FADING FROM PEAK:
- If peak was +1% and now +0.3% (fade 70%): book remaining lots, tighten hard
- If peak was +0.5% and now +0.4% (fade 20%): hold, normal pullback

Output JSON only:
{"action":"hold|book_l1|book_l2|book_l3|book_l4|tighten|close_all","new_stop":"price_or_unchanged","new_target":"price_or_unchanged","reason":"1 sentence"}"""

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
