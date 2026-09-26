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

# Use project-level logs dir (news-trading/live/logs) if exists, else local
_project_logs = Path(__file__).parent.parent.parent / 'live' / 'logs'
LOG_DIR = _project_logs if _project_logs.exists() else Path(__file__).parent.parent / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

_date = datetime.now().strftime('%Y%m%d')

_fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s', '%H:%M:%S')

_file_h = logging.FileHandler(LOG_DIR / f'trading_{_date}.log')
_file_h.setFormatter(_fmt)
_console_h = logging.StreamHandler(sys.stdout)
_console_h.setFormatter(_fmt)

_root = logging.getLogger('trading')
_root.setLevel(logging.INFO)
if not _root.handlers:
    _root.addHandler(_file_h)
    _root.addHandler(_console_h)


def get_logger(name: str) -> logging.Logger:
    return _root.getChild(name)


_audit_path = LOG_DIR / f'audit_{_date}.jsonl'


def audit(component: str, action: str, symbol: str = '', **kw):
    """Append one audit record."""
    record = {'ts': datetime.now().isoformat(), 'component': component,
              'action': action, 'symbol': symbol, **kw}
    with open(_audit_path, 'a') as f:
        f.write(json.dumps(record, default=str) + '\n')
    detail = ' '.join(f'{k}={v}' for k, v in kw.items())
    _root.getChild(component).info(f'{action} | {symbol} | {detail}')
