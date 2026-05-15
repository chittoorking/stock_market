"""
Main WebSocket trade route — the primary interface for trader interaction.
WebSocket-based real-time communication with the trading agent.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.orchestration.trading_crew import process_message
from app.core.conversation_state import get_state, reset_state
from app.core.channel_output import register_channel, unregister_channel, ChannelType
from app.core.session_context import current_session_id, current_trader_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Active WebSocket connections
_connections: dict[str, WebSocket] = {}


@router.websocket("/trade/{session_id}")
async def trade_websocket(
    websocket: WebSocket,
    session_id: str,
    trader_id: Optional[str] = Query(None),
    channel: Optional[str] = Query("web"),
):
    """
    Main trading WebSocket endpoint.
    Lifecycle: connect → auth → message loop → disconnect.
    """
    await websocket.accept()
    _connections[session_id] = websocket

    # Set context vars
    current_session_id.set(session_id)
    current_trader_id.set(trader_id or f"trader-{uuid.uuid4().hex[:8]}")

    # Register channel
    ch = ChannelType(channel) if channel in ChannelType.__members__.values() else ChannelType.WEB
    register_channel(session_id, ch)

    logger.info("Trade session connected: %s (trader=%s, channel=%s)", session_id, trader_id, channel)

    # Send welcome
    state = await get_state(session_id)
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "state": state.value,
        "message": (
            "Welcome! I'm Arjun, your AI trading agent. "
            "I can scan markets, analyze signals, execute trades, and manage your portfolio. "
            "What would you like to do?"
        ),
    })

    # Conversation history for this session
    conversation_history = []

    try:
        while True:
            data = await websocket.receive_text()

            try:
                payload = json.loads(data)
                message = payload.get("message", data)
            except json.JSONDecodeError:
                message = data

            if not message or not message.strip():
                continue

            logger.info("Trader message [%s]: %s", session_id, message[:200])

            # Add to conversation history
            conversation_history.append({"role": "user", "content": message})

            # Process through the trading crew
            result = await process_message(
                session_id=session_id,
                message=message,
                conversation_history=conversation_history[-20:],  # Keep last 20 turns
            )

            # Add assistant response to history
            conversation_history.append({"role": "assistant", "content": result["response"]})

            # Send response
            await websocket.send_json({
                "type": "response",
                "message": result["response"],
                "tool_used": result.get("tool_used"),
                "state": result.get("state"),
                "quick_replies": result.get("quick_replies", []),
                "intent": result.get("intent"),
            })

    except WebSocketDisconnect:
        logger.info("Trade session disconnected: %s", session_id)
    except Exception as e:
        logger.error("Trade WebSocket error [%s]: %s", session_id, e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _connections.pop(session_id, None)
        unregister_channel(session_id)


@router.post("/trade/{session_id}/message")
async def trade_http(session_id: str, message: str):
    """
    HTTP fallback for environments where WebSocket isn't available.
    Stateless request-response for REST API clients.
    """
    register_channel(session_id, ChannelType.WEB)

    result = await process_message(
        session_id=session_id,
        message=message,
    )

    return {
        "response": result["response"],
        "tool_used": result.get("tool_used"),
        "state": result.get("state"),
        "quick_replies": result.get("quick_replies", []),
    }
