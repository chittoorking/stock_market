# Agent Learnings — Reinforcement Log

## Trade 1: 2026-04-09 SHORT CIPLA (CAM_R3)
- Result: W +0.181% | MFE=0.692% | Captured=26%
- **Problem**: L1 booking at +0.5% never triggered for 50 bars. Slow grinder.
- **Fix**: For CAM_R3 signals, book L1 at +0.3% (not +0.5%). CAM_R3 avg move is 0.4-0.7%.
- **Fix**: After 2 PM, if in profit, tighten stop aggressively. Move is ending.
- **Miss**: BRITANNIA -3.15% was best trade. Couldn't have known at entry.

## Trade 2: 2026-04-10 SHORT HDFCLIFE (CAM_R3) — LOSS
- Result: L -1.289% | MFE=0.556% | MAE=1.709%
- **Root cause**: Bar0 was GREEN with 95% body (massive bullish marubozu).
  I shorted against the strongest possible opening conviction.
  The R3 level was being BROKEN through, not bounced off.
- Bar 1-2: went +0.56% in my favor (fake R3 bounce)
- Bar 3: reversed violently (green 85% body). Buyers returned.
- Bar 4-59: never came back. 57 bars of pain.
- **Fix**: NEVER take CAM_R3 SHORT when bar0 is GREEN with body > 70%.
  This is a breakout THROUGH resistance, not a bounce OFF it.
- **Fix**: If price reverses 3 bars with strong body (>70%) against me, EXIT.
  Don't wait for stop. 3 strong bars against = thesis is dead.

## Rules Updated:
1. CAM_R3 booking thresholds: L1 at +0.3%, L2 at +0.6%, L3 at +1.0%
2. After 2 PM: if P&L > 0, trail stop to 50% of current profit
3. **KILL RULE**: No CAM_R3 SHORT when bar0 is GREEN body > 70% (breakout, not bounce)
4. **KILL RULE**: No CAM_R3 LONG when bar0 is RED body > 70% (breakdown, not bounce)
5. **EXIT RULE**: 3 consecutive strong bars (body>60%) against your direction = EXIT immediately
6. **BOUNCE RULE**: If stock dropped >2% in prior 2 days AND yesterday was GREEN, it's bouncing. Don't SHORT it.
7. **VOLUME RULE**: Don't trade stocks with avg bar volume < 20,000. Dead liquidity = dead moves.
8. **SELECTION RULE**: When multiple CAM_R3 signals fire, pick the one with HIGHEST volume, not highest score.

## Trade 3: 2026-04-13 LONG NTPC (ORB) — WIN +0.634%
- MFE=0.84%, captured 75%. Good trade.
- ORB on choppy day with weak bar0 (20% body). Worked because NTPC has consistent volume.
- Confirms: ORB works well, especially NTPC.

## Trade 4: 2026-04-15 SHORT BHARTIARTL (CAM_R3) — WIN +0.929%
- MFE=1.20%, captured 77%. Excellent execution.
- Bar0 was GREEN 40% body — moderate, not conflicting.
- Confirms: CAM_R3 SHORT works when bar0 isn't strongly green.

## Trade 5: 2026-04-16 SHORT HCLTECH (CAM_R3) — LOSS -0.855%
- MFE only 0.28%. Stock barely dropped then reversed UP.
- Root cause: HCLTECH dropped 2.5% over 2 prior days, then bounced +1.53% yesterday.
  I shorted INTO the bounce. The selling was exhausted.
- Fix: Rule 6 (bounce detection)

## Trade 6: 2026-04-17 SHORT M&M (CAM_R3) — WIN +1.063%
- MFE=1.68%, captured 63%. Strong.
- Bar0 GREEN 55% — moderate. Choppy market. Good setup.

## Trade 7: 2026-04-22 SHORT DIVISLAB (CAM_R3) — LOSS -0.128%
- MFE only 0.19%. DEAD PICK — stock didn't move.
- Avg bar volume only 5,176. Extremely illiquid.
- 14 other CAM_R3 signals available! Should have picked NESTLEIND (+1.60%) or HEROMOTOCO (+1.51%).
- Fix: Rule 7 + 8 (volume filter, pick most liquid)

## Trade 8: 2026-04-23 SHORT AXISBANK (CAM_R3) — WIN +0.311%
- MFE=0.93%, captured only 33%. Left money on table.
- Same issue as Trade 1: CAM_R3 booking thresholds too high.
- Confirms Rule 1 fix: lower booking to L1@+0.3%

## Trades 9-20: THE BREAKEVEN PROBLEM
- 6 out of 12 CAM_R3 trades ended at BREAKEVEN (PnL=0)
- Pattern: price touches R3, drops 0.3-0.5%, stop moves to BE, price recovers, stopped at BE
- Each breakeven costs Rs 84 in charges for ZERO gain
- 6 x Rs 84 = Rs 504 wasted
- Root cause: breakeven stop at +0.3% is too tight for CAM_R3
  The avg MFE is 0.4-0.6% — just barely triggers BE, then reverts
- **Fix**: For CAM_R3, DON'T move stop to breakeven. Instead:
  Book L1 (30%) at +0.3% to lock in some profit.
  Keep original stop for remaining lots. Let the trade breathe.
  The L1 booking protects 30% of capital, other 70% has room.
9. **NO BREAKEVEN RULE FOR CAM_R3**: Don't trail stop to entry. Book lots instead.

## Running Score:
- 20 trades analyzed: 8W 6BE 4L 2Dead = 40% real WR
- If rules applied (skip conflicts, bounce, low vol, no premature BE):
  Skip: Trade 2 (bar0 conflict), Trade 5 (bounce), Trade 7 (low vol), Trade 10 (bar0 conflict), Trade 12 (low vol), Trade 17 (low vol)
  Keep: 14 trades, ~57% WR, much less charges wasted
