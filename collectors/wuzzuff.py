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

        q_param = query.replace(' ', '+')
        l_param = location.replace(' ', '+') if location else ""

        search_url = f"{WUZZUF_BASE}/search/jobs?q={q_param}"
        if l_param:
            search_url += f"&l={l_param}"

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

           
            for i, basic in enumerate(basic_list):
                if len(all_jobs) >= max_jobs:
                    break

               
                if i > 0:
                    self._human_delay()

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
               
                self._human_delay(extra=5)
                page = await self._rotate_context()
                advanced = await self._go_to_next_page(page, page_num, q_param, l_param)
                if advanced:
                    page_num += 1
                else:
                    logger.info(
                        f"[{self.source_name}] No more pages after page {page_num} "
                        f"— stopping with {len(all_jobs)}/{max_jobs} job(s)."
                    )
                    break
            else:
                break

        await page.context.close()
        logger.info(f"[{self.source_name}] Total jobs collected: {len(all_jobs)}")
        return all_jobs

    # سيلكتورات محتملة لزرار "الصفحة التالية" — بنجرب كل واحد لحد ما نلاقي واحد شغال،
    # عشان لو Wuzzuf غيّرت شكل الصفحة السيلكتور القديم يوقف بهدوء من غير ما يبوّظ كل حاجة.
    NEXT_BUTTON_SELECTORS = [
        "a[aria-label='Next']",
        "a[aria-label='next']",
        "a[title='Next']",
        "a.next",
        "li.next a",
        "a[rel='next']",
        "button[aria-label='Next']",
    ]

    async def _go_to_next_page(self, page: Page, current_page_num: int, q_param: str, l_param: str) -> bool:
    
        for selector in self.NEXT_BUTTON_SELECTORS:
            try:
                next_button = await page.query_selector(selector)
            except Exception:
                continue
            if not next_button:
                continue
            classes = (await next_button.get_attribute("class") or "")
            aria_disabled = (await next_button.get_attribute("aria-disabled") or "")
            if "disabled" in classes or aria_disabled == "true":
                logger.info(f"[{self.source_name}] '{selector}' found but disabled (last page).")
                continue
            try:
                await next_button.click()
                await page.wait_for_load_state("networkidle")
                logger.info(f"[{self.source_name}] Moved to page {current_page_num + 1} via '{selector}'.")
                return True
            except Exception as e:
                logger.warning(f"[{self.source_name}] Click on '{selector}' failed: {e}")
                continue

        logger.warning(
            f"[{self.source_name}] No working 'Next' button selector found on page "
            f"{current_page_num} — falling back to offset URL navigation."
        )

        # بننسخ نفس ترتيب الباراميترز اللي اتأكد إنه شغال فعليًا: q, start, l
        offset = current_page_num
        fallback_url = f"{WUZZUF_BASE}/search/jobs?q={q_param}&start={offset}"
        if l_param:
            fallback_url += f"&l={l_param}"

        try:
            # بنستخدم نفس استراتيجية التحميل اللي نجحت في أول صفحة (مفيش wait_until مخصص +
            # wait_for_selector بمهلة 15 ثانية) بدل domcontentloaded السريع اللي كان بيسبق
            # تحميل المحتوى بالـ JS.
            await page.goto(fallback_url, timeout=30000)
            self._human_delay(extra=1.5)
            try:
                await page.wait_for_selector("div[class*='css-pkv5jc']", timeout=15000)
            except Exception:
                pass

            found = await page.query_selector("div[class*='css-pkv5jc']")
            if found:
                logger.info(f"[{self.source_name}] Moved to page {current_page_num + 1} via offset URL ({fallback_url}).")
                return True

            # تشخيص إضافي عشان لو فشلت تاني نعرف السبب من اللوج مباشرة من غير تخمين
            try:
                page_title = await page.title()
                body_snippet = (await page.inner_text("body"))[:200].replace("\n", " ")
            except Exception:
                page_title, body_snippet = "?", "?"
            logger.info(
                f"[{self.source_name}] Offset URL returned no job cards — assuming last page. "
                f"url={page.url} title='{page_title}' body_start='{body_snippet}'"
            )
            return False
        except Exception as e:
            logger.warning(f"[{self.source_name}] Offset URL navigation failed: {e}")
            return False

    async def _parse_card_basic(self, card) -> Optional[dict]:
       
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
       
        description = ""
        requirements = ""

        if not job_url:
            return description, requirements

        detail_page = None
        try:
            # فتح tab جديد في نفس الـ context
            detail_page = await main_page.context.new_page()
            await detail_page.goto(job_url, timeout=30000, wait_until="domcontentloaded")

            # انتظر إن نص "Job Description"/"وصف الوظيفة" يكون فعلاً موجود في الـ DOM،
            # مش بس أي h3 عشوائي في الصفحة (اللي كان بيخلي الانتظار يعدي بدري على
            # بيئات أبطأ زي HF Spaces قبل ما القسم الحقيقي يترندر، فيرجع الاستخراج فاضي).
            try:
                await detail_page.wait_for_function(
                    """() => {
                        const t = document.body.innerText;
                        return t.includes('Job Description') || t.includes('وصف الوظيفة')
                            || t.includes('Job Requirements') || t.includes('متطلبات الوظيفة');
                    }""",
                    timeout=15000,
                )
            except Exception:
                logger.warning(f"Job description text never appeared for {job_url} within timeout")

            page_text = await detail_page.inner_text("body")

            # لو لسه فاضي (شبكة بطيئة جدًا / محتوى محمّل بعد آخر مصادر)، جرب مرة واحدة
            # كمان بعد مهلة قصيرة قبل ما نستسلم.
            if "Job Description" not in page_text and "وصف الوظيفة" not in page_text:
                await detail_page.wait_for_timeout(3000)
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