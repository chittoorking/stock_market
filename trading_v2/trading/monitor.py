"""Monitor — websocket-first, polling fallback.

Primary: Websocket price feed (SL/trail/target) + order feed (GTT detection)
Fallback: Polling every 30s only when websocket disconnected
EOD: NSE 3:10 PM, MCX 11:25 PM — cancel GTTs, close positions
External: force_exit() for signal-driven closes
"""
import json
import time
import threading
import websocket
from datetime import datetime
from pathlib import Path

from trading.types import Side
from trading.logger import audit, get_logger, tg_alert
from live.indmoney_client import tick_round

log = get_logger('monitor')

PRICE_WS_URL = 'wss://ws-prices.indstocks.com/api/v1/ws/prices'
ORDER_WS_URL = 'wss://ws-order-updates.indstocks.com/api/v1/ws/trades'
_MCX_SYMS = ('SILVERM', 'GOLDM', 'CRUDEOILM', 'NATURALGAS', 'NATGASMINI')


class Monitor:
    def __init__(self, broker, positions, fund_manager, poll_interval: int = 30):
        self._broker = broker
        self._pos = positions
        self._fm = fund_manager
        self._poll_interval = poll_interval
        self._running = False

        # Websocket state
        self._price_ws = None
        self._order_ws = None
        self._price_ws_connected = False
        self._order_ws_connected = False
        self._token = ''
        self._subscribed_instruments = set()

    def start(self):
        self._token = Path('/home/ai18developer/news-trading/data/indmoney_token.txt').read_text().strip()
        self._running = True

        # Register callback so new positions auto-subscribe to websocket
        self._pos._on_open_callback = self.on_position_opened

        threading.Thread(target=self._run_price_ws, daemon=True, name='price_ws').start()
        threading.Thread(target=self._run_order_ws, daemon=True, name='order_ws').start()
        threading.Thread(target=self._poll_fallback, daemon=True, name='poll_fallback').start()
        threading.Thread(target=self._eod_loop, daemon=True, name='eod_check').start()

        log.info('Monitor started (ws-first, poll fallback=%ds)', self._poll_interval)

    def stop(self):
        self._running = False
        if self._price_ws:
            self._price_ws.close()
        if self._order_ws:
            self._order_ws.close()

    # ── Price Websocket ─────────────────────────────────────────────

    def _run_price_ws(self):
        while self._running:
            try:
                self._price_ws = websocket.WebSocketApp(
                    PRICE_WS_URL,
                    header={'Authorization': self._token},
                    on_open=self._on_price_open,
                    on_message=self._on_price_msg,
                    on_error=self._on_price_error,
                    on_close=self._on_price_close,
                )
                self._price_ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.warning('Price WS crashed: %s', e)
            self._price_ws_connected = False
            if self._running:
                time.sleep(5)

    def _on_price_open(self, ws):
        self._price_ws_connected = True
        log.info('Price WS connected')
        self._resubscribe()

    def _on_price_msg(self, ws, message):
        try:
            d = json.loads(message)
            if isinstance(d, str):
                d = json.loads(d)
            raw_inst = str(d.get('instrument', ''))
            ltp = d.get('data', {}).get('ltp', 0)
            if ltp > 0 and raw_inst:
                # Strip prefix: 'NSE:764940' → '764940', 'MCX:483080' → '483080'
                sec_id = raw_inst.split(':')[-1] if ':' in raw_inst else raw_inst
                self._on_price_tick(sec_id, ltp)
        except Exception as e:
            log.warning('Price msg error: %s', e)

    def _on_price_error(self, ws, error):
        log.warning('Price WS error: %s', error)

    def _on_price_close(self, ws, code, msg):
        self._price_ws_connected = False
        log.info('Price WS closed: %s', code)

    def _resubscribe(self):
        """Subscribe to price feeds for all active positions (equity + FnO + MCX)."""
        instruments = set()
        for pos in self._pos.active():
            # FnO / MCX — use fno_sec_id
            sec_id = getattr(pos, 'fno_sec_id', '')
            if sec_id:
                _mcx = any(m in pos.symbol.upper() for m in _MCX_SYMS)
                prefix = 'MCX' if _mcx else 'NSE'
                instruments.add(prefix + ':' + str(sec_id))
            else:
                # Equity — resolve from scrip codes
                eq_sec = getattr(pos, '_eq_sec_id', '')
                if not eq_sec:
                    eq_sec = self._resolve_equity_sec(pos.symbol)
                    if eq_sec:
                        pos._eq_sec_id = eq_sec
                if eq_sec:
                    instruments.add('NSE:' + str(eq_sec))

        new = instruments - self._subscribed_instruments
        if new and self._price_ws:
            try:
                self._price_ws.send(json.dumps({
                    'action': 'subscribe', 'mode': 'ltp',
                    'instruments': list(new),
                }))
                self._subscribed_instruments.update(new)
                log.info('Subscribed: %s', new)
            except Exception as e:
                log.warning('Subscribe error: %s', e)

    def on_position_opened(self, pos):
        """Called when a new position is opened — subscribe to its price feed."""
        self._resubscribe()
    def _resolve_equity_sec(self, symbol: str) -> str:
        """Get INDmoney security_id for equity symbol."""
        try:
            from live.indmoney_client import SCRIP_CODES
            scrip = SCRIP_CODES.get(symbol, '')
            if scrip and scrip.startswith('NSE_'):
                return scrip.replace('NSE_', '')
        except Exception:
            pass
        return ''


    # ── Price tick handler ──────────────────────────────────────────

    def _on_price_tick(self, sec_id, ltp):
        """Real-time SL/trail/target check on every tick (equity + FnO + MCX)."""
        # Log first tick per instrument for debugging
        if sec_id not in self._subscribed_instruments and            not hasattr(self, '_first_tick_logged'):
            self._first_tick_logged = set()
        if not hasattr(self, '_first_tick_logged'):
            self._first_tick_logged = set()
        if sec_id not in self._first_tick_logged:
            self._first_tick_logged.add(sec_id)
            log.info('FIRST_TICK | sec_id=%s ltp=%.2f', sec_id, ltp)
        for pos in self._pos.active():
            # Match by fno_sec_id (FnO/MCX) OR equity sec_id
            fno_id = str(getattr(pos, 'fno_sec_id', ''))
            eq_id = str(getattr(pos, '_eq_sec_id', ''))
            matched = False
            if fno_id and (fno_id == sec_id or fno_id.split(':')[-1] == sec_id):
                matched = True
            elif eq_id and (eq_id == sec_id or eq_id.split(':')[-1] == sec_id):
                matched = True
            if not matched:
                continue

            price = ltp
            entry = pos.entry_price
            self._pos.update_peak(pos.id, price)

            if pos.side == Side.BUY:
                move = (price - entry) / entry * 100
            else:
                move = (entry - price) / entry * 100

            # Dynamic SL tightening for anomaly strategy
            if pos.strategy == 'anomaly' and not getattr(pos, '_sl_tightened', False):
                if move >= 0.25:
                    new_sl_pct = 1.0
                    if pos.side == Side.BUY:
                        new_sl = entry * (1 - new_sl_pct / 100)
                        if new_sl > pos.sl:
                            pos.sl = new_sl
                            pos._sl_tightened = True
                            audit('monitor', 'SL_TIGHTEN', pos.symbol,
                                  new_pct=new_sl_pct, move=round(move, 2))
                    else:
                        new_sl = entry * (1 + new_sl_pct / 100)
                        if new_sl < pos.sl:
                            pos.sl = new_sl
                            pos._sl_tightened = True
                            audit('monitor', 'SL_TIGHTEN', pos.symbol,
                                  new_pct=new_sl_pct, move=round(move, 2))

            # SL check
            if pos.sl > 0:
                if (pos.side == Side.BUY and price <= pos.sl) or \
                   (pos.side == Side.SELL and price >= pos.sl):
                    log.info('SL_HIT | %s | price=%.2f sl=%.2f', pos.symbol, price, pos.sl)
                    self._exit(pos.id, price, 'SL_HIT')
                    return

            # Target check
            if pos.target > 0:
                if (pos.side == Side.BUY and price >= pos.target) or \
                   (pos.side == Side.SELL and price <= pos.target):
                    log.info('TARGET_HIT | %s | price=%.2f target=%.2f',
                             pos.symbol, price, pos.target)
                    self._exit(pos.id, price, 'TARGET_HIT')
                    return

            # Trail check
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
                    if drop <= -pos.trail_pct:
                        log.info('TRAIL_EXIT | %s | price=%.2f peak=%.2f drop=%.2f%%',
                                 pos.symbol, price, peak, drop)
                        self._exit(pos.id, price, 'TRAIL_EXIT')
                        return

            # Trailing target extension (BUY LIMIT)
            if getattr(pos, 'gtt_target_id', '') and getattr(pos, 'gtt_target_pct', 0) > 0:
                if entry and entry > 0:
                    _profit_pct = (entry - price) / entry * 100
                    _tgt_pct = pos.gtt_target_pct
                    _last_ext = getattr(pos, '_last_extend_pct', 0)
                    if _profit_pct >= _tgt_pct - 1.0 and _tgt_pct != _last_ext:
                        _new_tgt_pct = _tgt_pct + 0.5
                        _new_tgt_price = tick_round(entry * (1 - _new_tgt_pct / 100))
                        pos._last_extend_pct = _tgt_pct
                        try:
                            self._broker._api.cancel_order(pos.gtt_target_id)
                        except Exception as e:
                            log.warning('Target cancel failed %s: %s', pos.symbol, e)
                        _new_oid = self._broker.buy(pos.symbol, pos.qty,
                                                    price=_new_tgt_price,
                                                    order_type='LIMIT')
                        if _new_oid:
                            audit('monitor', 'TARGET_EXTEND', pos.symbol,
                                  old_tgt=_tgt_pct, new_tgt=_new_tgt_pct,
                                  price=round(_new_tgt_price, 2))
                            pos.gtt_target_id = _new_oid
                            pos.gtt_target_pct = _new_tgt_pct
                        else:
                            log.error('Target extend BUY failed %s', pos.symbol)

    # ── Order Websocket ─────────────────────────────────────────────

    def _run_order_ws(self):
        while self._running:
            try:
                self._order_ws = websocket.WebSocketApp(
                    ORDER_WS_URL,
                    header={'Authorization': self._token},
                    on_open=self._on_order_open,
                    on_message=self._on_order_msg,
                    on_error=self._on_order_error,
                    on_close=self._on_order_close,
                )
                self._order_ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.warning('Order WS crashed: %s', e)
            self._order_ws_connected = False
            if self._running:
                time.sleep(5)

    def _on_order_open(self, ws):
        ws.send(json.dumps({'action': 'subscribe', 'mode': 'order_update'}))
        self._order_ws_connected = True
        log.info('Order WS connected')

    def _on_order_msg(self, ws, message):
        try:
            d = json.loads(message)
            if isinstance(d, str):
                d = json.loads(d)
            log.info('ORDER_UPDATE: %s', json.dumps(d)[:300])
            self._on_order_update(d)
        except Exception as e:
            log.warning('Order msg error: %s', e)

    def _on_order_error(self, ws, error):
        log.warning('Order WS error: %s', error)

    def _on_order_close(self, ws, code, msg):
        self._order_ws_connected = False
        log.info('Order WS closed: %s', code)

    def _on_order_update(self, update):
        """Detect GTT SL/target fires from real-time order updates."""
        d = update.get('data', update)
        order_id = str(d.get('order_id', d.get('id', '')))
        pref = d.get('order_pref', '')
        if pref and not order_id.startswith(pref):
            order_id_full = pref + '-' + order_id
        else:
            order_id_full = order_id
        status = str(d.get('order_status', d.get('status', ''))).upper()
        traded_price = float(d.get('executed_price', d.get('traded_price', 0)) or 0)

        if status not in ('S', 'COMPLETE', 'FILLED', 'SUCCESS'):
            return

        for pos in self._pos.active():
            gtt_sl = str(getattr(pos, 'gtt_sl_id', ''))
            gtt_tgt = str(getattr(pos, 'gtt_target_id', ''))

            _match_sl = gtt_sl and (gtt_sl in (order_id, order_id_full)
                                    or order_id in gtt_sl)
            _match_tgt = gtt_tgt and (gtt_tgt in (order_id, order_id_full)
                                      or order_id in gtt_tgt)

            if _match_sl:
                fill = traded_price if traded_price > 0 else \
                    self._broker.ltp_safe(pos.symbol, pos.entry_price)
                audit('monitor', 'GTT_SL_FIRED', pos.symbol,
                      pos_id=pos.id, fill_price=fill, order_id=order_id)
                if gtt_tgt:
                    try:
                        self._broker._api.cancel_order(gtt_tgt)
                    except Exception:
                        pass
                self._exit(pos.id, fill, 'GTT_SL_FIRED', skip_sell=True)
                return

            if _match_tgt:
                fill = traded_price if traded_price > 0 else \
                    self._broker.ltp_safe(pos.symbol, pos.entry_price)
                audit('monitor', 'GTT_TARGET_FIRED', pos.symbol,
                      pos_id=pos.id, fill_price=fill, order_id=order_id)
                if gtt_sl:
                    try:
                        self._broker._api.cancel_trigger_order(gtt_sl)
                    except Exception:
                        pass
                self._exit(pos.id, fill, 'GTT_TARGET_FIRED', skip_sell=True)
                return

    # ── Polling Fallback ────────────────────────────────────────────

    def _poll_fallback(self):
        """Runs always as safety net. WS handles real-time, poll catches gaps."""
        while self._running:
            time.sleep(self._poll_interval)

            active = self._pos.active()
            if not active:
                continue

            # Only log when WS is down to avoid spam
            if not self._price_ws_connected:
                log.info('POLL_FALLBACK: WS down, checking %d positions', len(active))
            symbols = list(set(p.symbol for p in active))
            try:
                prices = self._broker._api.get_ltp(symbols)
            except Exception:
                prices = {}

            for pos in active:
                try:
                    price = float(prices.get(pos.symbol, 0))
                    if price <= 0 and getattr(pos, 'fno_sec_id', ''):
                        price = self._broker.ltp_fno(pos.fno_sec_id)
                    if price <= 0:
                        continue
                    self._pos.update_peak(pos.id, price)

                    if pos.side == Side.BUY:
                        move = (price - pos.entry_price) / pos.entry_price * 100
                    else:
                        move = (pos.entry_price - price) / pos.entry_price * 100

                    # SL
                    if pos.sl > 0:
                        if (pos.side == Side.BUY and price <= pos.sl) or \
                           (pos.side == Side.SELL and price >= pos.sl):
                            self._exit(pos.id, price, 'SL_HIT')
                            continue
                    # Target
                    if pos.target > 0:
                        if (pos.side == Side.BUY and price >= pos.target) or \
                           (pos.side == Side.SELL and price <= pos.target):
                            self._exit(pos.id, price, 'TARGET_HIT')
                            continue
                    # Trail
                    if pos.trail_activate_pct > 0 and pos.trail_pct > 0:
                        if not pos.trail_active and move >= pos.trail_activate_pct:
                            pos.trail_active = True
                        if pos.trail_active:
                            peak = pos.peak_price
                            if pos.side == Side.BUY:
                                drop = (price - peak) / peak * 100
                            else:
                                drop = (peak - price) / peak * 100
                            if drop <= -pos.trail_pct:
                                self._exit(pos.id, price, 'TRAIL_EXIT')
                except Exception as e:
                    log.warning('Poll check error %s: %s', pos.symbol, e)

    # ── EOD Logic ───────────────────────────────────────────────────

    def _eod_loop(self):
        """Check EOD conditions every 60 seconds."""
        while self._running:
            time.sleep(60)
            try:
                self._eod_check()
            except Exception as e:
                log.error('EOD check error: %s', e)

    def _eod_check(self):
        now = datetime.now()
        _is_nse_eod = now.hour > 15 or (now.hour == 15 and now.minute >= 10)
        _is_mcx_eod = now.hour >= 23 and now.minute >= 25

        if not (_is_nse_eod or _is_mcx_eod):
            return

        # Cancel ALL pending orders
        try:
            _book = self._broker._api.get_order_book() or []
            for _o in _book:
                _st = str(_o.get('status', '')).upper()
                if _st in ('PENDING', 'OPEN', 'CREATED', 'INITIATED'):
                    _oid = _o.get('id', _o.get('order_id', ''))
                    if _oid:
                        try:
                            self._broker._api.cancel_order(str(_oid))
                            log.info('EOD: cancelled order %s', _oid)
                        except Exception as e:
                            log.warning('EOD cancel failed %s: %s', _oid, e)
        except Exception as e:
            log.warning('EOD order sweep failed: %s', e)

        # Cancel GTT trigger orders
        for p in self._pos.active():
            gtt_sl = getattr(p, 'gtt_sl_id', '')
            gtt_tgt = getattr(p, 'gtt_target_id', '')
            if gtt_sl:
                try:
                    self._broker._api.cancel_trigger_order(str(gtt_sl))
                except Exception:
                    pass
            if gtt_tgt:
                try:
                    self._broker._api.cancel_order(str(gtt_tgt))
                except Exception:
                    pass

        # Close positions (skip MCX during NSE EOD)
        active = self._pos.active()
        to_close = []
        for p in active:
            is_mcx = any(m in p.symbol.upper() for m in _MCX_SYMS)
            if is_mcx and _is_nse_eod and not _is_mcx_eod:
                log.info('EOD: keeping MCX %s (closes 11:30)', p.symbol)
                continue
            to_close.append(p)

        if to_close:
            log.info('EOD: closing %d positions', len(to_close))
            for p in to_close:
                self._exit(p.id, reason='EOD_EXIT')

        # Verify broker is clean (retry with widening buffer)
        for attempt in range(5):
            time.sleep(3)
            try:
                from live.indmoney_client import get_positions
                broker_pos = get_positions() or []
                open_pos = [p for p in broker_pos if abs(p.get('net_qty', 0)) > 0]
                if not open_pos:
                    log.info('EOD: broker confirmed zero positions')
                    return
                log.warning('EOD: broker still has %d positions, attempt %d',
                            len(open_pos), attempt + 2)
                for bp in open_pos:
                    sym = bp.get('symbol', '')
                    qty = abs(bp.get('net_qty', 0))
                    is_short = bp.get('net_qty', 0) < 0
                    if qty <= 0 or not sym:
                        continue
                    # Skip MCX during NSE EOD
                    if any(m in sym.upper() for m in _MCX_SYMS) \
                            and _is_nse_eod and not _is_mcx_eod:
                        continue
                    ltp = self._broker.ltp_safe(sym)
                    if ltp <= 0:
                        continue
                    buffer = [0.005, 0.01, 0.02, 0.03, 0.05][min(attempt, 4)]
                    try:
                        if is_short:
                            price = round(ltp * (1 + buffer), 2)
                            self._broker.buy(sym, qty, price=price,
                                             order_type='LIMIT', validity='IOC')
                        else:
                            price = round(ltp * (1 - buffer), 2)
                            self._broker.sell(sym, qty, price=price,
                                              order_type='LIMIT', validity='IOC')
                        log.info('EOD retry: %s qty=%d price=%.2f buffer=%.1f%%',
                                 sym, qty, price, buffer * 100)
                    except Exception as e:
                        log.error('EOD retry failed %s: %s', sym, e)
            except Exception as e:
                log.error('EOD verify error: %s', e)

    # ── Exit ────────────────────────────────────────────────────────

    def _exit(self, pos_id: str, exit_price: float = 0, reason: str = 'MANUAL',
              skip_sell: bool = False):
        pos = self._pos.close(pos_id, exit_price=exit_price, reason=reason,
                              skip_sell=skip_sell)
        if pos:
            audit('monitor', 'EXIT', pos.symbol, reason=reason,
                  entry=pos.entry_price, exit=round(pos.exit_price, 2),
                  pnl=round(pos.pnl, 2), pnl_pct=round(pos.pnl_pct, 2),
                  side=pos.side.value, qty=pos.qty,
                  peak=round(pos.peak_price, 2) if pos.peak_price else 0,
                  trail_active=pos.trail_active)
            tg_alert(f'{reason} {pos.symbol} pnl={pos.pnl:+.0f} '
                     f'({pos.pnl_pct:+.1f}%) entry={pos.entry_price:.1f} '
                     f'exit={pos.exit_price:.1f}')
            if pos.trade_id:
                self._fm.release(pos.trade_id, pnl=pos.pnl)

    # ── Force Exit ──────────────────────────────────────────────────

    def force_exit(self, symbol: str, reason: str = 'SIGNAL_EXIT',
                   strategy: str = None) -> bool:
        """Close position for symbol. If strategy given, only that one."""
        with self._pos._lock:
            candidates = [p for p in self._pos._positions.values()
                          if p.symbol == symbol
                          and (strategy is None or p.strategy == strategy)]
        if not candidates:
            tg_alert(f'EXIT SIGNAL but NO POSITION: {symbol} ({reason})')
            log.warning('force_exit: no position for %s (%s)', symbol, reason)
            return False
        pos = candidates[0]
        price = self._broker.ltp_safe(pos.symbol, pos.entry_price)
        self._exit(pos.id, price, reason)
        return True
