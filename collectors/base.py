# collectors/base.py

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from core.config import settings
from core.logger import logger
from core.models import RawJob


class BaseScraper(ABC):
    source_name: str = "unknown"

    def __init__(self, headless: bool = None):
        self.headless = headless if headless is not None else settings.headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def _human_delay(self, extra: float = 0.0):
        delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max) + extra
        logger.debug(f"[{self.source_name}] Sleeping {delay:.1f}s...")
        time.sleep(delay)

    async def _init_browser(self):
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            logger.info(f"[{self.source_name}] Browser launched (headless={self.headless})")
        return self._browser

    async def _get_page(self) -> Page:
        browser = await self._init_browser()
        if self._context is None:
            self._context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        page = await self._context.new_page()
        return page

    async def _get_isolated_page(self) -> tuple[Page, BrowserContext]:
        """
        بيرجع صفحة جوه context منفصل تمامًا عن self._context الرئيسي.
        استخدمها لأي صفحة فرعية (زي صفحة تفاصيل الوظيفة) محتاجة تتقفل
        لوحدها بعد الاستخدام، من غير ما تأثر على الصفحة الأساسية اللي
        بتلف على نتائج البحث. الكولر مسؤول عن إغلاق الـ context الراجع
        (مش self._context العام).
        """
        browser = await self._init_browser()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        return page, context

    async def _safe_click(self, page: Page, selector: str, timeout: int = 5000):
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            await page.click(selector)
            return True
        except Exception as e:
            logger.warning(f"[{self.source_name}] Failed to click {selector}: {e}")
            return False

    async def close(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info(f"[{self.source_name}] Browser closed")

    @abstractmethod
    async def scrape(self, query: str, location: str = "", max_jobs: int = None) -> List[RawJob]:
        pass