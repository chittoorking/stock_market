"""Win probability predictor — calibrated from 5-min backtest results.
Each signal contributes a known WR boost/penalty, combined into P(win).

Calibration source (10 days, 500+ candidates, 5-min bar-by-bar sim):
  Base WR (all candidates):     63%
  gap_atr 0.3-0.5:              68% (+5%)
  gap_atr > 0.8:                60% (-3%)
  rel_gap < 0.8:                74% (+11%)
  rel_gap < 1.0:                72% (+9%)
  momentum aligned:             +4% (from combo test)
  Rolling WR > 70%:             strong positive signal
  Rolling WR < 30%:             80% contrarian (small sample)
"""
import logging

log = logging.getLogger('predictor')

BASE_PROB = 0.63  # overall WR across all candidates


class WinPredictor:
    """Calibrated win probability from backtested signal contributions."""

    def predict(self, wr, gap_abs, gap_atr, rel_gap, is_momentum):
        """Predict P(win) for a gap candidate.

        Each signal adjusts the base probability up or down based on
        measured effect from 5-min backtest on NIFTY 500.
        """
        prob = BASE_PROB

        # Rolling WR effect (strongest signal when data exists)
        if wr >= 0.8:
            prob += 0.12
        elif wr >= 0.7:
            prob += 0.08
        elif wr >= 0.6:
            prob += 0.04
        elif wr <= 0.3:
            prob += 0.10  # contrarian reversion
        elif wr <= 0.4:
            prob -= 0.03

        # Gap/ATR ratio (signal-to-noise)
        if gap_atr <= 0.3:
            prob += 0.03
        elif gap_atr <= 0.5:
            prob += 0.05
        elif gap_atr <= 0.8:
            prob += 0.00
        elif gap_atr <= 1.2:
            prob -= 0.03
        else:
            prob -= 0.08

        # Relative gap (normal vs abnormal for this stock)
        if rel_gap < 0.8:
            prob += 0.11
        elif rel_gap < 1.0:
            prob += 0.06
        elif rel_gap > 2.0:
            prob -= 0.05

        # Momentum alignment
        if is_momentum:
            prob += 0.04

        # Gap size sweet spot
        if 1.0 <= gap_abs <= 2.0:
            prob += 0.02
        elif gap_abs > 4.0:
            prob -= 0.05

        return max(0.25, min(0.95, prob))

    def predict_with_details(self, wr, gap_abs, gap_atr, rel_gap, is_momentum):
        """Returns P(win) + human-readable breakdown."""
        prob = self.predict(wr, gap_abs, gap_atr, rel_gap, is_momentum)

        signals = []
        if wr >= 0.7:
            signals.append(f'Strong WR ({wr:.0%})')
        elif wr <= 0.4:
            signals.append(f'Weak WR ({wr:.0%})')

        if gap_atr <= 0.5:
            signals.append('Good gap/ATR')
        elif gap_atr > 1.0:
            signals.append('Overextended')

        if rel_gap < 0.8:
            signals.append('Normal gap for stock')
        elif rel_gap > 2.0:
            signals.append('Abnormal gap')

        if is_momentum:
            signals.append('Momentum')

        confidence = 'HIGH' if prob >= 0.75 else 'MED' if prob >= 0.60 else 'LOW'

        return {
            'probability': round(prob, 3),
            'confidence': confidence,
            'signals': signals,
            'label': f'{prob:.0%} ({confidence})',
        }


_predictor = None

def get_predictor():
    global _predictor
    if _predictor is None:
        _predictor = WinPredictor()
    return _predictor
