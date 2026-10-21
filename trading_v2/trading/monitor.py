"""Monitor — one background thread, one exit path.

Checks all positions every N seconds:
  SL → close
  Target → close
  Trail → close
  EOD 3:10 PM → close all
  External signal → close (via force_exit)

Every close goes through positions.close() → sell order + FM release.
"""
import threading
import time
from datetime import datetime

from trading.types import Side
from live.indmoney_client import tick_round
from trading.logger import audit, get_logger, tg_alert

log = get_logger('monitor')


class Monitor:
    def __init__(self, broker, positions, fund_manager, poll_interval: int = 10):
        self._broker = broker
        self._pos = positions
        self._fm = fund_manager
        self._poll = poll_interval
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name='monitor').start()
        log.info(f'Monitor started (poll={self._poll}s)')

    def stop(self):
        self._running = False

    def _loop(self):
        import traceback
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.error(f'Tick error: {e}')
                traceback.print_exc()
            time.sleep(self._poll)
        log.error('Monitor loop exited! _running is False')

    def _tick(self):
        now = datetime.now()

        # EOD 3:10 PM — close everything, verify with broker
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            # Cancel ALL pending orders first — prevent stale orders filling next day
            try:
                _book = self._broker._api.get_order_book() or []
                for _o in _book:
                    _st = str(_o.get('status', '')).upper()
                    if _st in ('PENDING', 'OPEN', 'CREATED', 'INITIATED'):
                        _oid = _o.get('id', _o.get('order_id', ''))
                        if _oid:
                            try:
                                self._broker._api.cancel_order(str(_oid))
                                log.info('EOD: cancelled pending order ' + str(_oid))
                            except Exception:
                                pass
            except Exception as _e:
                log.warning('EOD: order cancel sweep failed: ' + str(_e))

            active = self._pos.active()
            if active:
                log.info(f'EOD: closing {len(active)} positions')
                for p in active:
                    self._exit(p.id, reason='EOD_EXIT')

            # Verify broker has zero positions — keep trying until clean
            import time as _t
            for verify_attempt in range(5):
                _t.sleep(3)
                try:
                    from live.indmoney_client import get_positions
                    broker_pos = get_positions() or []
                    open_pos = [p for p in broker_pos if abs(p.get('net_qty', 0)) > 0]
                    if not open_pos:
                        log.info('EOD: broker confirmed zero positions')
                        break
                    # Still have positions — force close with wider price
                    log.warning(f'EOD: broker still has {len(open_pos)} positions, attempt {verify_attempt+2}')
                    # Re-fetch to avoid acting on stale data
                    broker_pos2 = get_positions() or []
                    open_pos = [p for p in broker_pos2 if abs(p.get('net_qty', 0)) > 0]
                    if not open_pos:
                        log.info('EOD: broker now clear after re-check')
                        break
                    for bp in open_pos:
                        sym = bp.get('symbol', '')
                        qty = abs(bp.get('net_qty', 0))
                        is_short = bp.get('net_qty', 0) < 0
                        if qty <= 0 or not sym:
                            continue
                        ltp = self._broker.ltp_safe(sym)
                        if ltp <= 0:
                            continue
                        # Widen buffer on each retry: 0.5%, 1%, 2%, 3%, 5%
                        buffer = [0.005, 0.01, 0.02, 0.03, 0.05][min(verify_attempt, 4)]
                        try:
                            if is_short:
                                price = round(ltp * (1 + buffer), 2)
                                self._broker.buy(sym, qty, price=price,
                                                order_type='LIMIT', validity='IOC')
                            else:
                                price = round(ltp * (1 - buffer), 2)
                                self._broker.sell(sym, qty, price=price,
                                                 order_type='LIMIT', validity='IOC')
                            log.info(f'EOD retry: {sym} qty={qty} price={price} buffer={buffer*100:.1f}%')
                        except Exception as e:
                            log.error(f'EOD retry failed {sym}: {e}')
                except Exception as e:
                    log.error(f'EOD verify error: {e}')

            return

        # Batch LTP for all positions at once (faster than one-by-one)
        active = self._pos.active()
        if not active:
            return

        # ── GTT Reconciliation ──────────────────────────────────────────────
        # Detect positions closed at exchange by GTT (SL or target fired).
        # If position gone at exchange but still in memory: close internally,
        # cancel the surviving GTT, release capital, alert Telegram.
        try:
            from live.indmoney_client import get_positions as _get_pos
            _broker_pos = _get_pos() or []
            # Also fetch FNO positions -- options/futures use segment=fno
            try:
                import requests as _req
                from live.indmoney_client import headers as _hdrs
                _fno_r = _req.get('https://api.indstocks.com/portfolio/positions?segment=derivative&product=intraday',
                                  headers=_hdrs(), timeout=10)
                if _fno_r.status_code == 200:
                    _fno_data = _fno_r.json().get('data', {})
                    if isinstance(_fno_data, list):
                        _broker_pos.extend(_fno_data)
                    elif isinstance(_fno_data, dict):
                        _broker_pos.extend(_fno_data.get('net_positions', []))
            except Exception as _fe:
                log.warning("FNO positions fetch failed: " + str(_fe))
            _live_syms = {
                p['symbol'] for p in _broker_pos
                if abs(p.get('net_qty', 0)) > 0
            }
            # Also build set of live security_ids for FNO matching
            _live_sec_ids = {
                str(p.get('security_id', '')) for p in _broker_pos
                if abs(p.get('net_qty', 0)) > 0
            }
            for _rpos in list(active):
                # For FNO: match by security_id. For equity: match by symbol.
                _is_fno = bool(getattr(_rpos, 'fno_sec_id', ''))
                if _is_fno:
                    _alive = str(getattr(_rpos, 'fno_sec_id', '')) in _live_sec_ids
                else:
                    _alive = _rpos.symbol in _live_syms
                # Grace period: skip recon for first 60s after open (API may not reflect yet)
                import time as _tm
                _age = _tm.time() - getattr(_rpos, "_opened_at", 0)
                if not _alive and not _rpos.closed and _age > 60:
                    # Try actual GTT fill price from order book first
                    _fill_price = 0.0
                    try:
                        _book = self._broker._api.get_order_book() or []
                        for _gtt_id in [getattr(_rpos, 'gtt_sl_id', ''),
                                        getattr(_rpos, 'gtt_target_id', '')]:
                            if not _gtt_id:
                                continue
                            for _o in _book:
                                _oid = str(_o.get('id', '')) or str(_o.get('order_id', ''))
                                _status = str(_o.get('status', '')).lower()
                                if _oid == str(_gtt_id) and _status in ('complete', 'filled'):
                                    _tp = float(_o.get('traded_price', 0) or 0)
                                    if _tp > 0:
                                        _fill_price = _tp
                                        break
                            if _fill_price > 0:
                                break
                    except Exception as _fe:
                        log.warning(f'GTT fill price lookup failed: {_fe}')
                    _exit_price = _fill_price if _fill_price > 0 else self._broker.ltp_safe(_rpos.symbol, _rpos.entry_price)
                    audit('monitor', 'GTT_FIRED_DETECTED', _rpos.symbol,
                          pos_id=_rpos.id, fill_price=round(_exit_price, 2),
                          source='order_book' if _fill_price > 0 else 'ltp_fallback',
                          gtt_sl=getattr(_rpos, 'gtt_sl_id', ''),
                          gtt_tgt=getattr(_rpos, 'gtt_target_id', ''))
                    audit('monitor', 'GTT_FILL_PRICE', _rpos.symbol, price=round(_exit_price, 2))
                    self._exit(_rpos.id, _exit_price, 'GTT_FIRED')
                    active = [p for p in active if p.id != _rpos.id]
        except Exception as _re:
            log.warning(f'GTT reconcile error: {_re}')
        # ────────────────────────────────────────────────────────────────────

        active = [p for p in active if not p.closed]  # re-filter after reconcile
        if not active:
            return

        symbols = list(set(p.symbol for p in active))
        try:
            prices = self._broker._api.get_ltp(symbols)
        except Exception:
            prices = {}
        for pos in active:
            try:
                price = float(prices.get(pos.symbol, 0))
                # FnO positions: batch LTP won't have them, use sec_id lookup
                if price <= 0 and getattr(pos, 'fno_sec_id', ''):
                    price = self._broker.ltp_fno(pos.fno_sec_id)
                if price <= 0:
                    continue
                self._check(pos, price)
            except Exception as e:
                log.warning(f'Check error {pos.symbol}: {e}')

    def _check(self, pos, price: float):
        if pos.side == Side.BUY:
            move = (price - pos.entry_price) / pos.entry_price * 100
        else:
            move = (pos.entry_price - price) / pos.entry_price * 100

        self._pos.update_peak(pos.id, price)

        # SL
        # Dynamic SL tightening: once +0.25% profit, tighten SL from 2% to 1%
        # Only for anomaly strategy (pullback reversal trades)
        if pos.strategy == 'anomaly' and not getattr(pos, '_sl_tightened', False):
            if move >= 0.25:
                new_sl_pct = 1.0
                if pos.side == Side.BUY:
                    new_sl = pos.entry_price * (1 - new_sl_pct / 100)
                    if new_sl > pos.sl:  # only tighten, never widen
                        pos.sl = new_sl
                        pos._sl_tightened = True
                        audit('monitor', 'SL_TIGHTEN', pos.symbol, old_pct=2.0, new_pct=new_sl_pct, move=round(move, 2))
                else:
                    new_sl = pos.entry_price * (1 + new_sl_pct / 100)
                    if new_sl < pos.sl:  # only tighten, never widen
                        pos.sl = new_sl
                        pos._sl_tightened = True
                        audit('monitor', 'SL_TIGHTEN', pos.symbol, old_pct=2.0, new_pct=new_sl_pct, move=round(move, 2))

        if pos.sl > 0:
            if (pos.side == Side.BUY and price <= pos.sl) or \
               (pos.side == Side.SELL and price >= pos.sl):
                self._exit(pos.id, price, 'SL_HIT')
                return

        # Target
        if pos.target > 0:
            if (pos.side == Side.BUY and price >= pos.target) or \
               (pos.side == Side.SELL and price <= pos.target):
                self._exit(pos.id, price, 'TARGET_HIT')
                return

        # Trail (software fallback only — GTT SL is fixed at exchange, never moved)
        if pos.trail_activate_pct > 0 and pos.trail_pct > 0:
            if not pos.trail_active and move >= pos.trail_activate_pct:
                pos.trail_active = True
                audit('monitor', 'TRAIL_ON', pos.symbol, move=round(move, 2))

            if pos.trail_active:
                peak = pos.peak_price
                if pos.side == Side.BUY:
                    drop = (price - peak) / peak * 100
                else:
                    drop = (peak - price) / peak * 100
                if drop <= -pos.trail_pct and not getattr(pos, 'gtt_sl_id', ''):
                    # Software exit only when no exchange GTT present (fallback)
                    self._exit(pos.id, price, 'TRAIL_EXIT')
                    return

        # Trailing target: extend BUY LIMIT when profit within 1% of current target
        # FIXED: track last extend level to prevent order stacking
        if getattr(pos, 'gtt_target_id', '') and getattr(pos, 'gtt_target_pct', 0) > 0:
            _entry = pos.entry_price
            if _entry and _entry > 0:
                _profit_pct = (_entry - price) / _entry * 100
                _tgt_pct = pos.gtt_target_pct
                _last_ext = getattr(pos, '_last_extend_pct', 0)
                # Only extend if we crossed a NEW threshold (not same one again)
                if _profit_pct >= _tgt_pct - 1.0 and _tgt_pct != _last_ext:
                    _new_tgt_pct = _tgt_pct + 0.5
                    _new_tgt_price = tick_round(_entry * (1 - _new_tgt_pct / 100))
                    # Mark as extended FIRST to prevent re-trigger on next tick
                    pos._last_extend_pct = _tgt_pct
                    # Cancel old order
                    _old_id = pos.gtt_target_id
                    try:
                        self._broker._api.cancel_order(_old_id)
                    except Exception as _ce:
                        log.warning('Target cancel failed ' + pos.symbol + ': ' + str(_ce))
                    # Place new order
                    _new_oid = self._broker.buy(pos.symbol, pos.qty,
                                                price=_new_tgt_price, order_type='LIMIT')
                    if _new_oid:
                        audit('monitor', 'TARGET_LIMIT_EXTEND', pos.symbol,
                              old_tgt=_tgt_pct, new_tgt=_new_tgt_pct,
                              price=round(_new_tgt_price, 2))
                        pos.gtt_target_id = _new_oid
                        pos.gtt_target_pct = _new_tgt_pct
                    else:
                        # Buy failed -- keep old target, do NOT retry
                        log.error('Target extend BUY failed ' + pos.symbol)

    def _exit(self, pos_id: str, exit_price: float = 0, reason: str = 'MANUAL'):
        pos = self._pos.close(pos_id, exit_price=exit_price, reason=reason)
        if pos:
            audit('monitor', 'EXIT', pos.symbol, reason=reason,
                  entry=pos.entry_price, exit=round(pos.exit_price, 2),
                  pnl=round(pos.pnl, 2), pnl_pct=round(pos.pnl_pct, 2),
                  side=pos.side.value, qty=pos.qty,
                  peak=round(pos.peak_price, 2) if pos.peak_price else 0,
                  trail_active=pos.trail_active)
            tg_alert(f'{reason} {pos.symbol} pnl={pos.pnl:+.0f} ({pos.pnl_pct:+.1f}%) entry={pos.entry_price:.1f} exit={pos.exit_price:.1f}')
            if pos.trade_id:
                self._fm.release(pos.trade_id, pnl=pos.pnl)

    def force_exit(self, symbol: str, reason: str = 'SIGNAL_EXIT',
                   strategy: str = None) -> bool:
        """Close position for symbol. If strategy is given, only close that strategy's position."""
        with self._pos._lock:
            candidates = [p for p in self._pos._positions.values()
                          if p.symbol == symbol
                          and (strategy is None or p.strategy == strategy)]
        if not candidates:
            tg_alert(f'EXIT SIGNAL but NO POSITION found: {symbol} ({reason})')
            log.warning(f'force_exit: no position for {symbol} ({reason})')
            return False
        pos = candidates[0]
        price = self._broker.ltp_safe(pos.symbol, pos.entry_price)
        self._exit(pos.id, price, reason)
        return True
