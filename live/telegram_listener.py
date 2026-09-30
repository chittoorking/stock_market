"""
Telegram Channel Listener — Reads advisory signals from Telegram channels
and forwards them to the ARISE army proxy.

Channels:
- Advisory on Upstox (equity trade alerts)
- Add more channels as needed

Runs 24/7 on GCP. No phone dependency.
"""
import re
import json
import logging
import asyncio
import requests
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events

# Telegram API credentials
API_ID = 22684095
API_HASH = 'f070dbb3588f6af1db3818145363a9a4'
SESSION_FILE = '/home/ai18developer/news-trading/data/telegram_session'

# ARISE proxy
PROXY_URL = 'http://localhost:8900/api/notify'

# Channels to monitor (add channel usernames or IDs here)
CHANNELS = [
    'Advisory on Upstox',  # will be resolved to ID on first run
]

# Logging
LOG_DIR = Path('/home/ai18developer/news-trading/live/logs')
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'telegram_listener.log'),
    ]
)
log = logging.getLogger('telegram')


def parse_upstox_signal(text):
    """Parse Upstox advisory signal format.

    Example:
    BUY HTEL for ₹73.8
    Upside potential: 10.0%
    Target: ₹81.17 | Stop Loss: ₹66.42
    """
    result = {}

    # Direction + Stock + Entry
    buy_match = re.search(r'(BUY|SELL)\s+(\w+)\s+(?:for|@|at)\s*₹?([\d.]+)', text, re.IGNORECASE)
    if buy_match:
        result['direction'] = buy_match.group(1).upper()
        result['stock'] = buy_match.group(2).upper()
        result['entry'] = float(buy_match.group(3))

    # Target
    target_match = re.search(r'Target[:\s]*₹?([\d.]+)', text, re.IGNORECASE)
    if target_match:
        result['target'] = float(target_match.group(1))

    # Stop Loss
    sl_match = re.search(r'Stop\s*Loss[:\s]*₹?([\d.]+)', text, re.IGNORECASE)
    if sl_match:
        result['sl'] = float(sl_match.group(1))

    # Upside potential
    upside_match = re.search(r'Upside\s*potential[:\s]*([\d.]+)%', text, re.IGNORECASE)
    if upside_match:
        result['upside'] = float(upside_match.group(1))

    return result if 'stock' in result else None


def parse_generic_signal(text):
    """Parse generic trading signal format."""
    result = {}

    # Try: BUY STOCK @ price or STOCK BUY @ price
    match = re.search(r'(BUY|SELL)\s+(\w{2,15})\s+(?:@|at|for|around)?\s*₹?\s*([\d.]+)', text, re.IGNORECASE)
    if not match:
        match = re.search(r'(\w{2,15})\s+(BUY|SELL)\s+(?:@|at|for|around)?\s*₹?\s*([\d.]+)', text, re.IGNORECASE)
        if match:
            result['stock'] = match.group(1).upper()
            result['direction'] = match.group(2).upper()
            result['entry'] = float(match.group(3))
    else:
        result['direction'] = match.group(1).upper()
        result['stock'] = match.group(2).upper()
        result['entry'] = float(match.group(3))

    # Target
    target_match = re.search(r'(?:Target|TGT|TP)[:\s]*₹?\s*([\d.]+)', text, re.IGNORECASE)
    if target_match:
        result['target'] = float(target_match.group(1))

    # Stop Loss
    sl_match = re.search(r'(?:Stop\s*Loss|SL|Stoploss)[:\s]*₹?\s*([\d.]+)', text, re.IGNORECASE)
    if sl_match:
        result['sl'] = float(sl_match.group(1))

    return result if 'stock' in result else None


def forward_to_army(signal, channel_name):
    """Forward parsed signal to ARISE proxy."""
    if not signal or 'stock' not in signal:
        return None

    # Build notification text in INDmoney format (Kaisel understands this)
    text_parts = []
    if signal.get('direction'):
        text_parts.append('Released: Equity Intraday Trade')
    text_parts.append('Stock Name: {}'.format(signal['stock']))
    if signal.get('target'):
        text_parts.append('Target: {}'.format(signal['target']))
    if signal.get('entry'):
        text_parts.append('Entry Range: {}'.format(signal['entry']))
    if signal.get('sl'):
        text_parts.append('Stop Loss: {}'.format(signal['sl']))

    notify_text = '\n'.join(text_parts)

    try:
        # Use GET (same as Android Automate)
        r = requests.get(PROXY_URL, params={'text': notify_text}, timeout=30)
        result = r.json()
        log.info('FORWARD: {} -> {} | Decision: {}'.format(
            signal['stock'], channel_name, result.get('decision', result.get('error', '?'))))
        return result
    except Exception as e:
        log.error('Forward error: {}'.format(e))
        return None


