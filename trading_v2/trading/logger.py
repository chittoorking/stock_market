"""Logging + audit trail.

Two outputs:
  1. Console + file log (human readable)
  2. JSON audit trail (machine readable, append-only)
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_ml_enricher = None

# Use project-level logs dir (news-trading/live/logs) if exists, else local
_project_logs = Path(__file__).parent.parent.parent / 'live' / 'logs'
LOG_DIR = _project_logs if _project_logs.exists() else Path(__file__).parent.parent / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

_fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s', '%H:%M:%S')

_console_h = logging.StreamHandler(sys.stdout)
_console_h.setFormatter(_fmt)

_root = logging.getLogger('trading')
_root.setLevel(logging.INFO)
if not _root.handlers:
    _root.addHandler(_console_h)

# Track current log file date to rotate at midnight
_current_log_date = None
_current_file_handler = None


def _ensure_file_handler():
    """Rotate log file if date changed."""
    global _current_log_date, _current_file_handler
    today = datetime.now().strftime('%Y%m%d')
    if today == _current_log_date:
        return
    # Remove old handler
    if _current_file_handler:
        _root.removeHandler(_current_file_handler)
        _current_file_handler.close()
    # Add new handler for today
    _current_file_handler = logging.FileHandler(LOG_DIR / f'trading_{today}.log')
    _current_file_handler.setFormatter(_fmt)
    _root.addHandler(_current_file_handler)
    _current_log_date = today


# Initialize on import
_ensure_file_handler()


def get_logger(name: str) -> logging.Logger:
    return _root.getChild(name)


def audit(component: str, action: str, symbol: str = '', **kw):
    """Append one audit record. Uses current date for file rotation."""
    _ensure_file_handler()
    today = datetime.now().strftime('%Y%m%d')
    audit_path = LOG_DIR / f'audit_{today}.jsonl'

    record = {'ts': datetime.now().isoformat(), 'component': component,
              'action': action, 'symbol': symbol, **kw}
    with open(audit_path, 'a') as f:
        f.write(json.dumps(record, default=str) + '\n')
    detail = ' '.join(f'{k}={v}' for k, v in kw.items())
    _root.getChild(component).info(f'{action} | {symbol} | {detail}')

    # Log ML 94 fields for signal events
    if symbol and any(tag in action for tag in ['ENTRY', 'SIGNAL', 'TAKE', 'SCORE', 'TRADE']):
        try:
            global _ml_enricher
            if _ml_enricher is None:
                from trading.services.ml_enricher import log_signal_with_ml
                _ml_enricher = log_signal_with_ml
            _ml_enricher(component, action, symbol, **kw)
        except Exception:
            pass
