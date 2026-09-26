"""Multyfi Options Agent — polls Multyfi API, scores signals, trades.

Thin agent: delegates to services for API calls, scoring, symbol resolution.
This file only handles: polling loop, signal routing, dedup, queue.
"""
import re
import time
from datetime import datetime

from trading.base_agent import BaseAgent
from trading.types import TradeRequest, Side
from trading.logger import audit
from trading.services import multyfi_api, multyfi_scoring, symbol_resolver

from trading.config import (PE_MIN_SCORE, CE_MIN_SCORE, FUT_MIN_SCORE,
                           OPTIONS_SL_PCT, MCX_COMMODITIES)


class MulOptions(BaseAgent):
    name = 'multyfi_options'

    def __init__(self, broker, positions, fund_manager, monitor):
        super().__init__(broker, positions, fund_manager, monitor)
        self._creds = multyfi_api.load_creds()
        self._seen = set()
        self._seen_stream_ids = set()
        self._queued = []

    # ── Main loop ───────────────────────────────────────────────

    def run(self):
        if not self._creds.get('authToken'):
            self._log.error('No Multyfi auth token — agent disabled')
            return

        self._log.info(f'Multyfi polling (interval=5s, min={PE_MIN_SCORE})')

        while True:
            now = datetime.now()
            if now.hour < 3 or now.hour >= 23 or (now.hour == 23 and now.minute >= 30):
                time.sleep(60)
                continue

            # Process queued pre-market signals at 9:15
            if self._queued and now.hour == 9 and now.minute >= 15:
                self._log.info(f'Processing {len(self._queued)} queued signals')
                for q in self._queued:
                    try:
                        self._route_signal(q['signal'], q['fd'])
                    except Exception as e:
                        self._log.error(f'Queue error: {e}')
                self._queued.clear()

            try:
                self._poll()
            except Exception as e:
                self._log.error(f'Poll error: {e}')

            time.sleep(5)

    # ── Polling ─────────────────────────────────────────────────

    def _poll(self):
        # 1. Active options + futures (entry signals)
        for ep in ['/options/premium', '/futures/premium']:
            for item in multyfi_api.fetch_premium(self._creds, ep):
                self._handle_premium_item(item)

        # 2. Streams (exit signals + new recommendations)
        for stream in multyfi_api.fetch_streams(self._creds):
            self._handle_stream(stream)

    def _handle_premium_item(self, opt: dict):
        """Route signal from /options/premium or /futures/premium."""
        action = str(opt.get('action', opt.get('orderType', ''))).upper()
        if action in ('MULTIPLE', ''):
            return

        base = opt.get('baseSymbol', '')
        opt_type = opt.get('optionType', '')
        strike = opt.get('strike', 0)
        entry_price = opt.get('entryPrice', 0)
        entry_date = opt.get('entryDate', '')

        if not base or not opt_type:
            return

        symbol = base.replace(' 50', '').replace(' ', '').strip().upper()
        if len(symbol) < 3:
            return

        # Dedup
        sig_key = f"{symbol}_{entry_date}_{opt_type}_{strike}"
        if sig_key in self._seen:
            return
        self._seen.add(sig_key)

        is_commodity = any(c in symbol for c in MCX_COMMODITIES)

        signal = {
            'symbol': symbol, 'option_type': opt_type, 'strike': float(strike),
            'is_commodity': is_commodity, 'entry_price': float(entry_price),
            'action': action, 'instrument': opt.get('instrument', opt.get('stockSymbol', '')),
        }

        # Score
        if is_commodity:
            score = PE_MIN_SCORE
            self._log.info(f'Commodity {symbol} {opt_type}: auto-score={score}')
        elif opt_type == 'PE':
            score = multyfi_scoring.score_pe(signal)
        else:
            score = multyfi_scoring.score_ce(signal)

        threshold = CE_MIN_SCORE if opt_type == 'CE' else PE_MIN_SCORE
        audit('multyfi', 'SIGNAL_SCORE', symbol, opt_type=opt_type,
              score=score, threshold=threshold, direction=action)

        if score >= threshold:
            self._log.info(f'TAKE {symbol} {opt_type} {strike} score={score} (min={threshold})')
            self._open_option(signal, opt_type, score, action)
        else:
            self._log.info(f'SKIP {symbol} {opt_type} {strike} score={score} (min={threshold})')

    def _handle_stream(self, stream: dict):
        """Route stream item — EXIT or NEW RECOMMENDATION."""
        stream_id = stream.get('_id', '')
        if stream_id in self._seen_stream_ids:
            return
        self._seen_stream_ids.add(stream_id)

        msg = stream.get('message', '').upper()
        fd = stream.get('fullDocument') or {}
        action = str(fd.get('action', fd.get('orderType', ''))).upper()

        if 'EXIT' in msg:
            signal = self._extract_signal(stream)
            if signal:
                sym = signal['symbol']
                self._log.info(f'EXIT signal: {sym}')
                audit('multyfi', 'EXIT_SIGNAL', sym)
                self._monitor.force_exit(sym, 'MULTYFI_EXIT', strategy=self.name)

        elif 'NEW' in msg and 'RECOMMENDATION' in msg:
            if action == 'MULTIPLE':
                self._log.info(f'SKIP {fd.get("baseSymbol","")} multi-leg')
                return
            signal = self._extract_signal(stream)
            if signal:
                self._route_signal(signal, fd)

    # ── Signal routing ──────────────────────────────────────────

    def _route_signal(self, signal: dict, fd: dict):
        """Route by type: stock, future, or option."""
        sym = signal['symbol']
        opt_type = signal.get('option_type', '')
        entry_price = float(fd.get('entryPrice', 0))
        is_commodity = any(c in sym.upper() for c in MCX_COMMODITIES)

        # Dedup
        entry_date = fd.get('entryDate', signal.get('date', ''))
        strike = signal.get('strike', 0)
        clean_sym = sym.replace(' 50', '').replace(' ', '').strip().upper()
        sig_key = f"{clean_sym}_{entry_date}_{opt_type}_{strike}"
        if sig_key in self._seen:
            return
        self._seen.add(sig_key)

        # Pre-market queue
        now = datetime.now()
        if not is_commodity and (now.hour < 9 or (now.hour == 9 and now.minute < 15)):
            self._log.info(f'QUEUED {sym}: pre-market')
            self._queued.append({'signal': signal, 'fd': fd})
            return

        is_option = opt_type in ('CE', 'PE')
        is_future = 'FUT' in sym or opt_type == 'FUT'

        audit('multyfi', 'ENTRY_SIGNAL', sym, opt_type=opt_type,
              entry=entry_price, is_commodity=is_commodity)

        if is_future:
            self._handle_futures(sym, signal, fd, is_commodity)
        elif is_option:
            self._handle_option(sym, signal, fd, is_commodity, opt_type)
        else:
            # Equity stock — intraday MIS
            self._handle_equity(sym, signal, fd)


    def _handle_equity(self, sym, signal, fd):
        """Trade equity stock signal — intraday MIS, exit on Multyfi EXIT."""
        entry_price = float(fd.get('entryPrice', 0))
        sl = float(fd.get('stopLoss', 0) or 0)
        target = float(fd.get('targetPrice', fd.get('target', 0)) or 0)
        action = str(fd.get('action', signal.get('action', 'BUY'))).upper()

        if entry_price <= 0:
            return

        # Skip swing/positional — only intraday
        dur = str(fd.get('durationType', fd.get('duration', '')) or '').lower()
        if dur in ('positional', 'mid term', 'long term'):
            self._log.info(f'SKIP {sym}: duration={dur} (not intraday)')
            return

        # SL distance check
        if sl > 0 and entry_price > 0:
            sl_pct = abs(entry_price - sl) / entry_price * 100
            # Skip if SL > 5% (swing trade, not intraday)
            if sl_pct > 5:
                self._log.info(f'SKIP {sym}: SL {sl_pct:.1f}% > 5% (swing)')
                return

        # Position dedup
        if self._positions.find_by_symbol(sym):
            self._log.info(f'{sym}: already in position')
            return

        # VWAP filter — only enter if price > today's VWAP
        try:
            vwap = self._broker.get_vwap(sym)
            if vwap and vwap > 0:
                if action == 'BUY' and entry_price < vwap:
                    self._log.info(f'SKIP {sym}: price {entry_price} < VWAP {vwap:.1f}')
                    audit('multyfi', 'EQUITY_SKIP_VWAP', sym, price=entry_price, vwap=round(vwap, 1))
                    return
                elif action == 'SELL' and entry_price > vwap:
                    self._log.info(f'SKIP {sym}: price {entry_price} > VWAP {vwap:.1f} (short needs below)')
                    audit('multyfi', 'EQUITY_SKIP_VWAP', sym, price=entry_price, vwap=round(vwap, 1))
                    return
                self._log.info(f'{sym}: price {entry_price} vs VWAP {vwap:.1f} OK')
        except Exception as e:
            self._log.warning(f'{sym}: VWAP check failed ({e}), proceeding anyway')

        audit('multyfi', 'EQUITY_ENTRY', sym, entry=entry_price, sl=sl,
              target=target, action=action)
        self._log.info(f'EQUITY {action} {sym} @ {entry_price} SL={sl} TGT={target}')

        req = TradeRequest(
            symbol=sym, side=Side.BUY if action == 'BUY' else Side.SELL,
            entry=entry_price,
            sl=sl,
            target=target,
            trail_activate_pct=0,  # no trail — trust Multyfi exit
            trail_pct=0,
            conviction=7,
            reason=f'multyfi equity {action}',
        )
        self.submit(req)

    def _handle_futures(self, sym, signal, fd, is_commodity):
        """Score and trade futures signal."""
        entry_price = float(fd.get('entryPrice', 0))
        fut_sl = float(fd.get('stopLoss', fd.get('sl', 0)) or 0)
        action = str(fd.get('action', signal.get('action', 'BUY'))).upper()

        # SL filter
        if fut_sl > 0 and entry_price > 0:
            sl_pct = abs(entry_price - fut_sl) / entry_price * 100
            if sl_pct < 0.3:
                self._log.info(f'SKIP {sym} futures: SL {sl_pct:.2f}% < 0.3%')
                return

        # Score
        if is_commodity:
            score, threshold = PE_MIN_SCORE, PE_MIN_SCORE
        else:
            score = multyfi_scoring.score_futures(signal, fd)
            threshold = FUT_MIN_SCORE

        audit('multyfi', 'FUTURES_SCORE', sym, score=score, threshold=threshold)
        self._log.info(f'{sym} futures score={score} (min={threshold})')

        if score < threshold:
            return

        # Resolve sec_id
        instrument = fd.get('instrument', fd.get('stockSymbol', ''))
        if is_commodity:
            # MCX: resolve from MCX instrument master
            sec_id = symbol_resolver.resolve_mcx_sec_id(sym, self._broker)
        elif instrument:
            # NSE: use Multyfi's instrument ID directly
            # "NFO:SBIN26SEPFUT" -> "SBIN26SEPFUT"
            sec_id = instrument.split(':')[-1] if ':' in instrument else instrument
            self._log.info(f'{sym}: using Multyfi instrument {sec_id}')
        else:
            # Fallback: use the stockSymbol as sec_id
            sec_id = sym
            self._log.info(f'{sym}: using symbol as sec_id')

        if not sec_id:
            self._log.warning(f'{sym}: no sec_id')
            return

        sl = fut_sl if fut_sl > 0 else (entry_price * 0.98 if entry_price > 0 else 0)

        # Use Multyfi's lot size and margin if available
        lot_size = int(fd.get('lotSize', 0) or 0)
        margin_req = float(fd.get('marginRequired', 0) or 0)

        # Lot scaling by conviction: 5-6=1x, 7-8=2x, 9+=3x
        if score >= 9:
            lot_mult = 3
        elif score >= 7:
            lot_mult = 2
        else:
            lot_mult = 1
        scaled_lot = lot_size * lot_mult if lot_size > 0 else lot_mult

        if lot_size > 0:
            self._log.info(f'{sym}: lot={lot_size} x{lot_mult} = {scaled_lot} (score={score})')

        req = TradeRequest(
            symbol=sym, side=Side.BUY if action == 'BUY' else Side.SELL,
            entry=entry_price, sl=sl,
            trail_activate_pct=0, trail_pct=0,
            conviction=min(score // 3, 10),
            reason=f'multyfi futures {action} score={score} x{lot_mult}',
            is_fno=True, fno_sec_id=sec_id, fno_lot_size=scaled_lot, fno_margin=margin_req,
        )
        self.submit(req)

    def _handle_option(self, sym, signal, fd, is_commodity, opt_type):
        """Score and trade option signal."""
        entry_price = float(fd.get('entryPrice', signal.get('entry_price', 0)))
        action = str(fd.get('action', signal.get('action', 'BUY'))).upper()
        instrument = fd.get('instrument', fd.get('stockSymbol', ''))

        sig = {
            'symbol': sym, 'option_type': opt_type,
            'strike': float(signal.get('strike', 0)),
            'is_commodity': is_commodity, 'entry_price': entry_price,
            'action': action, 'instrument': instrument,
        }

        # Score
        if is_commodity:
            score = PE_MIN_SCORE
        elif opt_type == 'PE':
            score = multyfi_scoring.score_pe(sig)
        else:
            score = multyfi_scoring.score_ce(sig)

        threshold = CE_MIN_SCORE if opt_type == 'CE' else PE_MIN_SCORE
        audit('multyfi', 'OPTION_SCORE', sym, opt_type=opt_type,
              score=score, threshold=threshold, direction=action)

        if score < threshold:
            self._log.info(f'SKIP {sym} {opt_type} score={score} (min={threshold})')
            return

        self._log.info(f'TAKE {sym} {opt_type} score={score} (min={threshold})')

        # Resolve instrument
        sec_id, premium = symbol_resolver.resolve_option_sec_id(
            sym, opt_type, sig['strike'], instrument, self._broker)
        if not sec_id:
            return

        opt_premium = premium or entry_price
        sl_price = opt_premium * (1 - OPTIONS_SL_PCT / 100) if opt_premium > 0 else 0

        # Use Multyfi's lot size if available
        lot_size = int(fd.get('lotSize', 0) or 0)

        # Lot scaling by conviction: 20-22=1x, 23-25=2x, 26+=3x
        if score >= 26:
            lot_mult = 3
        elif score >= 23:
            lot_mult = 2
        else:
            lot_mult = 1
        scaled_lot = lot_size * lot_mult if lot_size > 0 else lot_mult

        if lot_size > 0:
            self._log.info(f'{sym}: lot={lot_size} x{lot_mult} = {scaled_lot} (score={score})')

        req = TradeRequest(
            symbol=sym, side=Side.BUY if action == 'BUY' else Side.SELL,
            entry=opt_premium, sl=sl_price,
            trail_activate_pct=0, trail_pct=0,
            conviction=min(score // 3, 10),
            reason=f'multyfi {opt_type} {action} score={score} x{lot_mult}',
            is_fno=True, fno_sec_id=sec_id, fno_lot_size=scaled_lot,
        )
        self.submit(req)

    # ── Helpers ──────────────────────────────────────────────────

    def _extract_signal(self, stream: dict) -> dict:
        """Parse signal from stream fullDocument."""
        try:
            doc = stream.get('fullDocument', stream)
            action = doc.get('action', doc.get('orderType', '')).upper()
            if action == 'MULTIPLE':
                return None

            base = doc.get('baseSymbol', '')
            symbol = doc.get('stockSymbol', base)
            opt_type = doc.get('optionType', '')
            strike = doc.get('strike', 0)
            entry_price = doc.get('entryPrice', 0)

            text = (stream.get('message', '') + ' ' + stream.get('title', '')).upper()
            if not opt_type:
                opt_type = next((t for t in ('CE', 'PE', 'FUT') if t in text), None)
                if not opt_type:
                    return None

            if not symbol or len(symbol) < 3:
                skip = {'BUY', 'SELL', 'CE', 'PE', 'FUT', 'AT', 'EXIT', 'TARGET', 'SL', 'TRADE'}
                symbol = next((w for w in re.findall(r'[A-Z]{3,15}', text) if w not in skip), None)
                if not symbol:
                    return None

            if 'NIFTY' in symbol.upper():
                symbol = 'NIFTY'
            symbol = re.sub(r'\s+\d+$', '', symbol).strip().upper()
            if not symbol or len(symbol) < 3:
                return None

            msg = stream.get('message', '').upper()
            if 'EXIT' in msg and action != 'SELL':
                action = 'SELL'
            if action not in ('BUY', 'SELL'):
                action = 'BUY'

            return {
                'symbol': symbol, 'option_type': opt_type, 'action': action,
                'strike': float(strike) if strike else 0,
                'entry_price': float(entry_price) if entry_price else 0,
                'date': stream.get('createdAt', stream.get('entryDate', ''))[:10],
            }
        except Exception:
            return None
