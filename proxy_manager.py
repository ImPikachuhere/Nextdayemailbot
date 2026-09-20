"""
Proxy Manager - Complete auto-refresh system
Runs continuously, refreshes proxies every 30 minutes
"""

import asyncio
import threading
import time
import os
import logging
from datetime import datetime, timedelta
from proxy_fetcher import ProxyFetcher
from proxy_checker import ProxyChecker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProxyManager:
    def __init__(self):
        self.fetcher = ProxyFetcher()
        self.checker = ProxyChecker()
        self.working_proxies = []
        self.last_refresh = None
        self.refresh_interval = 1800  # 30 minutes
        self.min_proxies = 5
        self.is_running = False
        self._lock = threading.Lock()
        
        # Load existing proxies on startup
        self._load_existing()
    
    def _load_existing(self):
        """Load existing working proxies if available"""
        try:
            if os.path.exists("proxies/working_proxies.txt"):
                with open("proxies/working_proxies.txt", 'r') as f:
                    self.working_proxies = [p.strip() for p in f if p.strip()]
                logger.info(f"Loaded {len(self.working_proxies)} existing proxies")
        except Exception as e:
            logger.warning(f"Could not load existing proxies: {e}")
    
    def get_proxies(self) -> list:
        """Get current working proxies (thread-safe)"""
        with self._lock:
            return self.working_proxies.copy()
    
    def get_random_proxy(self) -> str:
        """Get random working proxy"""
        import random
        proxies = self.get_proxies()
        if proxies:
            return random.choice(proxies)
        return None
    
    def refresh_proxies(self):
        """Manual refresh - fetch and verify new proxies"""
        logger.info("=" * 50)
        logger.info("Starting proxy refresh...")
        
        try:
            # Step 1: Fetch from all sources
            logger.info("Step 1: Fetching from all sources...")
            all_proxies = self.fetcher.fetch_all(total_limit=150)
            
            if len(all_proxies) < 10:
                logger.warning("Too few proxies fetched, keeping old ones")
                return False
            
            # Step 2: Verify which work
            logger.info("Step 2: Checking proxy health...")
            working = self.checker.get_working_proxies(all_proxies, min_working=self.min_proxies)
            
            if len(working) < self.min_proxies:
                logger.warning(f"Only {len(working)} working proxies found, keeping old ones")
                return False
            
            # Step 3: Update working list
            with self._lock:
                self.working_proxies = working
                self.last_refresh = datetime.now()
            
            # Step 4: Save to file
            self.checker.save_working(working)
            
            logger.info(f"✅ Refresh complete! {len(working)} working proxies")
            return True
            
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
            return False
    
    def _auto_refresh_loop(self):
        """Background thread for auto-refresh"""
        logger.info("Auto-refresh thread started")
        
        # Initial refresh
        self.refresh_proxies()
        
        while self.is_running:
            time.sleep(60)  # Check every minute
            
            # Check if refresh needed
            if self.last_refresh:
                elapsed = (datetime.now() - self.last_refresh).total_seconds()
                if elapsed < self.refresh_interval:
                    continue
            
            # Also refresh if running low
            current_count = len(self.get_proxies())
            if current_count < self.min_proxies:
                logger.info(f"Running low on proxies ({current_count}), refreshing...")
                self.refresh_proxies()
            elif self.last_refresh and elapsed >= self.refresh_interval:
                logger.info("Scheduled refresh due...")
                self.refresh_proxies()
    
    def start_auto_refresh(self):
        """Start background auto-refresh thread"""
        if self.is_running:
            logger.warning("Auto-refresh already running")
            return
        
        self.is_running = True
        self._thread = threading.Thread(target=self._auto_refresh_loop, daemon=True)
        self._thread.start()
        logger.info("Auto-refresh started")
    
    def stop_auto_refresh(self):
        """Stop auto-refresh"""
        self.is_running = False
        logger.info("Auto-refresh stopped")
    
    def force_refresh(self) -> dict:
        """Force immediate refresh, return status"""
        success = self.refresh_proxies()
        return {
            'success': success,
            'proxies_count': len(self.get_proxies()),
            'last_refresh': self.last_refresh.isoformat() if self.last_refresh else None
        }
    
    def get_status(self) -> dict:
        """Get current status"""
        return {
            'working_proxies': len(self.get_proxies()),
            'last_refresh': self.last_refresh.isoformat() if self.last_refresh else None,
            'next_refresh': (self.last_refresh + timedelta(seconds=self.refresh_interval)).isoformat() if self.last_refresh else None,
            'auto_refresh': self.is_running
        }


# Global instance
_proxy_manager = None

def get_proxy_manager():
    """Get singleton proxy manager instance"""
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager()
    return _proxy_manager


# Standalone run
if __name__ == "__main__":
    manager = ProxyManager()
    
    # Single refresh
    print("Running single refresh...")
    manager.refresh_proxies()
    
    # Start auto-refresh
    print("\nStarting auto-refresh (Ctrl+C to stop)...")
    manager.start_auto_refresh()
    
    try:
        while True:
            time.sleep(10)
            status = manager.get_status()
            print(f"\rProxies: {status['working_proxies']} | Last refresh: {status['last_refresh'][:19] if status['last_refresh'] else 'Never'}", end='')
    except KeyboardInterrupt:
        print("\n\nStopping...")
        manager.stop_auto_refresh()
