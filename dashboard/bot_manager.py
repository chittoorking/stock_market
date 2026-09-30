"""Bot lifecycle management — start, stop, pause, status."""
import subprocess
import json
import os
import signal
import time
from pathlib import Path
from datetime import datetime
from . import config

import logging
log = logging.getLogger("dashboard")


def get_bot_pid(bot_id):
    """Get PID of running bot, or None."""
    bot = config.BOTS[bot_id]
    try:
        result = subprocess.run(
            ["pgrep", "-f", bot["pgrep"]],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p.strip()]
        return int(pids[0]) if pids else None
    except Exception:
        return None


def get_uptime(pid):
    """Get process uptime in seconds from /proc."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().split()
        boot_time = float(Path("/proc/stat").read_text().split("btime ")[1].split()[0])
        start_ticks = int(stat[21])
        hz = os.sysconf("SC_CLK_TCK")
        start_time = boot_time + start_ticks / hz
        return time.time() - start_time
    except Exception:
        return 0


def get_status(bot_id):
    """Get full status for a bot."""
    bot = config.BOTS[bot_id]
    pid = get_bot_pid(bot_id)
    paused = Path(bot["pause_file"]).exists()

    # Read state file
    state = {}
    state_file = Path(bot["state_file"])
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass

    # Read risk config
    risk = {}
    cfg_file = Path(bot["config_file"])
    if cfg_file.exists():
        try:
            risk = json.loads(cfg_file.read_text())
        except Exception:
            pass

    status = "paused" if pid and paused else "running" if pid else "stopped"

    return {
        "id": bot_id,
        "name": bot["name"],
        "market": bot["market"],
        "schedule": bot["schedule"],
        "status": status,
        "pid": pid,
        "uptime": get_uptime(pid) if pid else 0,
        "paused": paused,
        "last_cycle": state.get("last_cycle"),
        "positions": state.get("positions", {}),
        "positions_count": len(state.get("positions", {})),
        "daily_pnl": state.get("daily_pnl", 0),
        "cycle_count": state.get("cycle_count", 0),
        "risk": risk,
    }


def start_bot(bot_id):
    """Start a bot."""
    bot = config.BOTS[bot_id]
    pid = get_bot_pid(bot_id)
    if pid:
        return {"error": f"{bot['name']} already running (PID {pid})"}

    env = os.environ.copy()
    env.update(bot.get("env", {}))

    log_file = open(bot["log_file"], "a")
    proc = subprocess.Popen(
        [bot["python"], bot["script"]],
        cwd=bot["cwd"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.info(f"Started {bot['name']} (PID {proc.pid})")
    return {"ok": True, "pid": proc.pid}


def stop_bot(bot_id):
    """Stop a bot gracefully."""
    bot = config.BOTS[bot_id]
    pid = get_bot_pid(bot_id)
    if not pid:
        return {"error": f"{bot['name']} not running"}

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        # Force kill if still alive
        if get_bot_pid(bot_id):
            os.kill(pid, signal.SIGKILL)
        log.info(f"Stopped {bot['name']} (PID {pid})")
        return {"ok": True}
    except ProcessLookupError:
        return {"ok": True, "note": "already stopped"}
    except Exception as e:
        return {"error": str(e)}


def restart_bot(bot_id):
    """Stop and start a bot."""
    stop_bot(bot_id)
    time.sleep(2)
    return start_bot(bot_id)


def pause_bot(bot_id):
    """Toggle pause mode."""
    bot = config.BOTS[bot_id]
    pause_file = Path(bot["pause_file"])
    if pause_file.exists():
        pause_file.unlink()
        log.info(f"Unpaused {bot['name']}")
        return {"paused": False}
    else:
        pause_file.write_text(datetime.now().isoformat())
        log.info(f"Paused {bot['name']}")
        return {"paused": True}


def kill_all():
    """Emergency stop all bots."""
    results = {}
    for bot_id in config.BOTS:
        results[bot_id] = stop_bot(bot_id)
    log.warning("KILL SWITCH activated — all bots stopped")
    return results


def get_all_positions():
    """Get open positions across all bots."""
    all_pos = []
    for bot_id in config.BOTS:
        status = get_status(bot_id)
        positions = status.get("positions", {})
        for symbol, pos in positions.items():
            pos_data = dict(pos) if isinstance(pos, dict) else {}
            pos_data["bot"] = bot_id
            pos_data["symbol"] = symbol
            all_pos.append(pos_data)
    return all_pos


def get_ibkr_status():
    """Check IBKR Gateway Docker container status."""
    try:
        result = subprocess.run(
            ["sudo", "docker", "ps", "--filter", "name=ibkr", "--format",
             "{{.Status}}|||{{.Ports}}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            parts = result.stdout.strip().split("|||")
            return {
                "running": True,
                "status": parts[0] if parts else "unknown",
                "ports": parts[1] if len(parts) > 1 else "",
            }
        return {"running": False, "status": "not running"}
    except Exception as e:
        return {"running": False, "status": str(e)}


def get_token_status():
    """Check IndMoney token status."""
    bot = config.BOTS.get("indian", {})
    token_file = Path(bot.get("token_file", ""))
    if not token_file.exists():
        return {"valid": False, "error": "no token file"}

    token = token_file.read_text().strip()
    if not token:
        return {"valid": False, "error": "empty token"}

    # Decode JWT expiry
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.b64decode(payload))
        exp = data.get("exp", 0)
        exp_dt = datetime.fromtimestamp(exp)
        remaining = (exp_dt - datetime.now()).total_seconds()
        return {
            "valid": remaining > 0,
            "expires": exp_dt.isoformat(),
            "remaining_hours": round(remaining / 3600, 1),
            "client_id": data.get("clientID", ""),
        }
    except Exception:
        return {"valid": True, "note": "could not decode expiry"}


def update_token(token):
    """Update IndMoney token."""
    bot = config.BOTS.get("indian", {})
    token_file = Path(bot.get("token_file", ""))
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token.strip())
    return {"ok": True}


def auto_refresh_token():
    """Auto-refresh IndMoney token using TOTP."""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / "trading-bot"))
        from auto_token import refresh_token
        token = refresh_token()
        if token:
            return {"ok": True, "message": "Token auto-refreshed"}
        return {"error": "TOTP refresh failed"}
    except Exception as e:
        return {"error": str(e)}
