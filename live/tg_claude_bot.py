"""Telegram Claude Bot — chat with Claude from mobile, Claude can run server commands.

Send any message → Claude reads it, can run bash commands, responds with results.
Predefined shortcuts:
  /status — bot status, balance, positions
  /trades — today's trades
  /restart — restart trading bot
  /refresh — refresh INDmoney token
  /logs — last 20 log lines
"""
import os
import json
import time
import subprocess
import threading
import requests
import anthropic

TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'
TG_API = f'https://api.telegram.org/bot{TG_TOKEN}'
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

SYSTEM_PROMPT = """You are a trading bot assistant running on a GCP server.
You help the user check trade status, debug issues, and manage the trading system.
You can run bash commands using the bash tool.

Key paths:
- Trading bot: /home/ai18developer/news-trading/trading_v2/
- Audit logs: /home/ai18developer/news-trading/live/logs/audit_*.jsonl
- System log: /home/ai18developer/news-trading/trading_v2/logs/system.log
- INDmoney client: /home/ai18developer/news-trading/live/indmoney_client.py
- Refresh token: /home/ai18developer/news-trading/live/refresh_token.sh

Keep responses SHORT (under 500 chars) — this is Telegram on mobile.
Use simple formatting. No markdown tables."""

def run_cmd(cmd, timeout=15):
    """Run a bash command and return output."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()[:2000]
    except subprocess.TimeoutExpired:
        return "Command timed out"
    except Exception as e:
        return f"Error: {e}"

def handle_shortcut(text):
    """Handle predefined shortcuts without calling Claude."""
    text = text.strip().lower()
    
    if text == '/status':
        bot = run_cmd("pgrep -f trading.main && echo 'Bot: RUNNING' || echo 'Bot: DOWN'")
        bal = run_cmd("cd /home/ai18developer/news-trading && python3 -c \"from live.indmoney_client import get_funds; b=get_funds(); print(f'Balance: Rs {round(b):,}' if b else 'Balance: FAILED')\" 2>&1")
        pos = run_cmd("cat /home/ai18developer/news-trading/trading_v2/logs/positions.json 2>/dev/null")
        return f"{bot}\n{bal}\nPositions: {pos}"
    
    if text == '/trades':
        today = time.strftime('%Y%m%d')
        trades = run_cmd(f"grep -E 'ENTRY|CLOSE|EXIT' /home/ai18developer/news-trading/live/logs/audit_{today}.jsonl 2>/dev/null | grep -E 'anomaly|multyfi|positions' | tail -10")
        return trades or "No trades today"
    
    if text == '/restart':
        run_cmd("kill $(pgrep -f trading.main) 2>/dev/null; sleep 2; cd /home/ai18developer/news-trading/trading_v2 && nohup /home/ai18developer/news-trading/venv/bin/python3 -u -m trading.main > logs/system.log 2>&1 &")
        time.sleep(3)
        check = run_cmd("pgrep -f trading.main && echo 'Bot RESTARTED' || echo 'FAILED'")
        return check
    
    if text == '/refresh':
        result = run_cmd("bash /home/ai18developer/news-trading/live/refresh_token.sh 2>&1", timeout=30)
        return f"Token refresh:\n{result[:500]}"
    
    if text == '/logs':
        logs = run_cmd("tail -20 /home/ai18developer/news-trading/trading_v2/logs/system.log | grep -v 'InsecureRequest\|urllib3\|warnings'")
        return logs or "No recent logs"
    
    return None

def ask_claude(user_msg):
    """Send message to Claude with bash tool."""
    tools = [{
        "name": "bash",
        "description": "Run a bash command on the trading server",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Bash command to run"}},
            "required": ["command"]
        }
    }]
    
    messages = [{"role": "user", "content": user_msg}]
    
    # Loop for tool use
    for _ in range(5):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        
        # Check if Claude wants to use a tool
        tool_use = None
        text_parts = []
        for block in response.content:
            if block.type == "tool_use":
                tool_use = block
            elif block.type == "text":
                text_parts.append(block.text)
        
        if tool_use:
            cmd = tool_use.input.get("command", "")
            result = run_cmd(cmd)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use.id, "content": result}
            ]})
        else:
            return "\n".join(text_parts)[:3000]
    
    return "\n".join(text_parts)[:3000] if text_parts else "No response"

def send_tg(text):
    """Send message to Telegram."""
    # Split long messages
    for i in range(0, len(text), 4000):
        chunk = text[i:i+4000]
        requests.post(f'{TG_API}/sendMessage',
                      json={'chat_id': TG_CHAT, 'text': chunk}, timeout=10)

def poll():
    """Poll Telegram for new messages."""
    offset = 0
    print("Telegram Claude Bot started. Listening...")
    
    while True:
        try:
            r = requests.get(f'{TG_API}/getUpdates',
                           params={'offset': offset, 'timeout': 30}, timeout=35)
            updates = r.json().get('result', [])
            
            for update in updates:
                offset = update['update_id'] + 1
                msg = update.get('message', {})
                chat_id = str(msg.get('chat', {}).get('id', ''))
                text = msg.get('text', '')
                
                if chat_id != TG_CHAT or not text:
                    continue
                
                print(f"Received: {text}")
                
                # Try shortcut first
                shortcut_response = handle_shortcut(text)
                if shortcut_response:
                    send_tg(shortcut_response)
                    continue
                
                # Otherwise ask Claude
                send_tg("Thinking...")
                try:
                    response = ask_claude(text)
                    send_tg(response)
                except Exception as e:
                    send_tg(f"Claude error: {e}")
                    
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)

if __name__ == '__main__':
    poll()
