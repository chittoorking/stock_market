"""
Broker service — abstract interface to any broker.
Supports: Paper trading, Zerodha Kite, Alpaca, CCXT (crypto).
The agent is broker-agnostic — all broker-specific logic is hidden behind this interface.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from app.core.config import settings

logger = logging.getLogger(__name__)


class BrokerService:
    """
    Unified broker interface. Dispatches to the active broker backend.
    Paper trading mode is always available for testing.
    """

    def __init__(self):
        self.active_broker = settings.get_active_broker()
        self._paper_positions: Dict[str, Dict] = {}  # symbol -> position
        self._paper_orders: Dict[str, Dict] = {}  # order_id -> order
        self._paper_balance: float = 1_000_000.0  # 10L paper balance

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place an order with the active broker."""
        symbol = symbol.upper()

        if self.active_broker == "upstox":
            return await self._upstox_place_order(symbol, side, quantity, order_type, price)
        elif self.active_broker == "kite":
            return await self._kite_place_order(symbol, side, quantity, order_type, price)
        elif self.active_broker == "alpaca":
            return await self._alpaca_place_order(symbol, side, quantity, order_type, price)
        elif self.active_broker == "ccxt":
            return await self._ccxt_place_order(symbol, side, quantity, order_type, price)
        else:
            return await self._paper_place_order(symbol, side, quantity, order_type, price, stop_loss, take_profit)

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        if self.active_broker == "upstox":
            return await self._upstox_get_positions()
        elif self.active_broker == "kite":
            return await self._kite_get_positions()
        elif self.active_broker == "alpaca":
            return await self._alpaca_get_positions()
        else:
            return self._paper_get_positions()

    async def modify_position(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        add_quantity: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Modify an open position."""
        symbol = symbol.upper()
        if self.active_broker == "paper":
            return self._paper_modify_position(symbol, stop_loss, take_profit, add_quantity)
        # Real broker implementation would go here
        return self._paper_modify_position(symbol, stop_loss, take_profit, add_quantity)

    async def close_position(
        self,
        symbol: str,
        quantity: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Close a position (full or partial)."""
        symbol = symbol.upper()
        if self.active_broker == "paper":
            return self._paper_close_position(symbol, quantity)
        # Real broker implementation would go here
        return self._paper_close_position(symbol, quantity)

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Check order status."""
        return self._paper_orders.get(order_id, {"status": "UNKNOWN"})

    async def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Get all pending orders."""
        return [o for o in self._paper_orders.values() if o["status"] == "PENDING"]

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a pending order."""
        if order_id in self._paper_orders:
            self._paper_orders[order_id]["status"] = "CANCELLED"
            return {"status": "CANCELLED", "order_id": order_id}
        return {"status": "NOT_FOUND"}

    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        return {"balance": self._paper_balance, "currency": "INR"}

    # ─── Paper Trading Engine ───

    async def _paper_place_order(
        self, symbol, side, quantity, order_type, price, stop_loss, take_profit,
    ) -> Dict[str, Any]:
        """Paper trading — simulated order execution."""
        from app.services.market_data_service import market_data_service

        # Get current price
        live = await market_data_service.get_live_price(symbol)
        fill_price = price if order_type == "LIMIT" and price else live.get("price", 100.0)

        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        cost = fill_price * quantity

        if side == "BUY" and cost > self._paper_balance:
            return {"status": "REJECTED", "reason": "Insufficient paper balance"}

        # Execute
        if side == "BUY":
            self._paper_balance -= cost
            if symbol in self._paper_positions:
                pos = self._paper_positions[symbol]
                total_qty = pos["quantity"] + quantity
                pos["avg_entry_price"] = (
                    (pos["avg_entry_price"] * pos["quantity"] + fill_price * quantity) / total_qty
                )
                pos["quantity"] = total_qty
            else:
                self._paper_positions[symbol] = {
                    "symbol": symbol,
                    "side": "BUY",
                    "quantity": quantity,
                    "avg_entry_price": fill_price,
                    "current_price": fill_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
        elif side == "SELL":
            self._paper_balance += cost
            if symbol in self._paper_positions:
                pos = self._paper_positions[symbol]
                pos["quantity"] -= quantity
                if pos["quantity"] <= 0:
                    del self._paper_positions[symbol]

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "fill_price": fill_price,
            "status": "FILLED",
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        self._paper_orders[order_id] = order

        logger.info("Paper trade: %s %s %s @ %.2f (balance: %.2f)", side, quantity, symbol, fill_price, self._paper_balance)
        return order

    def _paper_get_positions(self) -> List[Dict[str, Any]]:
        return list(self._paper_positions.values())

    def _paper_modify_position(self, symbol, stop_loss, take_profit, add_quantity):
        if symbol not in self._paper_positions:
            raise ValueError(f"No position for {symbol}")
        pos = self._paper_positions[symbol]
        if stop_loss is not None:
            pos["stop_loss"] = stop_loss
        if take_profit is not None:
            pos["take_profit"] = take_profit
        if add_quantity:
            pos["quantity"] += add_quantity
        return pos

    def _paper_close_position(self, symbol, quantity):
        if symbol not in self._paper_positions:
            raise ValueError(f"No position for {symbol}")
        pos = self._paper_positions[symbol]
        close_qty = quantity or pos["quantity"]
        entry = pos["avg_entry_price"]
        exit_price = pos.get("current_price", entry)

        pnl = (exit_price - entry) * close_qty if pos["side"] == "BUY" else (entry - exit_price) * close_qty
        pnl_pct = (pnl / (entry * close_qty)) * 100 if entry else 0

        self._paper_balance += exit_price * close_qty
        remaining = pos["quantity"] - close_qty

        if remaining <= 0:
            del self._paper_positions[symbol]
        else:
            pos["quantity"] = remaining

        return {
            "trade_id": f"CLOSE-{uuid.uuid4().hex[:8]}",
            "symbol": symbol,
            "exit_price": exit_price,
            "quantity": close_qty,
            "remaining_quantity": max(remaining, 0),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "duration": "N/A",
        }

    # ─── Zerodha Kite ───

    async def _kite_place_order(self, symbol, side, quantity, order_type, price) -> Dict:
        """Zerodha Kite order placement."""
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=settings.KITE_API_KEY)
            kite.set_access_token(settings.KITE_ACCESS_TOKEN)

            kite_side = kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL
            kite_order = kite.ORDER_TYPE_MARKET if order_type == "MARKET" else kite.ORDER_TYPE_LIMIT

            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=kite_side,
                quantity=int(quantity),
                order_type=kite_order,
                price=price,
                product=kite.PRODUCT_CNC,
            )

            return {
                "order_id": str(order_id),
                "status": "PLACED",
                "fill_price": price or 0,
            }
        except Exception as e:
            logger.error("Kite order failed: %s", e)
            return {"status": "REJECTED", "reason": str(e)}

    async def _kite_get_positions(self) -> List[Dict]:
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=settings.KITE_API_KEY)
            kite.set_access_token(settings.KITE_ACCESS_TOKEN)
            positions = kite.positions()
            return [
                {
                    "symbol": p["tradingsymbol"],
                    "side": "BUY" if p["quantity"] > 0 else "SELL",
                    "quantity": abs(p["quantity"]),
                    "avg_entry_price": p["average_price"],
                    "current_price": p["last_price"],
                    "unrealized_pnl": p["pnl"],
                }
                for p in positions.get("net", [])
                if p["quantity"] != 0
            ]
        except Exception as e:
            logger.error("Kite positions failed: %s", e)
            return self._paper_get_positions()

    # ─── Alpaca ───

    async def _alpaca_place_order(self, symbol, side, quantity, order_type, price) -> Dict:
        """Alpaca order placement (US markets)."""
        try:
            import httpx
            headers = {
                "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.ALPACA_BASE_URL}/v2/orders",
                    headers=headers,
                    json={
                        "symbol": symbol,
                        "qty": str(quantity),
                        "side": side.lower(),
                        "type": order_type.lower(),
                        "time_in_force": "day",
                        "limit_price": str(price) if price else None,
                    },
                )
                data = resp.json()
                return {
                    "order_id": data.get("id", ""),
                    "status": data.get("status", "PLACED").upper(),
                    "fill_price": float(data.get("filled_avg_price", 0) or 0),
                }
        except Exception as e:
            logger.error("Alpaca order failed: %s", e)
            return {"status": "REJECTED", "reason": str(e)}

    async def _alpaca_get_positions(self) -> List[Dict]:
        try:
            import httpx
            headers = {
                "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{settings.ALPACA_BASE_URL}/v2/positions", headers=headers)
                positions = resp.json()
                return [
                    {
                        "symbol": p["symbol"],
                        "side": p["side"].upper(),
                        "quantity": float(p["qty"]),
                        "avg_entry_price": float(p["avg_entry_price"]),
                        "current_price": float(p["current_price"]),
                        "unrealized_pnl": float(p["unrealized_pl"]),
                    }
                    for p in positions
                ]
        except Exception as e:
            logger.error("Alpaca positions failed: %s", e)
            return []

    # ─── Upstox (NSE — primary Indian broker) ───

    async def _upstox_place_order(self, symbol, side, quantity, order_type, price) -> Dict:
        """Upstox order placement for NSE."""
        try:
            from app.services.live_feed import NSE_INSTRUMENTS
            instrument_key = NSE_INSTRUMENTS.get(symbol, f"NSE_EQ|{symbol}")

            headers = {
                "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            upstox_side = "BUY" if side == "BUY" else "SELL"
            upstox_order_type = {
                "MARKET": "MARKET",
                "LIMIT": "LIMIT",
                "STOP_LOSS": "SL",
                "STOP_LIMIT": "SL",
            }.get(order_type, "MARKET")

            body = {
                "quantity": int(quantity),
                "product": "I",  # Intraday
                "validity": "DAY",
                "price": price or 0,
                "tag": "c365-trading-bot",
                "instrument_token": instrument_key,
                "order_type": upstox_order_type,
                "transaction_type": upstox_side,
                "disclosed_quantity": 0,
                "trigger_price": 0,
                "is_amo": False,
            }

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.upstox.com/v2/order/place",
                    headers=headers,
                    json=body,
                )
                data = resp.json()

                if resp.status_code == 200 and data.get("status") == "success":
                    order_id = data.get("data", {}).get("order_id", "")
                    return {
                        "order_id": order_id,
                        "status": "PLACED",
                        "fill_price": price or 0,
                    }
                else:
                    reason = data.get("errors", [{}])[0].get("message", str(data))
                    return {"status": "REJECTED", "reason": reason}

        except Exception as e:
            logger.error("Upstox order failed: %s", e)
            return {"status": "REJECTED", "reason": str(e)}

    async def _upstox_get_positions(self) -> List[Dict]:
        """Get positions from Upstox."""
        try:
            headers = {
                "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.upstox.com/v2/portfolio/short-term-positions",
                    headers=headers,
                )
                data = resp.json()
                positions = data.get("data", [])

                return [
                    {
                        "symbol": p.get("tradingsymbol", p.get("trading_symbol", "")),
                        "side": "BUY" if p.get("quantity", 0) > 0 else "SELL",
                        "quantity": abs(p.get("quantity", 0)),
                        "avg_entry_price": float(p.get("average_price", 0)),
                        "current_price": float(p.get("last_price", 0)),
                        "unrealized_pnl": float(p.get("pnl", 0)),
                    }
                    for p in positions
                    if p.get("quantity", 0) != 0
                ]
        except Exception as e:
            logger.error("Upstox positions failed: %s", e)
            return self._paper_get_positions()

    # ─── CCXT (Crypto) ───

    async def _ccxt_place_order(self, symbol, side, quantity, order_type, price) -> Dict:
        """CCXT order placement (crypto)."""
        try:
            import ccxt
            exchange_class = getattr(ccxt, settings.CCXT_EXCHANGE)
            exchange = exchange_class({
                "apiKey": settings.CCXT_API_KEY,
                "secret": settings.CCXT_API_SECRET,
            })
            order = exchange.create_order(
                symbol=symbol,
                type=order_type.lower(),
                side=side.lower(),
                amount=quantity,
                price=price,
            )
            return {
                "order_id": order.get("id", ""),
                "status": order.get("status", "PLACED").upper(),
                "fill_price": float(order.get("average", 0) or 0),
            }
        except Exception as e:
            logger.error("CCXT order failed: %s", e)
            return {"status": "REJECTED", "reason": str(e)}


# Singleton
broker_service = BrokerService()
