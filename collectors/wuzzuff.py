import re
from typing import List, Optional
from urllib.parse import urljoin

from playwright.sync_api import Page

from core.config import Settings
from core.logger import logger
from collectors.base import BaseScraper, RawJob
from core.models import RawJob

WUZZUF_BASE = "https://wuzzuf.net"


class WuzzufScraper(BaseScraper):
    source_name = "wuzzuf"

    def scrape(self, query: str, location: str = "", max_jobs: int = None) -> List[RawJob]:
        max_jobs = max_jobs or Settings.max_jobs_per_source
        all_jobs = []
        page_num = 1

        page: Page = self._get_page()

        search_url = f"{WUZZUF_BASE}/search/jobs?q={query.replace(' ', '+')}"
        if location:
            search_url += f"&l={location.replace(' ', '+')}"

        logger.info(f"[{self.source_name}] Navigate to: {search_url}")
        page.goto(search_url)

        # انتظار تحميل الكروت
        try:
            page.wait_for_selector("div[class*='css-pkv5jc']", timeout=15000)
        except:
            logger.warning("Timeout waiting for job cards")
            page.context.close()
            return []

        self._human_delay(extra=1.5)

        while len(all_jobs) < max_jobs and page_num <= Settings.max_pages:
            logger.info(f"[{self.source_name}] Scraping page {page_num}...")

            job_cards = page.query_selector_all("div[class*='css-pkv5jc']")
            if not job_cards:
                break

            for card in job_cards:
                if len(all_jobs) >= max_jobs:
                    break
                try:
                    job = self._parse_card(card)
                    if job and job.is_valid():
                        all_jobs.append(job)
                        logger.debug(f"Added job: {job.title} at {job.company}")
                except Exception as e:
                    logger.warning(f"Error parsing card: {e}")

            # الانتقال للصفحة التالية
            if len(all_jobs) < max_jobs:
                next_button = page.query_selector("a[aria-label='Next']")
                if next_button and "disabled" not in (next_button.get_attribute("class") or ""):
                    next_button.click()
                    page.wait_for_load_state("networkidle")
                    self._human_delay(extra=2)
                    page_num += 1
                else:
                    break
            else:
                break

        page.context.close()
        logger.info(f"[{self.source_name}] Total jobs collected: {len(all_jobs)}")
        return all_jobs

    def _parse_card(self, card) -> Optional[RawJob]:
        try:
            title_elem = card.query_selector("h2 a")
            title = title_elem.inner_text().strip() if title_elem else "Unknown"

            company_elem = card.query_selector("a[class*='css-ipsyv7']")
            company = company_elem.inner_text().strip() if company_elem else "Unknown"

            location_elem = card.query_selector("span[class*='css-16x61xq']")
            location = location_elem.inner_text().strip() if location_elem else ""

            link_elem = title_elem if title_elem else card.query_selector("a[href*='/jobs/']")
            apply_link = ""
            if link_elem:
                href = link_elem.get_attribute("href")
                if href:
                    apply_link = urljoin(WUZZUF_BASE, href)

            ext_id = ""
            if apply_link:
                m = re.search(r"/jobs/(\d+)", apply_link)
                ext_id = m.group(1) if m else ""

            # جلب التفاصيل من صفحة الوظيفة (وصف، متطلبات، إيميل)
            description, requirements, apply_email = self._fetch_details(apply_link)

            apply_type = "email" if apply_email else "other"

            return RawJob(
                title=title,
                company=company,
                location=location,
                description=description,
                requirements=requirements,
                apply_link=apply_link,
                apply_email=apply_email,
                apply_type=apply_type,
                source="wuzzuf",
                external_id=ext_id,
            )
        except Exception as e:
            logger.warning(f"Parse card error: {e}")
            return None

    def _fetch_details(self, job_url: str):
        """يفتح صفحة الوظيفة، يضمن تحميل المحتوى الديناميكي، ويستخرج البيانات."""
        description = ""
        requirements = ""
        apply_email = ""

        if not job_url:
            return description, requirements, apply_email

        try:
            page = self._get_page()
            page.goto(job_url, timeout=20000)

            # انتظار تحميل الصفحة الأساسي
            page.wait_for_load_state("networkidle")
            self._human_delay(extra=1.0)

            # التمرير لأسفل لتفعيل lazy loading
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._human_delay(extra=1.5)

            # التمرير لأعلى قليلاً ثم لأسفل مرة أخرى (لضمان تحميل كل شيء)
            page.evaluate("window.scrollTo(0, 0)")
            self._human_delay(extra=0.5)
            

            # النقر على أي زر "قراءة المزيد" إن وجد
            more_selectors = [
                "button:has-text('Read more')",
                "button:has-text('Show more')",
                "button:has-text('قراءة المزيد')",
                "a:has-text('Read more')"
            ]
            for selector in more_selectors:
                try:
                    if page.locator(selector).count() > 0:
                        page.locator(selector).first.click()
                        self._human_delay(extra=1.0)
                except:
                    pass

            # الحصول على النص الكامل للصفحة بعد التحميل
            page_text = page.inner_text("body")

            # استخراج الوصف - يدعم الإنجليزية والعربية
            if "Job Description" in page_text:
                parts = page_text.split("Job Description")
                if len(parts) > 1:
                    desc_part = parts[1]
                    if "Job Requirements" in desc_part:
                        description = desc_part.split("Job Requirements")[0].strip()
                    else:
                        description = desc_part.strip()
            elif "وصف الوظيفة" in page_text:
                parts = page_text.split("وصف الوظيفة")
                if len(parts) > 1:
                    desc_part = parts[1]
                    if "متطلبات الوظيفة" in desc_part:
                        description = desc_part.split("متطلبات الوظيفة")[0].strip()
                    else:
                        description = desc_part.strip()
            else:
                # محاولة بديلة: البحث عن div محدد
                desc_selectors = ["div.css-fo5k8l", "div[class*='description']", "section[class*='description']"]
                for sel in desc_selectors:
                    elem = page.query_selector(sel)
                    if elem:
                        description = elem.inner_text().strip()
                        break

            # استخراج المتطلبات
            if "Job Requirements" in page_text:
                parts = page_text.split("Job Requirements")
                if len(parts) > 1:
                    req_part = parts[1]
                    # نقطة التوقف عند عناوين غير مرغوب فيها
                    stop_markers = [
                        "Featured Jobs", "Similar Jobs", "About Company", "Job Type",
                        "Seniority Level", "Industry", "Employment Type", "Job ID",
                        "Apply Now", "Share this job", "Report this job", "Skills",
                        "وظائف مميزة", "وظائف مشابهة", "عن الشركة", "نوع الوظيفة",
                        "مستوى الأقدمية", "المهارات", "تقدم الآن"
                    ]
                    for marker in stop_markers:
                        if marker in req_part:
                            req_part = req_part.split(marker)[0]
                            break
                    requirements = req_part.strip()
            elif "متطلبات الوظيفة" in page_text:
                parts = page_text.split("متطلبات الوظيفة")
                if len(parts) > 1:
                    req_part = parts[1]
                    stop_markers = ["وظائف مميزة", "وظائف مشابهة", "عن الشركة", "نوع الوظيفة", "مستوى الأقدمية", "المهارات"]
                    for marker in stop_markers:
                        if marker in req_part:
                            req_part = req_part.split(marker)[0]
                            break
                    requirements = req_part.strip()
            else:
                req_selectors = ["div.css-1t5f0fr", "div[class*='requirements']", "section[class*='requirements']"]
                for sel in req_selectors:
                    elem = page.query_selector(sel)
                    if elem:
                        requirements = elem.inner_text().strip()
                        break

            # استخراج البريد الإلكتروني
            email_pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
            email_match = re.search(email_pattern, page_text)
            if email_match:
                potential_email = email_match.group(0)
                # استبعاد البريدات العامة
                excluded = ["support", "info@wuzzuf", "noreply", "careers@", "hr@", "job@"]
                if not any(x in potential_email.lower() for x in excluded):
                    apply_email = potential_email

            # تنظيف النصوص من الأسطر الفارغة الزائدة
            description = self._clean_text(description)
            requirements = self._clean_text(requirements)

            page.context.close()
        except Exception as e:
            logger.warning(f"Failed to fetch details from {job_url}: {e}")

        return description, requirements, apply_email

    def _clean_text(self, text: str) -> str:
        """تنظيف النص من الأسطر الفارغة الزائدة والمسافات."""
        if not text:
            return ""
        lines = text.splitlines()
        cleaned_lines = [line.strip() for line in lines if line.strip()]
        return "\n".join(cleaned_lines)