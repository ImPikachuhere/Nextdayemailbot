"""
Free Proxy Fetcher - Multiple Sources
Auto-fetches fresh proxies every 30 minutes
"""

import requests
import re
import json
import random
from typing import List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProxyFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0'
        })
        self.working_proxies = []
    
    # ============ SOURCE 1: ProxyScrape API ============
    
    def fetch_proxyscrape(self, limit=50) -> List[str]:
        """Fetch from ProxyScrape API - Most reliable"""
        urls = [
            f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all&limit={limit}",
            f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=US&ssl=yes&anonymity=elite&limit={limit//2}",
        ]
        
        proxies = []
        for url in urls:
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    lines = response.text.strip().split('\n')
                    for line in lines:
                        line = line.strip()
                        if ':' in line and self._is_valid_proxy(line):
                            proxies.append(f"http://{line}")
                logger.info(f"ProxyScrape: Got {len(proxies)} proxies")
            except Exception as e:
                logger.warning(f"ProxyScrape failed: {e}")
        
        return list(set(proxies))  # Remove duplicates
    
    # ============ SOURCE 2: FreeProxyList ============
    
    def fetch_freeproxylists(self, limit=30) -> List[str]:
        """Fetch from free-proxy-list.net"""
        try:
            url = "https://free-proxy-list.net/"
            response = self.session.get(url, timeout=15)
            
            # Extract from table using regex
            pattern = r'<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>'
            matches = re.findall(pattern, response.text)
            
            proxies = [f"http://{ip}:{port}" for ip, port in matches[:limit]]
            logger.info(f"FreeProxyList: Got {len(proxies)} proxies")
            return proxies
            
        except Exception as e:
            logger.warning(f"FreeProxyList failed: {e}")
            return []
    
    # ============ SOURCE 3: Proxy-List.download ============
    
    def fetch_proxylist_download(self, limit=30) -> List[str]:
        """Fetch from proxy-list.download"""
        try:
            url = "https://www.proxy-list.download/api/v1/get?type=http"
            response = self.session.get(url, timeout=15)
            
            proxies = []
            for line in response.text.strip().split('\n')[:limit]:
                line = line.strip()
                if ':' in line:
                    proxies.append(f"http://{line}")
            
            logger.info(f"ProxyListDownload: Got {len(proxies)} proxies")
            return proxies
            
        except Exception as e:
            logger.warning(f"ProxyListDownload failed: {e}")
            return []
    
    # ============ SOURCE 4: GitHub Proxy Lists ============
    
    def fetch_github_proxies(self, limit=30) -> List[str]:
        """Fetch from GitHub proxy lists (community maintained)"""
        urls = [
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        ]
        
        proxies = []
        for url in urls:
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    for line in response.text.strip().split('\n')[:limit//3]:
                        line = line.strip()
                        if ':' in line and not line.startswith('#'):
                            # Remove auth info if present
                            proxy = line.split('@')[-1] if '@' in line else line
                            if not proxy.startswith('http'):
                                proxy = f"http://{proxy}"
                            proxies.append(proxy)
            except Exception as e:
                logger.warning(f"GitHub source failed: {e}")
        
        logger.info(f"GitHub sources: Got {len(proxies)} proxies")
        return list(set(proxies))
    
    # ============ SOURCE 5: GeoNode ============
    
    def fetch_geonode(self, limit=20) -> List[str]:
        """Fetch from GeoNode free API"""
        try:
            url = f"https://proxylist.geonode.com/api/proxy-list?limit={limit}&page=1&sort_by=lastChecked&sort_type=desc&protocols=http"
            response = self.session.get(url, timeout=15)
            data = response.json()
            
            proxies = []
            for item in data.get('data', []):
                ip = item.get('ip')
                port = item.get('port')
                if ip and port:
                    proxies.append(f"http://{ip}:{port}")
            
            logger.info(f"GeoNode: Got {len(proxies)} proxies")
            return proxies
            
        except Exception as e:
            logger.warning(f"GeoNode failed: {e}")
            return []
    
    # ============ HELPER METHODS ============
    
    def _is_valid_proxy(self, proxy: str) -> bool:
        """Basic validation"""
        parts = proxy.split(':')
        if len(parts) != 2:
            return False
        
        ip, port = parts
        # Basic IP validation
        if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
            return False
        
        # Port validation
        try:
            port_num = int(port)
            if not (1 <= port_num <= 65535):
                return False
        except:
            return False
        
        return True
    
    def fetch_all(self, total_limit=100) -> List[str]:
        """Fetch from all sources and combine"""
        all_proxies = []
        
        # Fetch from all sources
        sources = [
            self.fetch_proxyscrape(limit=40),
            self.fetch_freeproxylists(limit=20),
            self.fetch_proxylist_download(limit=20),
            self.fetch_github_proxies(limit=30),
            self.fetch_geonode(limit=20),
        ]
        
        for source_proxies in sources:
            all_proxies.extend(source_proxies)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_proxies = []
        for proxy in all_proxies:
            if proxy not in seen:
                seen.add(proxy)
                unique_proxies.append(proxy)
        
        # Shuffle for randomness
        random.shuffle(unique_proxies)
        
        final_proxies = unique_proxies[:total_limit]
        logger.info(f"Total unique proxies fetched: {len(final_proxies)}")
        
        return final_proxies
    
    def save_to_file(self, proxies: List[str], filename: str = "proxies/all_proxies.txt"):
        """Save proxies to file"""
        import os
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        
        with open(filename, 'w') as f:
            for proxy in proxies:
                f.write(f"{proxy}\n")
        
        logger.info(f"Saved {len(proxies)} proxies to {filename}")


# Test
if __name__ == "__main__":
    fetcher = ProxyFetcher()
    proxies = fetcher.fetch_all(total_limit=100)
    fetcher.save_to_file(proxies)
    print(f"Fetched {len(proxies)} proxies")
