"""
MoE with Tool Use — LLM calls tools to get data and execute actions.
Uses OpenAI function calling. LLM decides what to check and when to act.
"""
import json
import requests
from typing import Dict, List, Tuple, Any


# ═══ TOOL DEFINITIONS (OpenAI function calling format) ═══
MONITOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_calculation",
            "description": "Run a Python calculation on the trade data. You can compute: linear regression slope, momentum projection, ATR-based targets, Fibonacci levels, moving averages, RSI, or any custom math. Write a Python expression that returns a number or string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python expression to evaluate. Available variables: closes (list of close prices), highs, lows, volumes, entry (entry price), current (current price), atr (average true range). Example: 'sum(closes[-5:])/5' for 5-bar SMA."}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_momentum_projection",
            "description": "Project where price is heading based on recent momentum. Uses linear regression on last N bars to project price at bar+5 and bar+10. Shows momentum strength and projected target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n_bars": {"type": "integer", "description": "Number of recent bars to use for projection (3-15)"}
                },
                "required": ["n_bars"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_risk_reward",
            "description": "Calculate current risk:reward from current price to stop and target. Shows how much room to target vs how much risk to stop, and whether R:R justifies staying in.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_similar_past_trades",
            "description": "Check if similar setups (same stock, same direction, similar conditions) have been profitable in the past. Shows historical win rate and average P&L.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_candle_patterns",
            "description": "Detect candlestick patterns at current bar: doji, engulfing, hammer, shooting star, marubozu, etc. Shows if reversal or continuation patterns are forming.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_bars",
            "description": "Get the last N 5-minute candles for this stock. Shows OHLCV for each bar. Use this to see recent price action and patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n_bars": {"type": "integer", "description": "Number of recent bars to see (1-10)"}
                },
                "required": ["n_bars"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_flow",
            "description": "Check what the sector leader and other sector stocks are doing right now. Shows leader change %, breadth, which stocks confirm direction.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume_analysis",
            "description": "Get detailed volume analysis: RVOL, first half vs second half volume, entry bar spike detection, volume trend.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_key_levels",
            "description": "Get support/resistance levels near current price: Camarilla S3/R3/R4/S4, previous day high/low, VWAP, opening range.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_breadth",
            "description": "How many stocks are up vs down right now across the entire market. Shows trending vs choppy regime.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_action",
            "description": "Execute a trading action on the position. Call this AFTER you've analyzed the situation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["hold", "book_lot", "tighten_stop", "extend_target", "close_all"],
                        "description": "What to do"
                    },
                    "lot_to_book": {
                        "type": "string",
                        "description": "Which lot to book: L1, L2, L3, L4, L5 (only for book_lot)"
                    },
                    "new_stop": {
                        "type": "number",
                        "description": "New stop price (only for tighten_stop)"
                    },
                    "new_target": {
                        "type": "number",
                        "description": "New target price (only for extend_target)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you're taking this action"
                    }
                },
                "required": ["action", "reason"]
            }
        }
    },
]

SCANNER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "dig_deeper",
            "description": "Get detailed analysis for a specific signal/stock before deciding. Shows volume profile, sector context, key levels, recent bars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol to analyze"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "decide_trades",
            "description": "Submit your final trade decisions after analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trades": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
                                "strategy": {"type": "string"},
                                "confidence": {"type": "integer", "description": "0-100"},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["symbol", "direction", "confidence", "reasoning"]
                        },
                        "description": "List of trades to take (max 3). Empty = skip day."
                    }
                },
                "required": ["trades"]
            }
        }
    },
]


