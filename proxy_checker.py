"""
Proxy Checker - Verifies which proxies actually work
Tests proxies against multiple test URLs
"""

import asyncio
import aiohttp
import aiohttp_socks
import time
from typing import List, Tuple, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProxyChecker:
    def __init__(self):
        self.test_urls = [
            "http://httpbin.org/ip",
            "http://httpbin.org/get",
            "https://httpbin.org/ip",
        ]
        self.timeout = aiohttp.ClientTimeout(total=15, connect=10)
    
    async def check_proxy(self, proxy: str) -> Tuple[str, bool, Dict]:
        """
        Check if proxy works
        Returns: (proxy, is_working, details)
        """
        try:
            # Parse proxy
            connector = aiohttp_socks.ProxyConnector.from_url(proxy)
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout
            ) as session:
                
                start_time = time.time()
                test_url = random.choice(self.test_urls)
                
                async with session.get(test_url) as response:
                    response_time = time.time() - start_time
                    
                    if response.status == 200:
                        try:
                            data = await response.json()
                            origin_ip = data.get('origin', 'unknown')
                            
                            return (proxy, True, {
                                'response_time': round(response_time, 2),
                                'status': response.status,
                                'origin_ip': origin_ip,
                                'anonymity': 'elite' if origin_ip != 'unknown' else 'unknown'
                            })
                        except:
                            return (proxy, True, {
                                'response_time': round(response_time, 2),
                                'status': response.status
                            })
                    else:
                        return (proxy, False, {'error': f'HTTP {response.status}'})
                        
        except asyncio.TimeoutError:
            return (proxy, False, {'error': 'Timeout'})
        except Exception as e:
            return (proxy, False, {'error': str(e)})
    
    async def check_batch(self, proxies: List[str], max_concurrent: int = 20) -> List[Tuple]:
        """Check multiple proxies concurrently"""
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def check_with_limit(proxy):
            async with semaphore:
                # Small delay to avoid overwhelming
                await asyncio.sleep(random.uniform(0.1, 0.5))
                return await self.check_proxy(proxy)
        
        tasks = [check_with_limit(p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions
        valid_results = []
        for r in results:
            if isinstance(r, Exception):
                continue
            valid_results.append(r)
        
        return valid_results
    
    def get_working_proxies(self, proxies: List[str], min_working: int = 5) -> List[str]:
        """Get list of working proxies"""
        results = asyncio.run(self.check_batch(proxies))
        
        working = []
        failed = []
        
        for proxy, is_working, details in results:
            if is_working:
                working.append((proxy, details))
                logger.info(f"✅ Working: {proxy} ({details.get('response_time', '?')}s)")
            else:
                failed.append((proxy, details))
                logger.debug(f"❌ Failed: {proxy} - {details.get('error', 'Unknown')}")
        
        # Sort by response time
        working.sort(key=lambda x: x[1].get('response_time', 999))
        
        # Take top proxies
        top_working = [p[0] for p in working[:min_working * 3]]  # Get 3x for rotation
        
        logger.info(f"Found {len(top_working)} working proxies out of {len(proxies)}")
        
        return top_working
    
    def save_working(self, proxies: List[str], filename: str = "proxies/working_proxies.txt"):
        """Save working proxies"""
        import os
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        
        with open(filename, 'w') as f:
            for proxy in proxies:
                f.write(f"{proxy}\n")
        
        logger.info(f"Saved {len(proxies)} working proxies to {filename}")


# Test
if __name__ == "__main__":
    checker = ProxyChecker()
    
    # Read proxies from file
    try:
        with open("proxies/all_proxies.txt", 'r') as f:
            proxies = [p.strip() for p in f if p.strip()]
        
        working = checker.get_working_proxies(proxies, min_working=10)
        checker.save_working(working)
        print(f"Working proxies: {len(working)}")
    except FileNotFoundError:
        print("Run proxy_fetcher.py first!")
