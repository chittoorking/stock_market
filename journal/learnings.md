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
6. [More to come from next trades]
