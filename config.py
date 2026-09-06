import os

# Bot Credentials
API_ID = int(os.environ.get("API_ID", ""))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Admin List (Comma-separated user IDs)
# Example: "123456789,987654321"
ADMINS = os.environ.get("ADMINS", "")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("Missing required environment variables (API_ID, API_HASH, BOT_TOKEN)")
