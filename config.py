import os

# Bot Credentials
API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Admin List (Comma-separated user IDs)
ADMINS = os.environ.get("ADMINS", "")

# Proxy Configuration (CRITICAL - Add these in Render Environment Variables)
# Format: http://user:pass@host:port,http://user2:pass2@host2:port2
PROXIES = os.environ.get("PROXIES", "").split(',') if os.environ.get("PROXIES") else []

# Delay Configuration
MIN_DELAY = float(os.environ.get("MIN_DELAY", "10"))  # Minimum seconds between checks
MAX_DELAY = float(os.environ.get("MAX_DELAY", "30"))  # Maximum seconds between checks

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("Missing required environment variables (API_ID, API_HASH, BOT_TOKEN)")