class TradeContext:
    """Holds all data for tool responses during simulation."""

    def __init__(self, symbol, direction, entry, all_bars, bar_idx, all_data, date,
                 prev_day_data, lots, booked_pnl, cur_stop, target, max_fav):
        self.symbol = symbol
        self.direction = direction
        self.entry = entry
        self.all_bars = all_bars  # Full day bars for this stock
        self.bar_idx = bar_idx   # Current bar index
        self.all_data = all_data  # All stocks data
        self.date = date
        self.prev_day = prev_day_data
        self.lots = lots
        self.booked_pnl = booked_pnl
        self.cur_stop = cur_stop
        self.target = target
        self.max_fav = max_fav

    def handle_tool_call(self, name: str, args: dict) -> str:
        """Execute a tool and return result string."""
        if name == "get_recent_bars":
            return self._recent_bars(args.get("n_bars", 5))
        elif name == "get_sector_flow":
            return self._sector_flow()
        elif name == "get_volume_analysis":
            return self._volume_analysis()
        elif name == "get_key_levels":
            return self._key_levels()
        elif name == "get_market_breadth":
            return self._market_breadth()
        elif name == "run_calculation":
            return self._run_calculation(args.get("code", ""))
        elif name == "get_momentum_projection":
            return self._momentum_projection(args.get("n_bars", 5))
        elif name == "get_risk_reward":
            return self._risk_reward()
        elif name == "get_similar_past_trades":
            return self._similar_past()
        elif name == "get_candle_patterns":
            return self._candle_patterns()
        elif name == "execute_action":
            return json.dumps(args)
        elif name == "dig_deeper":
            return self._dig_deeper(args.get("symbol", self.symbol))
        elif name == "decide_trades":
            return json.dumps(args)
        return "{}"

    def _recent_bars(self, n):
        start = max(0, self.bar_idx - n)
        bars = self.all_bars[start:self.bar_idx + 1]
        lines = []
        for i, b in enumerate(bars):
            ts = b.get('timestamp', '').split(' ')[1][:5] if ' ' in b.get('timestamp', '') else ''
            color = 'GREEN' if b['close'] > b['open'] else 'RED'
            body = abs(b['close']-b['open'])
            rng = b['high']-b['low']
            body_pct = body/rng*100 if rng > 0 else 0
            lines.append(f"  {ts} O={b['open']:.2f} H={b['high']:.2f} L={b['low']:.2f} C={b['close']:.2f} V={b['volume']} {color} body={body_pct:.0f}%")
        return "\n".join(lines)

    def _sector_flow(self):
        from app.signals.base import get_sector, SECTOR_MAP
        sector = get_sector(self.symbol)
        sec_info = SECTOR_MAP.get(sector, {})
        if not sec_info:
            return f"Sector: unknown for {self.symbol}"

        leader = sec_info.get("leader", "")
        laggards = sec_info.get("laggards", [])
        lines = [f"Sector: {sector}"]

        for s in [leader] + list(laggards):
            s_bars = [b for b in self.all_data.get(s, []) if b['timestamp'][:10] == self.date]
            if s_bars and len(s_bars) > self.bar_idx:
                move = (s_bars[self.bar_idx]['close'] - s_bars[0]['open']) / s_bars[0]['open'] * 100
                tag = "LEADER" if s == leader else "laggard"
                lines.append(f"  {s} ({tag}): {move:+.2f}%")

        return "\n".join(lines)

    def _volume_analysis(self):
        bars = self.all_bars[:self.bar_idx+1]
        if len(bars) < 3: return "Not enough bars"
        total_vol = sum(b['volume'] for b in bars)
        first_half = sum(b['volume'] for b in bars[:len(bars)//2])
        second_half = sum(b['volume'] for b in bars[len(bars)//2:])
        sustain = second_half/first_half*100 if first_half > 0 else 0
        entry_vol = bars[-1]['volume']
        avg_vol = total_vol / len(bars)
        spike = entry_vol / avg_vol if avg_vol > 0 else 1
        trend = "RISING" if len(bars)>=3 and bars[-1]['volume'] > bars[-2]['volume'] > bars[-3]['volume'] else \
                "FALLING" if len(bars)>=3 and bars[-1]['volume'] < bars[-2]['volume'] < bars[-3]['volume'] else "MIXED"
        return (f"Total vol: {total_vol:,} | Avg/bar: {avg_vol:,.0f}\n"
                f"1st half: {first_half:,} | 2nd half: {second_half:,} | Sustain: {sustain:.0f}%\n"
                f"Current bar vol: {entry_vol:,} ({spike:.1f}x avg)\n"
                f"Vol trend: {trend}")

    def _key_levels(self):
        price = self.all_bars[self.bar_idx]['close']
        lines = [f"Current price: {price:.2f}"]

        # VWAP
        tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in self.all_bars[:self.bar_idx+1])
        cum_vol = sum(b['volume'] for b in self.all_bars[:self.bar_idx+1])
        vwap = tp_vol/cum_vol if cum_vol > 0 else price
        lines.append(f"VWAP: {vwap:.2f} (price {'above' if price>vwap else 'below'} by {abs(price-vwap)/price*100:.2f}%)")

        # Camarilla
        if self.prev_day:
            h=self.prev_day['high']; l=self.prev_day['low']; c=self.prev_day['close']; rng=h-l
            if rng > 0:
                lines.append(f"Cam S4={c-rng*1.1/2:.2f} S3={c-rng*1.1/4:.2f} | Pivot={c:.2f} | R3={c+rng*1.1/4:.2f} R4={c+rng*1.1/2:.2f}")
            lines.append(f"Prev day: H={h:.2f} L={l:.2f} C={c:.2f}")

        # ORB
        lines.append(f"Opening range: H={self.all_bars[0]['high']:.2f} L={self.all_bars[0]['low']:.2f}")

        # Day high/low so far
        day_h = max(b['high'] for b in self.all_bars[:self.bar_idx+1])
        day_l = min(b['low'] for b in self.all_bars[:self.bar_idx+1])
        lines.append(f"Day H={day_h:.2f} L={day_l:.2f}")

        return "\n".join(lines)

    def _market_breadth(self):
        up = dn = tot = 0
        for sym, bars_all in self.all_data.items():
            s_bars = [b for b in bars_all if b['timestamp'][:10] == self.date]
            if not s_bars or len(s_bars) <= self.bar_idx: continue
            tot += 1
            mv = (s_bars[self.bar_idx]['close'] - s_bars[0]['open']) / s_bars[0]['open'] * 100
            if mv > 0.15: up += 1
            elif mv < -0.15: dn += 1
        flat = tot - up - dn
        regime = "TRENDING UP" if up/tot>0.65 else "TRENDING DOWN" if dn/tot>0.65 else "CHOPPY"
        return f"Up: {up}/{tot} ({up/tot*100:.0f}%) | Down: {dn}/{tot} ({dn/tot*100:.0f}%) | Flat: {flat} | Regime: {regime}"

    def _run_calculation(self, code):
        """Safely execute Python calculation with trade data available."""
        bars = self.all_bars[:self.bar_idx+1]
        closes = [b['close'] for b in bars]
        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        volumes = [b['volume'] for b in bars]
        entry = self.entry
        current = bars[-1]['close'] if bars else 0
        atr = sum(highs[i]-lows[i] for i in range(max(0,len(bars)-6),len(bars)))/min(6,len(bars)) if bars else 0

        allowed_names = {
            'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes,
            'entry': entry, 'current': current, 'atr': atr,
            'sum': sum, 'max': max, 'min': min, 'abs': abs, 'len': len,
            'round': round, 'sorted': sorted,
        }
        try:
            import math
            allowed_names['math'] = math
            result = eval(code, {"__builtins__": {}}, allowed_names)
            return str(result)
        except Exception as e:
            return f"Error: {e}"

    def _momentum_projection(self, n_bars):
        """Linear regression projection on last N bars."""
        bars = self.all_bars[max(0,self.bar_idx-n_bars+1):self.bar_idx+1]
        if len(bars) < 3: return "Not enough bars for projection"
        closes = [b['close'] for b in bars]
        n = len(closes)
        x_mean = (n-1)/2
        y_mean = sum(closes)/n
        sx2 = sum((i-x_mean)**2 for i in range(n))
        if sx2 == 0: return "Flat price, no projection"
        slope = sum((i-x_mean)*(closes[i]-y_mean) for i in range(n)) / sx2
        intercept = y_mean - slope * x_mean

        proj_5 = slope * (n + 4) + intercept
        proj_10 = slope * (n + 9) + intercept
        current = closes[-1]
        slope_pct = slope / current * 100

        strength = "STRONG" if abs(slope_pct) > 0.05 else "MODERATE" if abs(slope_pct) > 0.02 else "WEAK"
        direction = "UP" if slope > 0 else "DOWN"

        return (f"Momentum: {direction} {strength} (slope={slope_pct:+.3f}%/bar)\n"
                f"Current: {current:.2f}\n"
                f"Projected +25min: {proj_5:.2f} ({(proj_5-current)/current*100:+.2f}%)\n"
                f"Projected +50min: {proj_10:.2f} ({(proj_10-current)/current*100:+.2f}%)\n"
                f"R-squared fit quality: {self._r_squared(closes):.2f}")

    def _r_squared(self, closes):
        """R-squared for linear regression fit."""
        n = len(closes)
        if n < 3: return 0
        x_mean = (n-1)/2; y_mean = sum(closes)/n
        sx2 = sum((i-x_mean)**2 for i in range(n))
        if sx2 == 0: return 0
        slope = sum((i-x_mean)*(closes[i]-y_mean) for i in range(n)) / sx2
        intercept = y_mean - slope * x_mean
        ss_res = sum((closes[i] - (slope*i+intercept))**2 for i in range(n))
        ss_tot = sum((closes[i] - y_mean)**2 for i in range(n))
        return 1 - ss_res/ss_tot if ss_tot > 0 else 0

    def _risk_reward(self):
        """Calculate current risk:reward."""
        current = self.all_bars[self.bar_idx]['close']
        if self.direction == 'LONG':
            risk = current - self.cur_stop
            reward = self.target - current
        else:
            risk = self.cur_stop - current
            reward = current - self.target
        rr = reward / risk if risk > 0 else 0
        risk_pct = risk / current * 100
        reward_pct = reward / current * 100
        return (f"Current: {current:.2f} | Stop: {self.cur_stop:.2f} | Target: {self.target:.2f}\n"
                f"Risk: {risk:.2f} ({risk_pct:.2f}%) | Reward: {reward:.2f} ({reward_pct:.2f}%)\n"
                f"R:R = {rr:.1f}:1\n"
                f"{'FAVORABLE' if rr >= 2 else 'MARGINAL' if rr >= 1 else 'UNFAVORABLE'} risk:reward")

    def _similar_past(self):
        """Check historical performance of this stock."""
        # From our 4-year analysis
        known_stats = {
            'NTPC': '64% WR on ORB (best stock), profitable every year',
            'ULTRACEMCO': '48% WR on ORB, inconsistent across years',
            'HDFCBANK': '70% WR on Camarilla R3, most reliable',
            'HCLTECH': '68% WR on Camarilla R3',
            'TITAN': '68% WR on Camarilla R3',
            'SBIN': '67% WR on Camarilla R3',
            'ADANIENT': '43% WR on ORB — unreliable, high ATR',
            'APOLLOHOSP': '36% WR on ORB — worst stock, avoid',
            'ASIANPAINT': '49% WR on ORB, inconsistent',
            'TATASTEEL': '42% WR on ORB',
            'BPCL': '50% WR on ORB',
            'COALINDIA': '49% WR on ORB',
            'HEROMOTOCO': '40% WR on ORB — avoid',
        }
        stat = known_stats.get(self.symbol, f'No specific history for {self.symbol}')
        return f"Historical: {stat}\n(Based on 4-year backtest, 1087 trading days)"

    def _candle_patterns(self):
        """Detect patterns at current bar."""
        if self.bar_idx < 2: return "Not enough bars"
        b = self.all_bars[self.bar_idx]
        prev = self.all_bars[self.bar_idx-1]

        body = abs(b['close']-b['open'])
        rng = b['high']-b['low']
        if rng == 0: return "Zero range bar (no movement)"

        body_pct = body/rng*100
        uw = b['high'] - max(b['open'],b['close'])
        lw = min(b['open'],b['close']) - b['low']
        is_green = b['close'] > b['open']

        patterns = []

        # Marubozu
        if body_pct > 80:
            patterns.append(f"MARUBOZU ({'bullish' if is_green else 'bearish'}) — {body_pct:.0f}% body, strong conviction")

        # Doji
        if body_pct < 10:
            patterns.append(f"DOJI — {body_pct:.0f}% body, indecision, potential reversal")

        # Hammer
        if lw > body*2 and uw < body*0.5:
            patterns.append(f"HAMMER — long lower wick, buying pressure from below")

        # Shooting star
        if uw > body*2 and lw < body*0.5:
            patterns.append(f"SHOOTING STAR — long upper wick, selling pressure from above")

        # Engulfing
        prev_body = abs(prev['close']-prev['open'])
        if body > prev_body * 1.3:
            if is_green and prev['close'] < prev['open']:
                patterns.append("BULLISH ENGULFING — current green candle engulfs previous red")
            elif not is_green and prev['close'] > prev['open']:
                patterns.append("BEARISH ENGULFING — current red candle engulfs previous green")

        # Pin bar
        total_wick = uw + lw
        if total_wick > body * 3:
            patterns.append(f"PIN BAR — wicks {total_wick/body:.1f}x body, rejection candle")

        # Inside bar
        if b['high'] <= prev['high'] and b['low'] >= prev['low']:
            patterns.append("INSIDE BAR — range within previous bar, compression before breakout")

        if not patterns:
            patterns.append(f"No significant pattern. Body={body_pct:.0f}%, {'green' if is_green else 'red'}")

        return "\n".join(patterns)

    def _dig_deeper(self, symbol):
        parts = []
        # Recent 5 bars
        s_bars = [b for b in self.all_data.get(symbol, []) if b['timestamp'][:10] == self.date]
        if s_bars and len(s_bars) > 6:
            parts.append(f"Recent bars for {symbol}:")
            for b in s_bars[max(0,min(6,len(s_bars))-5):min(6,len(s_bars))+1]:
                ts = b.get('timestamp','').split(' ')[1][:5] if ' ' in b.get('timestamp','') else ''
                c = 'G' if b['close']>b['open'] else 'R'
                parts.append(f"  {ts} O={b['open']:.1f} H={b['high']:.1f} L={b['low']:.1f} C={b['close']:.1f} V={b['volume']} {c}")

        # Sector
        from app.signals.base import get_sector
        parts.append(f"Sector: {get_sector(symbol)}")

        # Volume
        if s_bars and len(s_bars) > 6:
            avg_v = sum(b['volume'] for b in s_bars[:7])/7
            parts.append(f"Avg vol (7 bars): {avg_v:,.0f}")

        return "\n".join(parts)


def moe_monitor_with_tools(
    position_summary: str,
    trade_ctx: TradeContext,
    api_key: str,
    model: str = "gpt-4o-mini",
    max_tool_calls: int = 3,
) -> Dict:
    """
    Monitor with tool use. LLM can call tools to check data before deciding.
    Returns the execute_action result.
    """
    system_prompt = """You are managing an open intraday trade. You have tools to check live data.

WORKFLOW:
1. Look at the position summary
2. If you need more info, call a tool (get_recent_bars, get_sector_flow, etc.)
3. When ready, call execute_action with your decision

GOLDEN RULE: Never give back all profits. If price peaked at +0.5%, protect at least +0.2%.
You manage 5 lots (L1-L5). Book lots progressively as price moves in your favor.
Trail stop behind price. Extend target if momentum is strong.

You have up to 3 tool calls before you must execute_action."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": position_summary},
    ]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for _ in range(max_tool_calls + 1):
        payload = {
            "model": model,
            "messages": messages,
            "tools": MONITOR_TOOLS,
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": 500,
        }

        try:
            resp = requests.post("https://api.openai.com/v1/chat/completions",
                               headers=headers, json=payload, timeout=15)
            if resp.status_code != 200:
                return {"action": "hold", "reason": f"API error {resp.status_code}"}

            data = resp.json()
            msg = data["choices"][0]["message"]

            # Check for tool calls
            if msg.get("tool_calls"):
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    fn_args = json.loads(tc["function"]["arguments"]) if tc["function"].get("arguments") else {}

                    if fn_name == "execute_action":
                        # Final action — return it
                        return fn_args

                    # Call the tool
                    result = trade_ctx.handle_tool_call(fn_name, fn_args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
            else:
                # No tool call — try to parse as action
                content = msg.get("content", "")
                try:
                    start = content.find('{'); end = content.rfind('}') + 1
                    if start >= 0: return json.loads(content[start:end])
                except: pass
                return {"action": "hold", "reason": "no_tool_call"}

        except Exception as e:
            return {"action": "hold", "reason": f"error: {e}"}

    return {"action": "hold", "reason": "max_tool_calls_exceeded"}


def moe_scan_with_tools(
    context: str,
    all_data: dict,
    date: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Tuple[List[Dict], int]:
    """
    Scanner with tool use. LLM can dig deeper into specific stocks.
    Returns (trades, api_calls).
    """
    system_prompt = """You are a trading decision system with 15 domain expert perspectives.
You see signals with basic data. If you want more detail on a specific stock, call dig_deeper(symbol).
When ready, call decide_trades with your final picks (max 3 trades).

Analyze from: Volume, Sector, Price, Momentum, Market, Macro, Risk, Timing, History, Contrarian, RS, Gap, Volatility, Levels, Candlestick perspectives.

Pick trades where multiple perspectives converge. Skip if no strong conviction."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    api_calls = 0

    # Dummy context for dig_deeper
    dummy_ctx = TradeContext("", "", 0, [], 0, all_data, date, None, {}, 0, 0, 0, 0)

    for _ in range(5):
        payload = {
            "model": model,
            "messages": messages,
            "tools": SCANNER_TOOLS,
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": 800,
        }

        try:
            resp = requests.post("https://api.openai.com/v1/chat/completions",
                               headers=headers, json=payload, timeout=30)
            api_calls += 1
            if resp.status_code != 200:
                return [], api_calls

            data = resp.json()
            msg = data["choices"][0]["message"]

            if msg.get("tool_calls"):
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    fn_args = json.loads(tc["function"]["arguments"]) if tc["function"].get("arguments") else {}

                    if fn_name == "decide_trades":
                        return fn_args.get("trades", []), api_calls

                    result = dummy_ctx.handle_tool_call(fn_name, fn_args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
            else:
                # Try to parse trades from content
                content = msg.get("content", "")
                try:
                    start = content.find('{'); end = content.rfind('}') + 1
                    if start >= 0:
                        parsed = json.loads(content[start:end])
                        return parsed.get("trades", []), api_calls
                except: pass
                return [], api_calls

        except Exception as e:
            return [], api_calls

    return [], api_calls
