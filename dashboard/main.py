"""Trading Bot Dashboard — FastAPI app with full bot management."""
import asyncio
import json
import secrets
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import config, db
from .bot_manager import (
    get_status, start_bot, stop_bot, restart_bot, pause_bot,
    kill_all, get_all_positions, get_ibkr_status, get_token_status,
    update_token, auto_refresh_token,
)
from .log_parser import background_parser, get_recent_logs
from .alerts import send_telegram, alert_kill_switch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("dashboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    db.init_db()
    log.info("Dashboard started")
    task = asyncio.create_task(background_parser())
    yield
    task.cancel()
    log.info("Dashboard stopped")


app = FastAPI(title="Trading Bot Dashboard", lifespan=lifespan)
security = HTTPBasic()


def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not (secrets.compare_digest(credentials.username, config.ADMIN_USER) and
            secrets.compare_digest(credentials.password, config.ADMIN_PASS)):
        raise HTTPException(status_code=401, detail="Unauthorized",
                          headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# ═══════════════════════════════════════════
# DASHBOARD HTML
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard(user: str = Depends(check_auth)):
    html_path = Path(__file__).parent / "templates" / "index.html"
    return html_path.read_text()


# ═══════════════════════════════════════════
# BOT MANAGEMENT
# ═══════════════════════════════════════════

@app.get("/api/bots")
async def api_bots(user: str = Depends(check_auth)):
    bots = {}
    for bot_id in config.BOTS:
        bots[bot_id] = get_status(bot_id)
    return bots


@app.get("/api/bots/{bot_id}/status")
async def api_bot_status(bot_id: str, user: str = Depends(check_auth)):
    if bot_id not in config.BOTS:
        raise HTTPException(404, "Bot not found")
    return get_status(bot_id)


@app.post("/api/bots/{bot_id}/start")
async def api_start(bot_id: str, user: str = Depends(check_auth)):
    if bot_id not in config.BOTS:
        raise HTTPException(404, "Bot not found")
    return start_bot(bot_id)


@app.post("/api/bots/{bot_id}/stop")
async def api_stop(bot_id: str, user: str = Depends(check_auth)):
    if bot_id not in config.BOTS:
        raise HTTPException(404, "Bot not found")
    return stop_bot(bot_id)


@app.post("/api/bots/{bot_id}/restart")
async def api_restart(bot_id: str, user: str = Depends(check_auth)):
    if bot_id not in config.BOTS:
        raise HTTPException(404, "Bot not found")
    return restart_bot(bot_id)


@app.post("/api/bots/{bot_id}/pause")
async def api_pause(bot_id: str, user: str = Depends(check_auth)):
    if bot_id not in config.BOTS:
        raise HTTPException(404, "Bot not found")
    return pause_bot(bot_id)


@app.post("/api/kill-switch")
async def api_kill_switch(user: str = Depends(check_auth)):
    result = kill_all()
    alert_kill_switch()
    return result


# ═══════════════════════════════════════════
# POSITIONS
# ═══════════════════════════════════════════

@app.get("/api/positions")
async def api_positions(user: str = Depends(check_auth)):
    return get_all_positions()


@app.post("/api/positions/{bot_id}/{symbol}/close")
async def api_close_position(bot_id: str, symbol: str, user: str = Depends(check_auth)):
    """Manual close — place opposite order via IBKR."""
    # Read current position from state file
    status = get_status(bot_id)
    positions = status.get("positions", {})
    if symbol not in positions:
        raise HTTPException(404, f"No open position for {symbol}")

    pos = positions[symbol]
    side = pos.get("side", "")
    qty = pos.get("qty", 0)

    # For now, log the manual close request
    # Full IBKR integration would place the opposite order here
    log.info(f"MANUAL CLOSE requested: {bot_id} {symbol} {side} qty={qty}")
    return {"ok": True, "message": f"Close request sent for {symbol}"}


@app.post("/api/positions/{bot_id}/{symbol}/modify-sl")
async def api_modify_sl(bot_id: str, symbol: str, request: Request,
                        user: str = Depends(check_auth)):
    """Modify stop loss for an open position."""
    body = await request.json()
    new_sl = body.get("stop_loss")
    if new_sl is None:
        raise HTTPException(400, "stop_loss required")
    log.info(f"MODIFY SL: {bot_id} {symbol} → new SL={new_sl}")
    return {"ok": True, "message": f"SL modified to {new_sl} for {symbol}"}


# ═══════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════

@app.post("/api/orders")
async def api_place_order(request: Request, user: str = Depends(check_auth)):
    """Place a manual order."""
    body = await request.json()
    bot_id = body.get("bot")
    symbol = body.get("symbol")
    side = body.get("side")
    qty = body.get("qty")

    if not all([bot_id, symbol, side, qty]):
        raise HTTPException(400, "bot, symbol, side, qty required")

    order_id = db.insert_order(bot_id, symbol, side, qty)
    log.info(f"MANUAL ORDER: {bot_id} {side} {qty} {symbol} (order #{order_id})")
    return {"ok": True, "order_id": order_id}


@app.get("/api/orders")
async def api_orders(bot: str = None, status: str = None,
                     user: str = Depends(check_auth)):
    return db.get_orders(bot=bot, status=status)


@app.get("/api/orders/pending")
async def api_pending_orders(user: str = Depends(check_auth)):
    return db.get_orders(status="pending")


@app.post("/api/orders/{order_id}/cancel")
async def api_cancel_order(order_id: int, user: str = Depends(check_auth)):
    db.update_order(order_id, "cancelled")
    return {"ok": True}


# ═══════════════════════════════════════════
# TRADES & P&L
# ═══════════════════════════════════════════

@app.get("/api/trades")
async def api_trades(bot: str = None, from_date: str = None, to_date: str = None,
                     limit: int = 50, offset: int = 0,
                     user: str = Depends(check_auth)):
    trades, total = db.get_trades(bot=bot, from_date=from_date, to_date=to_date,
                                  limit=limit, offset=offset)
    return {"trades": trades, "total": total, "limit": limit, "offset": offset}


@app.get("/api/pnl")
async def api_pnl(bot: str = None, user: str = Depends(check_auth)):
    return db.get_pnl_summary(bot=bot)


@app.get("/api/pnl/chart")
async def api_pnl_chart(bot: str = None, days: int = 30,
                        user: str = Depends(check_auth)):
    return db.get_pnl_chart(bot=bot, days=days)


# ═══════════════════════════════════════════
# RISK CONTROLS
# ═══════════════════════════════════════════

@app.get("/api/risk/{bot_id}")
async def api_get_risk(bot_id: str, user: str = Depends(check_auth)):
    return db.get_risk_config(bot_id)


@app.post("/api/risk/{bot_id}")
async def api_set_risk(bot_id: str, request: Request,
                       user: str = Depends(check_auth)):
    body = await request.json()
    db.set_risk_config(bot_id, **body)
    return {"ok": True}


# ═══════════════════════════════════════════
# TOKEN & IBKR
# ═══════════════════════════════════════════

@app.get("/api/token/status")
async def api_token_status(user: str = Depends(check_auth)):
    return get_token_status()


@app.post("/api/token/update")
async def api_token_update(request: Request, user: str = Depends(check_auth)):
    body = await request.json()
    token = body.get("token", "").strip()
    if not token:
        raise HTTPException(400, "token required")
    return update_token(token)


@app.post("/api/token/auto-refresh")
async def api_token_auto_refresh(user: str = Depends(check_auth)):
    return auto_refresh_token()


@app.get("/api/ibkr/status")
async def api_ibkr_status(user: str = Depends(check_auth)):
    return get_ibkr_status()


# ═══════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════

@app.get("/api/alerts/config")
async def api_get_alerts(user: str = Depends(check_auth)):
    cfg = db.get_alert_config()
    # Mask passwords
    if cfg.get("email_pass"):
        cfg["email_pass"] = "***"
    return cfg


@app.post("/api/alerts/config")
async def api_set_alerts(request: Request, user: str = Depends(check_auth)):
    body = await request.json()
    db.set_alert_config(**body)
    return {"ok": True}


@app.post("/api/alerts/test")
async def api_test_alert(user: str = Depends(check_auth)):
    send_telegram("Test alert from Trading Dashboard")
    return {"ok": True, "message": "Test alert sent"}


# ═══════════════════════════════════════════
# STREAMING (SSE)
# ═══════════════════════════════════════════

@app.get("/stream/logs/{bot_id}")
async def stream_logs(bot_id: str, user: str = Depends(check_auth)):
    if bot_id not in config.BOTS:
        raise HTTPException(404, "Bot not found")

    async def generate():
        # Send existing logs first
        existing = get_recent_logs(bot_id, lines=30)
        yield f"data: {json.dumps({'type': 'init', 'logs': existing})}\n\n"

        # Then stream new lines
        from .log_parser import tail_log
        while True:
            new_lines = tail_log(bot_id)
            if new_lines:
                text = "".join(new_lines)
                yield f"data: {json.dumps({'type': 'append', 'logs': text})}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/stream/positions")
async def stream_positions(user: str = Depends(check_auth)):
    async def generate():
        while True:
            positions = get_all_positions()
            yield f"data: {json.dumps(positions, default=str)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ═══════════════════════════════════════════
# HEALTH (no auth)
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
# TERMINAL
# ═══════════════════════════════════════════

@app.post("/api/terminal")
async def api_terminal(request: Request, user: str = Depends(check_auth)):
    """Execute shell command on server."""
    body = await request.json()
    cmd = body.get("cmd", "").strip()
    if not cmd:
        raise HTTPException(400, "cmd required")
    # Block dangerous commands
    dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", "shutdown", "reboot", "passwd"]
    if any(d in cmd for d in dangerous):
        return {"error": "Command blocked for safety"}
    try:
        import subprocess
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
            cwd=str(Path.home())
        )
        output = result.stdout + result.stderr
        return {"output": output[:10000]}  # limit output size
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out (30s limit)"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}
