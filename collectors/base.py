import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from playwright.sync_api import sync_playwright, Page, Browser

from core.config import Settings
from core.logger import logger
from core.models import RawJob


class BaseScraper(ABC):
    source_name: str = "unknown"

    def __init__(self, headless: bool = None):
        self.headless = headless if headless is not None else Settings.headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    def _human_delay(self, extra: float = 0.0):
        delay = random.uniform(Settings.scrape_delay_min, Settings.scrape_delay_max) + extra
        logger.debug(f"[{self.source_name}] Sleeping {delay:.1f}s...")
        time.sleep(delay)

    def _init_browser(self):
        """يبدأ المتصفح مرة واحدة لكل سكرابر"""
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            logger.info(f"[{self.source_name}] Browser launched (headless={self.headless})")
        return self._browser

    def _get_page(self) -> Page:
        """ينشئ صفحة جديدة بسياق نظيف لكل عملية سكراب"""
        browser = self._init_browser()
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        return page

    def _safe_click(self, page: Page, selector: str, timeout: int = 5000):
        """ينتظر العنصر ويضغط عليه بأمان"""
        try:
            page.wait_for_selector(selector, timeout=timeout)
            page.click(selector)
            return True
        except Exception as e:
            logger.warning(f"[{self.source_name}] Failed to click {selector}: {e}")
            return False

    def close(self):
        """يغلق المتصفح وينهي الجلسة - لازم تستدعيه بعد السكراب"""
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        logger.info(f"[{self.source_name}] Browser closed")

    @abstractmethod
    def scrape(self, query: str, location: str = "", max_jobs: int = None) -> List[RawJob]:
        pass