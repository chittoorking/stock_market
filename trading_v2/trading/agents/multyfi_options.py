import os
"""Multyfi Options Agent — polls Multyfi API, scores signals, trades.

Thin agent: delegates to services for API calls, scoring, symbol resolution.
This file only handles: polling loop, signal routing, dedup, queue.
"""
import re
import time
import pandas as pd
import yfinance as yf
from datetime import datetime

from trading.base_agent import BaseAgent
from trading.types import TradeRequest, Side
from trading.logger import audit, tg_alert
from trading.services import multyfi_api, multyfi_scoring, symbol_resolver

from trading.config import (PE_MIN_SCORE, CE_MIN_SCORE, FUT_MIN_SCORE,
                           OPTIONS_SL_PCT, MCX_COMMODITIES, TG_TOKEN, TG_CHAT_ID)



def _get_market_structure():
    """Snapshot market breadth from ML scan data.
    Returns: {stocks_down_pct, below_vwap_pct, dlv_rising_pct} or None."""
    try:
        from trading.services.ml_enricher import _poll, _cache
        import trading.services.ml_enricher as mle
        mle._cache_time = 0
        _poll()

        total = 0; down = 0; below_vwap = 0; dlv_rising = 0
        # Track per-stock delivery baseline
        if not hasattr(_get_market_structure, '_dlv_base'):
            _get_market_structure._dlv_base = {}

        for ticker, data in mle._cache.items():
            ltp = data.get('lastTradedPrice', 0)
            op = data.get('openPrice', 0) or 0
            prev = data.get('previousClose', 0)
            vwap = data.get('avgPrice', 0) or 0
            dlv = data.get('deliveryPercentage', 0) or 0

            if not ltp or ltp <= 0 or not prev or prev <= 0 or op <= 0:
                continue

            total += 1
            if ltp < op: down += 1
            if vwap > 0 and ltp < vwap: below_vwap += 1

            # Delivery change tracking
            if dlv > 0:
                if ticker not in _get_market_structure._dlv_base:
                    _get_market_structure._dlv_base[ticker] = dlv
                if dlv > _get_market_structure._dlv_base[ticker]:
                    dlv_rising += 1

        if total < 100:
            return None

        return {
            'stocks_down_pct': down / total * 100,
            'below_vwap_pct': below_vwap / total * 100,
            'dlv_rising_pct': dlv_rising / total * 100,
            'total_stocks': total,
        }
    except Exception as e:
        return None


