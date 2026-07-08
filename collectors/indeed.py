# collectors/indeed.py

import asyncio
import random
import re
from typing import List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

from playwright.async_api import Page

from collectors.base import BaseScraper
from core.config import settings
from core.logger import logger
from core.models import RawJob


class IndeedScraper(BaseScraper):
    """
    ملاحظة: Indeed عنده حماية (Cloudflare/PerimeterX) أقوى بكتير من Wuzzuf.
    لو ظهر CAPTCHA أو "unusual traffic" بنوقف على طول بدل ما نلف في حلقة فاشلة.
    """

    source_name = "indeed"

    # ملاحظة: كلمة "captcha" لوحدها مش كافية - Indeed بيحط reCAPTCHA widget
    # عادي جوه فورم التقديم حتى لو مفيش حظر فعلي. لازم نستخدم عبارات أدق
    # بتظهر بس في صفحة الحظر الفعلية.
    BLOCKED_MARKERS = [
        "additional verification required",
        "unusual traffic from your computer network",
        "verify you are a human",
        "checking your browser before accessing",
    ]

    def __init__(self, headless: bool = None, country_domain: str = "eg"):
        super().__init__(headless=headless)
        self.indeed_base = f"https://{country_domain}.indeed.com"

    async def scrape(self, query: str, location: str = "", max_jobs: int = None) -> List[RawJob]:
        max_jobs = max_jobs or settings.max_jobs_per_source
        all_jobs = []
        page_num = 1

        page: Page = await self._get_page()

        search_url = f"{self.indeed_base}/jobs?q={query.replace(' ', '+')}"
        if location:
            search_url += f"&l={location.replace(' ', '+')}"

        logger.info(f"[{self.source_name}] Navigate to: {search_url}")
        await page.goto(search_url)

        try:
            await page.wait_for_selector("div.cardOutline", timeout=15000)
        except Exception:
            logger.warning("Timeout waiting for job cards")
            await page.context.close()
            return []

        self._human_delay(extra=1.5)

        # بنتتبع "الرابط النضيف" لصفحة النتائج (من غير أي vjk) عشان نرجعله
        # قبل الباجيناشن كل مرة، حتى لو آخر تفصيلة سبتنا على رابط فيه vjk.
        clean_listing_url = page.url

        while len(all_jobs) < max_jobs and page_num <= settings.max_pages:
            logger.info(f"[{self.source_name}] Scraping page {page_num}...")

            if await self._is_blocked(page):
                logger.warning(
                    f"[{self.source_name}] CAPTCHA/verification request - وقفنا هنا. "
                    "جرب headless=False أو زود الـ delays أو استخدم proxy."
                )
                break

            job_cards = await page.query_selector_all("div.cardOutline")
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
            for basic in basic_list:
                if len(all_jobs) >= max_jobs:
                    break

                # تأخير بسيط عشوائي قبل ما نفتح كل تفصيلة، بدل ما نفتح
                # التابات ورا بعض بسرعة زي بوت - ده بيقلل بصمة الأتمتة.
                self._human_delay(extra=random.uniform(1.0, 2.5))

                description, requirements = await self._fetch_details_via_vjk(
                    page, basic, clean_listing_url
                )
                job = RawJob(
                    title=basic["title"],
                    company=basic["company"],
                    location=basic["location"],
                    description=description,
                    requirements=requirements,
                    apply_link=basic["apply_link"],
                    apply_email="",
                    apply_type="external",
                    source="indeed",
                    external_id=basic["ext_id"],
                )
                if job.is_valid():
                    all_jobs.append(job)
                    logger.info(
                        f"✅ [{len(all_jobs)}] {basic['title'][:50]} "
                        f"| desc={len(description)}c req={len(requirements)}c"
                    )

            if len(all_jobs) < max_jobs:
                # لازم نرجع للرابط النضيف (من غير vjk) قبل ما ندور على زرار
                # Next، لأن آخر تفصيلة سابتنا على رابط فيه vjk لوظيفة معينة.
                if page.url != clean_listing_url:
                    await page.goto(clean_listing_url, wait_until="domcontentloaded")
                    await asyncio.sleep(random.uniform(0.5, 1.0))

                next_button = await page.query_selector(
                    "a[data-testid='pagination-page-next'], a[aria-label='Next Page'], a[aria-label='Next']"
                )
                if next_button:
                    await next_button.click()
                    await page.wait_for_load_state("networkidle")
                    self._human_delay(extra=2)
                    page_num += 1
                    clean_listing_url = page.url  # الرابط اتغير (باجيناشن)
                else:
                    break
            else:
                break

        await page.context.close()
        logger.info(f"[{self.source_name}] Total jobs collected: {len(all_jobs)}")
        return all_jobs

    async def _is_blocked(self, page: Page) -> bool:
        # نتشيك على الـ <title> الأول (أدق وأسرع وأقل عرضة لـ false positive
        # من مسح كل محتوى الصفحة اللي ممكن يحتوي على كلمات زي "captcha"
        # جوه سكريبتات عادية زي reCAPTCHA widget).
        try:
            title = (await page.title()).lower()
        except Exception:
            title = ""
        if any(marker in title for marker in self.BLOCKED_MARKERS):
            return True

        # fallback: نتشيك على body text بس (مش الـ HTML/scripts كاملة)
        try:
            body_text = (await page.inner_text("body")).lower()
        except Exception:
            body_text = ""
        return any(marker in body_text for marker in self.BLOCKED_MARKERS)

    async def _parse_card_basic(self, card) -> Optional[dict]:
        """يجيب البيانات الأساسية من كارت نتائج البحث.

        ملاحظة مهمة: Indeed بيستخدم <h3 class="jobTitle"> مش <h2> زي ما كنا
        مفترضين الأول - ده كان سبب رجوع 0 نتايج قبل كده رغم إن الـ container
        (div.cardOutline) كان بيتلاقى صح. اتأكدنا من الشكل الفعلي عن طريق
        HTML حقيقي اتبعته لينا.
        """
        try:
            # العنوان + اللينك جوه نفس الـ <a>، وده كمان بيحمل data-jk مباشرة
            link_elem = await card.query_selector("h3.jobTitle a")
            title = ""
            apply_link = ""
            href = None
            ext_id = ""
            if link_elem:
                # العنوان الفعلي جوه <span title="...">
                title_span = await link_elem.query_selector("span[title]")
                if title_span:
                    title = (await title_span.inner_text()).strip()
                else:
                    title = (await link_elem.inner_text()).strip()

                href = await link_elem.get_attribute("href")
                if href:
                    apply_link = urljoin(self.indeed_base, href)

                ext_id = await link_elem.get_attribute("data-jk") or ""

            company_elem = await card.query_selector("span[data-testid='company-name']")
            company = (await company_elem.inner_text()).strip() if company_elem else "Unknown"

            location_elem = await card.query_selector("div[data-testid='text-location']")
            location = (await location_elem.inner_text()).strip() if location_elem else ""

            if not title or not apply_link:
                return None

            if not ext_id:
                m = re.search(r"jk=([a-f0-9]+)", href or "")
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

    async def _fetch_details_via_vjk(self, page: Page, basic: dict, clean_listing_url: str) -> tuple:
        """
        اكتشفنا (من الـ HTML الحقيقي اللي المستخدم بعته) إن Indeed بيعرض
        الوصف الكامل جوه نفس صفحة نتائج البحث لو ضفت بارامتر `vjk=<jk>`
        للرابط - نفس مسار /jobs العادي، من غير أي navigation لـ /rc/clk
        المحمي بقوة. يعني مفيش داعي نضغط على حاجة خالص: بس بنعمل goto
        لنفس رابط صفحة البحث + &vjk=<ext_id>، ونستخرج #jobDescriptionText
        من نفس الصفحة زي ما شفنا في الـ HTML.
        """
        description = ""
        requirements = ""

        ext_id = basic.get("ext_id")
        if not ext_id:
            return description, requirements

        if getattr(self, "_details_blocked", False):
            return description, requirements

        # نضيف/نستبدل بارامتر vjk في الرابط النضيف لصفحة البحث
        parts = urlsplit(clean_listing_url)
        query_params = [
            (k, v) for k, v in parse_qsl(parts.query) if k != "vjk"
        ]
        query_params.append(("vjk", ext_id))
        detail_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query_params), parts.fragment)
        )

        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.warning(f"[{self.source_name}] فشل فتح {detail_url}: {e}")
            return description, requirements

        try:
            await page.wait_for_selector("#jobDescriptionText", timeout=10000)
        except Exception:
            pass  # هنحاول نستخرج أي حاجة موجودة حتى لو التايمينج اختلف

        await asyncio.sleep(random.uniform(0.5, 1.2))

        # تشيك حظر احترازي - مش متوقع يحصل هنا لأننا لسه جوه مسار /jobs
        # العادي (مش /rc/clk المحمي)، لكن لو حصل بأي شكل نوثقه ونوقف.
        if await self._is_blocked(page):
            await self._dump_blocked_diagnostics(page)
            logger.warning(
                f"[{self.source_name}] حظر غير متوقع حتى مع رابط /jobs العادي - وقفنا هنا."
            )
            self._details_blocked = True
            return description, requirements

        try:
            desc_elem = await page.query_selector("#jobDescriptionText")
            full_text = (await desc_elem.inner_text()).strip() if desc_elem else ""
        except Exception as e:
            logger.warning(f"[{self.source_name}] Failed reading description for jk={ext_id}: {e}")
            full_text = ""

        description = full_text
        requirements = self._extract_section(
            full_text,
            start_markers=["Requirements", "Qualifications", "متطلبات الوظيفة"],
            stop_markers=[
                "Benefits", "Job Type", "Schedule",
                "Apply Now", "تقدم الآن", "About the Company",
            ],
        )

        return self._clean_text(description), self._clean_text(requirements)

    async def _try_solve_checkbox_challenge(self, page: Page) -> bool:
        """
        محاولة تعدي تحدي 'Verify you are a human' البسيط (checkbox زي
        Cloudflare Turnstile) بنقرة فأرة حقيقية بمسار حركة، بدل النقر
        البرمجي المباشر اللي بيتكشف بسهولة. الاختيارات هنا تخمينية بناءً
        على شكل Turnstile الشائع - محتاج تتأكد من الـ selector الفعلي لو
        اختلف عندك (افتح الصفحة headless=False وشوف الـ iframe/checkbox
        بالظبط عن طريق DevTools).
        """
        candidate_selectors = [
            "input[type='checkbox']",
            "iframe[title*='challenge' i]",
            "#challenge-stage input",
            "label.cb-lb",
        ]

        target = None
        for selector in candidate_selectors:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    target = el
                    break
            except Exception:
                continue

        if not target:
            return False

        try:
            box = await target.bounding_box()
            if not box:
                return False

            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2

            # حركة فأرة تدريجية (مش قفزة فورية للنقطة) + وقفة عشوائية
            # قبل الضغط - بيقلل شبه الحركة الآلية.
            await page.mouse.move(x - 30, y - 10, steps=8)
            await asyncio.sleep(random.uniform(0.2, 0.4))
            await page.mouse.move(x, y, steps=6)
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.18))
            await page.mouse.up()

            await asyncio.sleep(random.uniform(2.0, 3.5))
            return True
        except Exception as e:
            logger.warning(f"Checkbox challenge attempt failed: {e}")
            return False

    async def _dump_blocked_diagnostics(self, page: Page):
        """
        بياخد screenshot ويطبع تفاصيل صفحة الحظر عشان نعرف بالظبط شكلها -
        هل هي تحدي تفاعلي (checkbox/captcha) ولا صفحة رفض نهائية مباشرة
        بسبب سمعة الـ IP. من غير الديباج ده بنفضل بنخمن في العمى.
        """
        try:
            title = await page.title()
        except Exception:
            title = "<unknown>"

        try:
            body_text = (await page.inner_text("body"))[:500]
        except Exception:
            body_text = "<couldn't read body>"

        screenshot_path = f"blocked_debug_{random.randint(1000, 9999)}.png"
        try:
            await page.screenshot(path=screenshot_path, full_page=False)
        except Exception as e:
            screenshot_path = f"<failed: {e}>"

        logger.warning(
            f"[{self.source_name}] DEBUG blocked page | title='{title}' "
            f"| url={page.url} | screenshot={screenshot_path}\n"
            f"--- body preview ---\n{body_text}\n--------------------"
        )

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