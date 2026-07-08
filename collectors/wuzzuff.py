# collectors/wuzzuff.py

import re
from typing import List, Optional
from urllib.parse import urljoin

from playwright.async_api import Page

from collectors.base import BaseScraper
from core.config import settings
from core.logger import logger
from core.models import RawJob

WUZZUF_BASE = "https://wuzzuf.net"


class WuzzufScraper(BaseScraper):
    source_name = "wuzzuf"

    async def scrape(self, query: str, location: str = "", max_jobs: int = None) -> List[RawJob]:
        max_jobs = max_jobs or settings.max_jobs_per_source
        all_jobs = []
        page_num = 1

        page: Page = await self._get_page()

        search_url = f"{WUZZUF_BASE}/search/jobs?q={query.replace(' ', '+')}"
        if location:
            search_url += f"&l={location.replace(' ', '+')}"

        logger.info(f"[{self.source_name}] Navigate to: {search_url}")
        await page.goto(search_url)

        try:
            await page.wait_for_selector("div[class*='css-pkv5jc']", timeout=15000)
        except Exception:
            logger.warning("Timeout waiting for job cards")
            await page.context.close()
            return []

        self._human_delay(extra=1.5)

        while len(all_jobs) < max_jobs and page_num <= settings.max_pages:
            logger.info(f"[{self.source_name}] Scraping page {page_num}...")

            job_cards = await page.query_selector_all("div[class*='css-pkv5jc']")
            if not job_cards:
                break

            # نجمع الـ links الأساسية من كروت نتائج البحث
            basic_list = []
            for card in job_cards:
                if len(all_jobs) + len(basic_list) >= max_jobs:
                    break
                try:
                    basic = await self._parse_card_basic(card)
                    if basic:
                        basic_list.append(basic)
                except Exception as e:
                    logger.warning(f"Error parsing card: {e}")

            # نجيب تفاصيل كل وظيفة بـ Playwright في نفس الـ context
            # (مش context منعزل) عشان نستفيد من الـ session cookies
            for basic in basic_list:
                if len(all_jobs) >= max_jobs:
                    break
                description, requirements = await self._fetch_details_playwright(
                    page, basic["apply_link"]
                )
                job = RawJob(
                    title=basic["title"],
                    company=basic["company"],
                    location=basic["location"],
                    description=description,
                    requirements=requirements,
                    apply_link=basic["apply_link"],
                    apply_email="",
                    apply_type="other",
                    source="wuzzuf",
                    external_id=basic["ext_id"],
                )
                if job.is_valid():
                    all_jobs.append(job)
                    logger.info(
                        f"✅ [{len(all_jobs)}] {basic['title'][:50]} "
                        f"| desc={len(description)}c req={len(requirements)}c"
                    )

            if len(all_jobs) < max_jobs:
                next_button = await page.query_selector("a[aria-label='Next']")
                if next_button and "disabled" not in (await next_button.get_attribute("class") or ""):
                    await next_button.click()
                    await page.wait_for_load_state("networkidle")
                    self._human_delay(extra=2)
                    page_num += 1
                else:
                    break
            else:
                break

        await page.context.close()
        logger.info(f"[{self.source_name}] Total jobs collected: {len(all_jobs)}")
        return all_jobs

    async def _parse_card_basic(self, card) -> Optional[dict]:
        """يجيب البيانات الأساسية من كارت نتائج البحث."""
        try:
            title_elem = await card.query_selector("h2 a")
            title = (await title_elem.inner_text()).strip() if title_elem else ""

            company_elem = await card.query_selector("a[class*='css-ipsyv7']")
            company = (await company_elem.inner_text()).strip() if company_elem else "Unknown"

            location_elem = await card.query_selector("span[class*='css-16x61xq']")
            location = (await location_elem.inner_text()).strip() if location_elem else ""

            link_elem = title_elem or await card.query_selector("a[href*='/jobs/']")
            apply_link = ""
            if link_elem:
                href = await link_elem.get_attribute("href")
                if href:
                    apply_link = urljoin(WUZZUF_BASE, href)

            if not title or not apply_link:
                return None

            ext_id = ""
            m = re.search(r"/p/([a-z0-9]+)-", apply_link)
            if m:
                ext_id = m.group(1)

            return {
                "title": title,
                "company": company,
                "location": location,
                "apply_link": apply_link,
                "ext_id": ext_id,
            }
        except Exception as e:
            logger.warning(f"Parse card error: {e}")
            return None

    async def _fetch_details_playwright(self, main_page: Page, job_url: str) -> tuple:
        """
        يجيب تفاصيل الوظيفة بـ Playwright في نفس الـ browser context.
        بيفتح tab جديد → يجيب الداتا → يقفله.
        أسرع من إنشاء context جديد وبيستخدم نفس الـ session.
        """
        description = ""
        requirements = ""

        if not job_url:
            return description, requirements

        detail_page = None
        try:
            # فتح tab جديد في نفس الـ context
            detail_page = await main_page.context.new_page()
            await detail_page.goto(job_url, timeout=30000, wait_until="domcontentloaded")

            # انتظر إن محتوى الوظيفة يظهر
            try:
                await detail_page.wait_for_selector(
                    "section.css-5ks56s, div.css-fo5k8l, div[class*='description'], h3",
                    timeout=8000,
                )
            except Exception:
                pass  # لو مجاش، هنجرب نستخرج من النص العادي

            # اضغط "Read more" لو موجود
            for selector in ["button:has-text('Read more')", "button:has-text('Show more')"]:
                try:
                    btn = detail_page.locator(selector)
                    if await btn.count() > 0:
                        await btn.first.click()
                        self._human_delay(extra=0.5)
                except Exception:
                    pass

            page_text = await detail_page.inner_text("body")

            description  = self._extract_section(
                page_text,
                start_markers=["Job Description", "وصف الوظيفة"],
                stop_markers=[
                    "Job Requirements", "متطلبات الوظيفة",
                    "About Company", "عن الشركة",
                    "Featured Jobs", "Similar Jobs",
                ],
            )

            requirements = self._extract_section(
                page_text,
                start_markers=["Job Requirements", "متطلبات الوظيفة"],
                stop_markers=[
                    "About Company", "عن الشركة",
                    "Featured Jobs", "Similar Jobs",
                    "Job Type", "Seniority Level",
                    "Apply Now", "تقدم الآن",
                ],
            )

        except Exception as e:
            logger.warning(f"Failed to fetch details from {job_url}: {e}")
        finally:
            if detail_page:
                try:
                    await detail_page.close()
                except Exception:
                    pass

        return self._clean_text(description), self._clean_text(requirements)

    def _extract_section(
        self,
        text: str,
        start_markers: List[str],
        stop_markers: List[str],
    ) -> str:
        for marker in start_markers:
            if marker in text:
                parts = text.split(marker, 1)
                if len(parts) > 1:
                    section = parts[1]
                    for stop in stop_markers:
                        if stop in section:
                            section = section.split(stop)[0]
                    return section.strip()
        return ""

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)