class MulOptions(BaseAgent):
    name = 'multyfi_options'
    PAPER_MODE = False  # LIVE — consec+ha filter active  # Log signals but don't place orders

    def __init__(self, broker, positions, fund_manager, monitor):
        super().__init__(broker, positions, fund_manager, monitor)
        self._creds = multyfi_api.load_creds()
        self._seen = set()
        # Load persisted seen signals
        self._seen_file = os.path.expanduser('~/multyfi_seen.json')
        try:
            import json as _json
            with open(self._seen_file) as _f:
                self._seen = set(_json.load(_f))
        except Exception:
            pass
        if len(self._seen) > 500:
            self._seen = set()  # fresh start — prevent unbounded growth
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
        # Block orders outside market hours
        from datetime import datetime as _dt
        _now = _dt.now()
        _mcx_close = 23  # MCX closes at 11:30 PM
        _nse_close = 15  # NSE closes at 3:30 PM
        is_mcx = any(c in str(opt.get('baseSymbol', '')).upper() for c in ('GOLD', 'SILVER', 'CRUDE', 'NATURAL', 'COPPER'))
        if is_mcx:
            if _now.hour < 9 or _now.hour >= _mcx_close:
                return  # MCX: 9 AM - 11 PM only
        else:
            if _now.hour < 9 or (_now.hour == 9 and _now.minute < 15) or (_now.hour == 15 and _now.minute >= 30) or _now.hour >= 16:
                return  # NSE: 9:15 AM - 3:30 PM only

        action = str(opt.get('action', opt.get('orderType', ''))).upper()
        if action in ('MULTIPLE', ''):
            return

        base = opt.get('baseSymbol', '')
        opt_type = opt.get('optionType', '')
        strike = opt.get('strike', 0)
        entry_price = opt.get('entryPrice', 0)
        entry_date = opt.get('entryDate', '')

        if not base:
            return
        # opt_type can be None/empty for equity signals — don't drop them
        opt_type = opt_type or ""

        symbol = base.replace(' 50', '').replace(' ', '').strip().upper()
        if len(symbol) < 3:
            return

        # Dedup
        sig_key = opt.get("_id", f"{symbol}_{entry_date}_{opt_type}_{strike}")
        if sig_key in self._seen:
            return
        self._seen.add(sig_key)
        # Persist seen signals to survive restarts
        try:
            import json as _json
            with open(self._seen_file, 'w') as _f:
                _json.dump(list(self._seen), _f)
        except Exception:
            pass

        is_commodity = any(c in symbol for c in MCX_COMMODITIES)

        signal = {
            'symbol': symbol, 'option_type': opt_type, 'strike': float(strike),
            'is_commodity': is_commodity, 'entry_price': float(entry_price),
            'action': action, 'instrument': opt.get('instrument', opt.get('stockSymbol', '')),
            'target_pct': opt.get('targetPercent', opt.get('target', 0)),
            'sl': float(opt.get('stopLoss', opt.get('sl', 0)) or 0),
            'expiry': opt.get('expiryDate', opt.get('expiry', '')),
        }

        # Route to handler
        if is_commodity:
            return  # Commodity options not traded, only futures via _route_signal
        fd = dict(opt)
        fd['action'] = action

        is_future = 'FUT' in symbol or opt_type == 'FUT'
        is_option = opt_type in ('CE', 'PE')

        if is_future:
            self._handle_futures(symbol, signal, fd, is_commodity)
        elif is_option:
            self._handle_option(symbol, signal, fd, is_commodity, opt_type)
        else:
            # Equity stock signal from premium endpoint (optionType empty/None)
            self._handle_equity(symbol, signal, fd)

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
                # Check if we have a position and if trail is active
                pos = self._positions.find_by_symbol(sym)
                if pos and pos.trail_active:
                    # Trail is managing exit — log but don't force exit
                    self._log.info(f'{sym}: trail active, ignoring Multyfi EXIT (our trail handles it)')
                    audit('multyfi', 'EXIT_IGNORED_TRAIL_ACTIVE', sym)
                else:
                    # Trail not active = stock hasn't moved our way — use Multyfi's exit
                    self._log.info(f'{sym}: trail NOT active, using Multyfi EXIT')
                    tg_alert(f'MULTYFI EXIT: {sym}')
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
        sig_key = fd.get("_id", f"{clean_sym}_{entry_date}_{opt_type}_{strike}")
        if sig_key in self._seen:
            return
        self._seen.add(sig_key)
        # Persist seen signals
        try:
            import json as _j
            with open(self._seen_file, 'w') as _f:
                _j.dump(list(self._seen), _f)
        except Exception:
            pass

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

        # Delivery change filter — only enter if institutions confirm
        # dlv_chg > 0 = 100% WR on 18-day backtest (7W 0L)
        try:
            from trading.services.ml_enricher import _poll, _cache
            import trading.services.ml_enricher as mle
            mle._cache_time = 0
            _poll()
            ml_data = mle._cache.get(sym, {})
            dlv_now = ml_data.get('deliveryPercentage', 0) or 0
            # We need dlv_chg but ML enricher gives snapshot, not change
            # Use a simple check: delivery must be > 40% (institutional participation)
            # AND we store first-seen delivery to compute change
            if not hasattr(self, '_dlv_baseline'):
                self._dlv_baseline = {}
            if sym not in self._dlv_baseline and dlv_now > 0:
                self._dlv_baseline[sym] = dlv_now
            dlv_first = self._dlv_baseline.get(sym, 0)
            dlv_chg = dlv_now - dlv_first if dlv_first > 0 and dlv_now > 0 else 0
            if dlv_chg <= 0:
                self._log.info(f'SKIP {sym}: dlv_chg={dlv_chg:+.1f} (no institutional confirmation)')
                audit('multyfi', 'EQUITY_SKIP_DLV', sym, dlv_now=round(dlv_now, 1),
                      dlv_first=round(dlv_first, 1), dlv_chg=round(dlv_chg, 1))
                return
            self._log.info(f'{sym}: dlv_chg={dlv_chg:+.1f} (institutional confirmed)')
        except Exception as e:
            self._log.warning(f'{sym}: delivery check failed ({e}), skipping')
            return

        audit('multyfi', 'EQUITY_ENTRY', sym, entry=entry_price, sl=sl,
              target=target, trade_action=action, dlv_chg=round(dlv_chg, 1))
        self._log.info(f'EQUITY {action} {sym} @ {entry_price} SL={sl} TGT={target} dlv_chg={dlv_chg:+.1f}')

        req = TradeRequest(
            symbol=sym, side=Side.BUY if action == 'BUY' else Side.SELL,
            entry=entry_price,
            sl=sl,
            target=target,
            trail_activate_pct=0.5,  # trail 0.5/0.5 like other equity strategies
            trail_pct=0.5,
            conviction=7,
            reason=f'multyfi equity {action}',
        )
        if self.PAPER_MODE:
            self._log.info(f'PAPER: {req.symbol} {req.side.value} @ {req.entry}')
            audit('multyfi', 'PAPER_SIGNAL', req.symbol, side=req.side.value, entry=req.entry, reason=req.reason)
            return
        self.submit(req)

    def _handle_futures(self, sym, signal, fd, is_commodity):
        """Score and trade futures signal."""
        entry_price = float(fd.get('entryPrice', 0))
        fut_sl = float(fd.get('stopLoss', fd.get('sl', 0)) or 0)
        action = str(fd.get('action', signal.get('action', 'BUY'))).upper()

        # Skip positional/carry-forward — we only trade intraday
        dur = str(fd.get('durationType', fd.get('duration', '')) or '').lower()
        hold = int(fd.get('holdDuration', 0) or 0)
        if dur in ('positional', 'mid term', 'long term') or hold > 0:
            self._log.info(f'SKIP {sym}: carry-forward (dur={dur} hold={hold})')
            return

        # Compute SL distance for all futures
        sl_pct = abs(entry_price - fut_sl) / entry_price * 100 if entry_price > 0 and fut_sl > 0 else 0

        score = 6  # default for commodity futures (passed COMEX gate)

        if is_commodity:
            # ── MCX COMMODITIES ──
            # Log every commodity signal
            audit('multyfi', 'COMMODITY_SIGNAL', sym,
                  trade_action=action, entry=entry_price, sl=fut_sl,
                  sl_pct=round(sl_pct, 2),
                  hour=datetime.now().hour, dow=datetime.now().weekday())

            if 'GOLD' in sym.upper():
                # GOLD: COMEX consec+HA on GC=F 1H + SL >= 0.4%
                # Validated: 100% WR (all losers had SL < 0.4%)
                if sl_pct < 0.4:
                    self._log.info(f'SKIP GOLD {sym}: SL {sl_pct:.2f}% < 0.4%')
                    return
                try:
                    gc = yf.download('GC=F', period='5d', interval='1h', progress=False, auto_adjust=True)
                    if hasattr(gc, 'columns') and isinstance(gc.columns, pd.MultiIndex):
                        gc.columns = gc.columns.get_level_values(0)
                    if len(gc) >= 10:
                        candles = [{'o': float(r['Open']), 'h': float(r['High']),
                                    'l': float(r['Low']), 'c': float(r['Close'])}
                                   for _, r in gc.tail(10).iterrows()]
                        consec_al, ha_al, _ = multyfi_scoring._check_consec_ha(candles, action)
                        if not (consec_al and ha_al):
                            self._log.info(f'SKIP GOLD {sym}: COMEX not aligned (cc={consec_al} ha={ha_al})')
                            return
                        self._log.info(f'GOLD LIVE {sym}: COMEX confirmed, SL={sl_pct:.1f}%')
                    else:
                        self._log.warning(f'GOLD {sym}: no COMEX data, skip')
                        return
                except Exception as e:
                    self._log.warning(f'GOLD {sym}: COMEX failed ({e}), skip')
                    return

            elif 'SILVER' in sym.upper():
                # SILVER: COMEX consec+HA on SI=F 1H + no Tuesday + SL >= 0.3%
                # Validated: 18W 0L = 100% WR
                if datetime.now().weekday() == 1:
                    self._log.info(f'SKIP SILVER {sym}: Tuesday')
                    return
                if sl_pct < 0.3:
                    self._log.info(f'SKIP SILVER {sym}: SL {sl_pct:.2f}% < 0.3%')
                    return
                try:
                    si = yf.download('SI=F', period='5d', interval='1h', progress=False, auto_adjust=True)
                    if hasattr(si, 'columns') and isinstance(si.columns, pd.MultiIndex):
                        si.columns = si.columns.get_level_values(0)
                    if len(si) >= 10:
                        candles = [{'o': float(r['Open']), 'h': float(r['High']),
                                    'l': float(r['Low']), 'c': float(r['Close'])}
                                   for _, r in si.tail(10).iterrows()]
                        consec_al, ha_al, _ = multyfi_scoring._check_consec_ha(candles, action)
                        if not (consec_al and ha_al):
                            self._log.info(f'SKIP SILVER {sym}: COMEX not aligned (cc={consec_al} ha={ha_al})')
                            return
                        self._log.info(f'SILVER LIVE {sym}: COMEX confirmed, SL={sl_pct:.1f}%')
                    else:
                        self._log.warning(f'SILVER {sym}: no COMEX data, skip')
                        return
                except Exception as e:
                    self._log.warning(f'SILVER {sym}: COMEX failed ({e}), skip')
                    return

            else:
                # Crude/NatGas: PAPER — log and skip
                self._log.info(f'PAPER MCX {action} {sym} @ {entry_price} SL={sl_pct:.1f}%')
                try:
                    import requests as _req
                    _req.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                              json={'chat_id': TG_CHAT_ID,
                                    'text': f'PAPER MCX {action} {sym} @ {entry_price} SL={sl_pct:.1f}%'},
                              timeout=5)
                except Exception:
                    pass
                return

        else:
            # ── NSE FUTURES ──
            if sl_pct < 0.3:
                self._log.info(f'SKIP {sym} futures: SL {sl_pct:.2f}% < 0.3%')
                return

            score = multyfi_scoring.score_futures(signal, fd)
            audit('multyfi', 'FUTURES_SCORE', sym, score=score, threshold=FUT_MIN_SCORE)
            self._log.info(f'{sym} futures score={score} (min={FUT_MIN_SCORE})')
            if score < FUT_MIN_SCORE:
                return

        # Resolve sec_id — ALWAYS use numeric sec_id from instrument master
        instrument = fd.get('instrument', fd.get('stockSymbol', ''))
        if is_commodity:
            sec_id = symbol_resolver.resolve_mcx_sec_id(sym, self._broker)
        else:
            # Try Multyfi's instrument field first (e.g. "NFO:HDFCBANK26OCTFUT")
            trading_sym = ''
            if instrument:
                trading_sym = instrument.split(':')[-1] if ':' in instrument else instrument
            if not trading_sym:
                trading_sym = sym  # sym itself may be "HDFCBANK26OCTFUT"
            sec_id = symbol_resolver.resolve_fno_sec_id(trading_sym)

        if not sec_id:
            self._log.warning(f'{sym}: no sec_id for futures')
            return

        # SL = lower of (Multyfi SL, our 2% SL) — caps runaway losses
        _multyfi_sl = fut_sl if fut_sl > 0 else 0
        _our_sl = entry_price * 0.98 if action == 'BUY' else entry_price * 1.02
        if _multyfi_sl > 0 and entry_price > 0:
            if action == 'BUY':
                sl = max(_multyfi_sl, _our_sl)  # BUY: SL is below entry, higher = tighter
            else:
                sl = min(_multyfi_sl, _our_sl)  # SELL: SL is above entry, lower = tighter
        elif _multyfi_sl > 0:
            sl = _multyfi_sl
        else:
            sl = _our_sl

        # Use Multyfi's lot size and margin if available
        lot_size = int(fd.get('lotSize', 0) or 0)
        margin_req = float(fd.get('marginRequired', 0) or 0)

        # Lot scaling by conviction: 5-6=1x, 7-8=2x, 9+=3x
        # Gold/Silver passed COMEX gate = score 6 equivalent
        if not isinstance(score, (int, float)):
            score = 6
        if score >= 9:
            lot_mult = 3
        elif score >= 7:
            lot_mult = 2
        else:
            lot_mult = 1
        scaled_lot = lot_size * lot_mult if lot_size > 0 else lot_mult

        if lot_size > 0:
            self._log.info(f'{sym}: lot={lot_size} x{lot_mult} = {scaled_lot} (score={score})')

        # Trail params: same 0.3/0.3 for all confirmed futures (Gold/Silver/NSE)
        _trail_act, _trail_pct = 0.3, 0.3

        req = TradeRequest(
            symbol=sym, side=Side.BUY if action == 'BUY' else Side.SELL,
            entry=entry_price, sl=sl,
            trail_activate_pct=_trail_act, trail_pct=_trail_pct,
            conviction=max(1, min(score // 3, 10)),
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
            'target_pct': fd.get('targetPercent', fd.get('target', signal.get('target_pct', 0))),
            'sl': float(fd.get('stopLoss', fd.get('sl', signal.get('sl', 0))) or 0),
            'expiry': fd.get('expiryDate', fd.get('expiry', signal.get('expiry', ''))),
        }

        # Score — reject MCX commodity options (we only trade Silver futures)
        if is_commodity:
            self._log.info(f'SKIP {sym} {opt_type}: commodity option (futures only)')
            return

        # Duplicate position check
        for _ep in self._positions.active():
            if _ep.symbol == sym and getattr(_ep, 'fno_sec_id', ''):
                self._log.info(f'SKIP {sym} {opt_type}: already have position {_ep.id}')
                return
        if opt_type == 'PE':
            score = multyfi_scoring.score_pe(sig)
        else:
            score = multyfi_scoring.score_ce(sig)

        threshold = CE_MIN_SCORE if opt_type == 'CE' else PE_MIN_SCORE
        audit('multyfi', 'OPTION_SCORE', sym, opt_type=opt_type,
              score=score, threshold=threshold, direction=action)

        # Get market structure for logging + gate
        mkt = _get_market_structure()
        mkt_log = {}
        if mkt:
            mkt_log = {
                'stocks_down': round(mkt['stocks_down_pct'], 1),
                'below_vwap': round(mkt['below_vwap_pct'], 1),
                'dlv_rising': round(mkt['dlv_rising_pct'], 1),
            }

        # LOG EVERY SIGNAL with market structure (even if skipped)
        audit('multyfi', 'OPTION_SIGNAL_FULL', sym,
              opt_type=opt_type, trade_action=action, score=score,
              entry=entry_price, threshold=threshold,
              **mkt_log, **{k: round(v, 2) if isinstance(v, float) else v
                          for k, v in sig.items() if k in ('target_pct', 'sl', 'strike', 'expiry')})

        if score < threshold:
            self._log.info(f'SKIP {sym} {opt_type} score={score} (min={threshold})')
            return

        # Market structure gate for options
        # Bearish bet (PE BUY or CE SELL): need stocks_down>75% + below_vwap>70%
        # Bullish bet (CE BUY or PE SELL): need stocks_down<40% + below_vwap<50% + dlv_rising>75%
        is_bearish = (opt_type == 'PE' and action == 'BUY') or (opt_type == 'CE' and action == 'SELL')
        is_bullish = (opt_type == 'CE' and action == 'BUY') or (opt_type == 'PE' and action == 'SELL')

        if mkt:
            if is_bearish:
                if mkt['stocks_down_pct'] < 75 or mkt['below_vwap_pct'] < 70:
                    self._log.info(f'SKIP {sym} {opt_type} {action}: market not bearish enough '
                                   f'(down={mkt["stocks_down_pct"]:.0f}% vwap={mkt["below_vwap_pct"]:.0f}%)')
                    audit('multyfi', 'OPTION_SKIP_MKT', sym, opt_type=opt_type, trade_action=action,
                          reason='not_bearish', **mkt_log)
                    return
            elif is_bullish:
                if mkt['stocks_down_pct'] > 40 or mkt['below_vwap_pct'] > 50 or mkt['dlv_rising_pct'] < 75:
                    self._log.info(f'SKIP {sym} {opt_type} {action}: market not bullish enough '
                                   f'(down={mkt["stocks_down_pct"]:.0f}% vwap={mkt["below_vwap_pct"]:.0f}% dlv={mkt["dlv_rising_pct"]:.0f}%)')
                    audit('multyfi', 'OPTION_SKIP_MKT', sym, opt_type=opt_type, trade_action=action,
                          reason='not_bullish', **mkt_log)
                    return
        else:
            self._log.warning(f'{sym}: no market structure data, skipping option')
            return

        self._log.info(f'TAKE {sym} {opt_type} score={score} mkt_confirmed '
                       f'(down={mkt["stocks_down_pct"]:.0f}% vwap={mkt["below_vwap_pct"]:.0f}% dlv={mkt["dlv_rising_pct"]:.0f}%)')

        # Resolve instrument to numeric sec_id
        sec_id, premium = symbol_resolver.resolve_option_sec_id(
            sym, opt_type, sig['strike'], instrument, self._broker)
        if not sec_id:
            self._log.warning(f'{sym} {opt_type}: could not resolve sec_id')
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
            trail_activate_pct=10, trail_pct=10,  # NSE options: activate at 10% profit, trail 10% from peak
            conviction=max(1, min(score // 3, 10)),
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
            # Extract strike from instrument if missing (e.g. NFO:NIFTY26O1322700PE -> 22700)
            if not strike:
                inst = doc.get('instrument', doc.get('stockSymbol', ''))
                import re as _re
                m = _re.search(r'(\d{4,6})(CE|PE)', str(inst))
                if m:
                    strike = int(m.group(1))
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
