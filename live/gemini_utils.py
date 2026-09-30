"""Shared Gemini utilities — retry, dual key fallback, proper error handling."""
import os
import time
import logging
import requests

log = logging.getLogger('gemini')

# Load both keys
_KEY1 = os.environ.get('GEMINI_API_KEY', '')
_KEY2 = os.environ.get('GEMINI_API_KEY_2', '')
_KEYS = [k for k in [_KEY1, _KEY2] if k]
_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'
_MODEL = 'gemini-3.6-flash'


def call_gemini(prompt, grounding=True, max_retries=3, max_tokens=65536):
    """Call Gemini with retry + dual key fallback.

    Returns response text or None. Never silently fails — always logs errors.
    """
    tools = [{'google_search': {}}] if grounding else []

    if not _KEYS:
        log.error("No GEMINI_API_KEY configured")
        return None

    for key_idx, key in enumerate(_KEYS):
        url = f'{_BASE}/{_MODEL}:generateContent?key={key}'
        key_label = f"key{key_idx+1}"

        for attempt in range(max_retries):
            try:
                resp = requests.post(url,
                    json={
                        'contents': [{'parts': [{'text': prompt}]}],
                        'tools': tools,
                        'generationConfig': {'temperature': 0, 'maxOutputTokens': max_tokens},
                    }, timeout=180)

                if resp.status_code == 200:
                    text = ''.join(
                        p.get('text', '')
                        for p in resp.json()['candidates'][0]['content']['parts']
                    )
                    if text:
                        return text
                    log.warning(f"Gemini ({key_label}) returned empty text")
                    return None

                elif resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    log.warning(f"Gemini ({key_label}) rate limited (429), attempt {attempt+1}/{max_retries}, waiting {wait}s")
                    time.sleep(wait)
                    continue  # retry same key

                elif resp.status_code == 403:
                    log.error(f"Gemini ({key_label}) forbidden (403): {resp.text[:100]}")
                    break  # try next key

                else:
                    log.error(f"Gemini ({key_label}) error {resp.status_code}: {resp.text[:200]}")
                    break  # try next key

            except requests.exceptions.Timeout:
                log.error(f"Gemini ({key_label}) timeout, attempt {attempt+1}/{max_retries}")
                continue  # retry
            except Exception as e:
                log.error(f"Gemini ({key_label}) exception: {e}")
                break  # try next key

        # If we exhausted retries on this key, try next key
        if key_idx < len(_KEYS) - 1:
            log.info(f"Switching to {f'key{key_idx+2}'}...")

    log.error("All Gemini keys exhausted — no response")
    return None


def reload_keys():
    """Reload keys from environment (call after dotenv refresh)."""
    global _KEYS, _KEY1, _KEY2
    _KEY1 = os.environ.get('GEMINI_API_KEY', '')
    _KEY2 = os.environ.get('GEMINI_API_KEY_2', '')
    _KEYS = [k for k in [_KEY1, _KEY2] if k]
    log.info(f"Gemini keys loaded: {len(_KEYS)} keys available")
