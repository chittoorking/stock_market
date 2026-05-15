"""
SSE streaming endpoint — for web dashboard real-time updates.
SSE streaming for real-time dashboard updates.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.agui_events.emit import drain_events

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stream/{session_id}")
async def event_stream(session_id: str):
    """
    Server-Sent Events stream for real-time dashboard updates.
    Pushes: signals, trade executions, position updates, risk alerts.
    """
    async def generate():
        while True:
            events = drain_events(session_id)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"

            await asyncio.sleep(0.5)  # Poll interval

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