async def main():
    """Main Telegram listener."""
    log.info('Starting Telegram listener...')
    log.info('API ID: {}'.format(API_ID))
    log.info('Channels: {}'.format(CHANNELS))

    PHONE = '+918431126627'
    OTP_FILE = Path('/home/ai18developer/news-trading/data/telegram_otp.txt')

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()

    # First-time auth: provide phone, read OTP from file
    if not await client.is_user_authorized():
        await client.send_code_request(PHONE)
        log.info('OTP sent to {}. Write the OTP to: {}'.format(PHONE, OTP_FILE))
        log.info('Run: echo "12345" > {}'.format(OTP_FILE))

        # Wait for OTP file
        for i in range(60):
            if OTP_FILE.exists():
                otp = OTP_FILE.read_text().strip()
                if otp and len(otp) >= 4:
                    log.info('OTP found: {}'.format(otp))
                    try:
                        await client.sign_in(PHONE, otp)
                        log.info('Telegram login successful!')
                        OTP_FILE.unlink()  # delete OTP file
                    except Exception as e:
                        log.error('Sign in failed: {}'.format(e))
                    break
            await asyncio.sleep(2)
        else:
            log.error('OTP timeout. Write OTP to {} and restart.'.format(OTP_FILE))
            return
    else:
        log.info('Already authorized!')

    me = await client.get_me()
    log.info('Logged in as: {} {} (@{})'.format(me.first_name, me.last_name or '', me.username or ''))

    # Resolve channel entities
    channel_entities = []
    for ch_name in CHANNELS:
        try:
            # Try to find the channel by searching dialogs
            async for dialog in client.iter_dialogs():
                if ch_name.lower() in dialog.name.lower():
                    channel_entities.append(dialog.entity)
                    log.info('Found channel: {} (ID: {})'.format(dialog.name, dialog.id))
                    break
        except Exception as e:
            log.error('Error finding channel {}: {}'.format(ch_name, e))

    if not channel_entities:
        log.warning('No channels found! Make sure you have joined these channels on Telegram.')
        log.info('Listening for ALL incoming messages instead...')

    # Listen for new messages
    @client.on(events.NewMessage(chats=channel_entities if channel_entities else None))
    async def handler(event):
        text = event.raw_text
        if not text:
            return

        # Get channel/sender name
        chat = await event.get_chat()
        chat_name = getattr(chat, 'title', getattr(chat, 'username', 'unknown'))

        # Skip non-signal messages
        skip_words = ['disclaimer', 'subscribe', 'join', 'follow', 'visit', 'download']
        if any(w in text.lower() for w in skip_words) and 'buy' not in text.lower() and 'sell' not in text.lower():
            return

        # Try to parse as trading signal
        signal = parse_upstox_signal(text)
        if not signal:
            signal = parse_generic_signal(text)

        if signal:
            log.info('SIGNAL from {}: {} {} @ {} SL={} TGT={}'.format(
                chat_name,
                signal.get('direction', '?'),
                signal.get('stock', '?'),
                signal.get('entry', '?'),
                signal.get('sl', '?'),
                signal.get('target', '?'),
            ))

            # Forward to army
            result = forward_to_army(signal, chat_name)

            # Save to log
            with open(LOG_DIR / 'telegram_signals.json', 'a') as f:
                entry = {
                    'timestamp': datetime.now().isoformat(),
                    'channel': chat_name,
                    'raw_text': text[:200],
                    'parsed': signal,
                    'result': str(result)[:200] if result else None,
                }
                f.write(json.dumps(entry) + '\n')
        else:
            # Log unrecognized messages that might be signals
            if any(w in text.upper() for w in ['BUY', 'SELL', 'TARGET', 'STOP LOSS', 'ENTRY']):
                log.info('UNPARSED from {}: {}'.format(chat_name, text[:150]))

    log.info('Listening for signals... (Ctrl+C to stop)')
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
