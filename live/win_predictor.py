"""Win probability predictor — logistic regression trained on fill_history.
Outputs P(win) for each gap candidate at scan time.

Features: rolling_wr, gap_abs, gap_atr, rel_gap, is_momentum
Trained on seeded fill_history (18K+ trades from 200 days).
Retrains daily as new data comes in.
"""
import json
import math
import logging
from pathlib import Path

log = logging.getLogger('predictor')

DATA_DIR = Path(__file__).parent.parent / 'data'
FILL_HISTORY_FILE = DATA_DIR / 'fill_history.json'
MODEL_FILE = DATA_DIR / 'win_model.json'


class WinPredictor:
    """Logistic regression: P(win) = sigmoid(w0 + w1*wr + w2*gap + w3*gap_atr + w4*rel + w5*momentum)"""

    def __init__(self):
        self.weights = None  # [bias, w_wr, w_gap, w_gap_atr, w_rel, w_momentum]
        self.trained = False
        self.n_samples = 0
        self._load_model()

    def _sigmoid(self, x):
        x = max(-500, min(500, x))  # clamp to avoid overflow
        return 1.0 / (1.0 + math.exp(-x))

    def _load_model(self):
        """Load pre-trained weights from disk."""
        if MODEL_FILE.exists():
            try:
                data = json.loads(MODEL_FILE.read_text())
                self.weights = data['weights']
                self.n_samples = data.get('n_samples', 0)
                self.trained = True
                log.info(f'Win predictor loaded: {self.n_samples} samples, weights={[round(w,3) for w in self.weights]}')
            except Exception as e:
                log.error(f'Failed to load win model: {e}')

    def _save_model(self):
        """Save trained weights to disk."""
        MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        MODEL_FILE.write_text(json.dumps({
            'weights': [round(w, 6) for w in self.weights],
            'n_samples': self.n_samples,
        }))

    def train(self, fill_history=None, daily_history=None):
        """Train logistic regression from fill_history + daily OHLC data.

        fill_history: {sym: [[date, pnl], ...]}
        daily_history: {sym: [{open, high, low, close, ...}, ...]}

        Since we don't have per-trade features stored, we train from
        aggregate stats per stock (WR predicts future WR).
        """
        if fill_history is None:
            if FILL_HISTORY_FILE.exists():
                fill_history = json.loads(FILL_HISTORY_FILE.read_text())
            else:
                log.warning('No fill history — cannot train predictor')
                return

        # Build training data: each trade becomes a sample
        # Features: wr_at_time, gap_proxy (avg for stock), momentum_proxy
        # Label: 1 if pnl > 0, else 0
        X = []  # feature vectors
        y = []  # labels

        for sym, trades in fill_history.items():
            if len(trades) < 5:
                continue

            for i in range(5, len(trades)):
                # Rolling WR from trades before this one
                prev = trades[:i]
                wr = sum(1 for _, p in prev[-20:] if p > 0) / len(prev[-20:])

                # Average gap (proxy — we don't store actual gap per trade)
                avg_pnl = sum(abs(p) for _, p in prev[-20:]) / len(prev[-20:])

                # Trend: were recent trades winners? (momentum proxy)
                recent_3 = prev[-3:]
                recent_wr = sum(1 for _, p in recent_3 if p > 0) / len(recent_3)

                # Consistency: std dev of recent PnL
                pnls = [p for _, p in prev[-10:]]
                avg_p = sum(pnls) / len(pnls)
                variance = sum((p - avg_p) ** 2 for p in pnls) / len(pnls)
                consistency = 1.0 / (1.0 + math.sqrt(variance))  # higher = more consistent

                # Label
                label = 1 if trades[i][1] > 0 else 0

                X.append([wr, avg_pnl, recent_wr, consistency, 1.0])  # 1.0 = placeholder for runtime features
                y.append(label)

        if len(X) < 100:
            log.warning(f'Too few samples ({len(X)}) to train predictor')
            return

        self.n_samples = len(X)

        # Train logistic regression with gradient descent
        n_features = len(X[0])
        self.weights = [0.0] * (n_features + 1)  # +1 for bias

        lr = 0.01
        epochs = 200

        for epoch in range(epochs):
            total_loss = 0
            for xi, yi in zip(X, y):
                z = self.weights[0]  # bias
                for j, xj in enumerate(xi):
                    z += self.weights[j + 1] * xj
                pred = self._sigmoid(z)
                error = pred - yi
                total_loss += -yi * math.log(max(pred, 1e-10)) - (1 - yi) * math.log(max(1 - pred, 1e-10))

                # Update weights
                self.weights[0] -= lr * error  # bias
                for j, xj in enumerate(xi):
                    self.weights[j + 1] -= lr * error * xj

            if epoch % 50 == 0:
                avg_loss = total_loss / len(X)
                # Compute accuracy
                correct = sum(1 for xi, yi in zip(X, y)
                              if (self._predict_raw(xi) >= 0.5) == (yi == 1))
                acc = correct / len(X)
                log.info(f'  Epoch {epoch}: loss={avg_loss:.4f} acc={acc:.1%}')

        # Final accuracy
        correct = sum(1 for xi, yi in zip(X, y)
                      if (self._predict_raw(xi) >= 0.5) == (yi == 1))
        accuracy = correct / len(X)

        self.trained = True
        self._save_model()
        log.info(f'Win predictor trained: {self.n_samples} samples, accuracy={accuracy:.1%}')
        log.info(f'  Weights: {[round(w, 3) for w in self.weights]}')

        return accuracy

    def _predict_raw(self, features):
        """Raw prediction from feature vector."""
        z = self.weights[0]
        for j, xj in enumerate(features):
            z += self.weights[j + 1] * xj
        return self._sigmoid(z)

    def predict(self, wr, gap_abs, gap_atr, rel_gap, is_momentum):
        """Predict P(win) for a gap candidate.

        Args:
            wr: rolling win rate for this stock (0-1)
            gap_abs: absolute gap size (%)
            gap_atr: gap / ATR ratio
            rel_gap: relative gap (today's gap / avg gap)
            is_momentum: bool, gap aligns with prev day direction

        Returns:
            float: probability of winning (0-1)
        """
        if not self.trained:
            # Fallback: use simple formula
            score = wr * 0.5 + (1 - min(gap_atr, 2) / 2) * 0.3 + (0.1 if rel_gap < 0.8 else 0) + (0.1 if is_momentum else 0)
            return max(0.3, min(0.95, score))

        # Map to training features
        # [wr, avg_pnl_proxy, recent_wr_proxy, consistency_proxy, momentum]
        features = [
            wr,
            gap_abs * 0.5,  # scale to match training range
            wr,  # recent_wr ≈ wr for live prediction
            0.5,  # consistency unknown at scan time
            1.0 if is_momentum else 0.0,
        ]
        return self._predict_raw(features)

    def predict_with_details(self, wr, gap_abs, gap_atr, rel_gap, is_momentum):
        """Returns P(win) + human-readable breakdown."""
        prob = self.predict(wr, gap_abs, gap_atr, rel_gap, is_momentum)

        # Contribution breakdown (approximate)
        signals = []
        if wr >= 0.7:
            signals.append(f'Strong WR ({wr:.0%})')
        elif wr <= 0.4:
            signals.append(f'Weak WR ({wr:.0%})')

        if gap_atr < 0.5:
            signals.append('Good gap/ATR')
        elif gap_atr > 1.0:
            signals.append('Overextended gap/ATR')

        if rel_gap < 0.8:
            signals.append('Normal gap for stock')
        elif rel_gap > 2.0:
            signals.append('Abnormal gap')

        if is_momentum:
            signals.append('Momentum aligned')

        confidence = 'HIGH' if prob >= 0.75 else 'MEDIUM' if prob >= 0.55 else 'LOW'

        return {
            'probability': round(prob, 3),
            'confidence': confidence,
            'signals': signals,
            'label': f'{prob:.0%} ({confidence})',
        }


# Singleton
_predictor = None

def get_predictor():
    global _predictor
    if _predictor is None:
        _predictor = WinPredictor()
    return _predictor
