import os

# Bot Credentials
API_ID = int(os.environ.get("API_ID", "0") or "0")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Admin List
ADMINS = os.environ.get("ADMINS", "")

# Legacy proxy support (fallback)
LEGACY_PROXIES = os.environ.get("PROXIES", "")

# Delay Configuration
MIN_DELAY = float(os.environ.get("MIN_DELAY", "15"))
MAX_DELAY = float(os.environ.get("MAX_DELAY", "45"))

# Auto-refresh settings
AUTO_REFRESH_PROXIES = os.environ.get("AUTO_REFRESH_PROXIES", "true").lower() == "true"
PROXY_REFRESH_INTERVAL = int(os.environ.get("PROXY_REFRESH_INTERVAL", "1800"))  # 30 min

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("Missing required environment variables (API_ID, API_HASH, BOT_TOKEN)")
