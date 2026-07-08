# debug_indeed_selectors.py
"""
سكريبت تشخيصي بس - مش جزء من الـ pipeline.
بيفتح صفحة نتايج Indeed، ويحفظ:
  1. الـ HTML الكامل للصفحة -> indeed_page_debug.html
  2. الـ outerHTML لأول كارت وظيفة (لو لقى) -> indeed_card_debug.html
عشان نقارن الـ selectors الحالية في indeed.py مع الواقع ونظبطها.

تشغيل:
    python scripts/debug_indeed_selectors.py
"""

import asyncio
import sys
from pathlib import Path

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.async_api import async_playwright

SEARCH_URL = "https://eg.indeed.com/jobs?q=python+developer&l=Egypt"

# جرب أكتر من selector محتمل للكونتينر الرئيسي بتاع كل كارت وظيفة
CANDIDATE_CONTAINER_SELECTORS = [
    "div.job_seen_beacon",
    "td.resultContent",
    "div.cardOutline",
    "div[data-testid='slider_item']",
    "li[data-testid='job-card']",
    "div.jobsearch-SerpJobCard",
]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print(f"🔍 فاتح: {SEARCH_URL}")
        await page.goto(SEARCH_URL, timeout=30000)
        await page.wait_for_timeout(5000)  # وقت كافي للصفحة تحمل / أي تحقق يدوي لو ظهر

        # 1) احفظ الصفحة كاملة
        full_html = await page.content()
        out_page = Path("indeed_page_debug.html")
        out_page.write_text(full_html, encoding="utf-8")
        print(f"💾 اتحفظت الصفحة كاملة في: {out_page.resolve()}")

        # 2) دوّر على أول selector شغال من اللستة واحفظ الكارت بتاعه
        found = False
        for selector in CANDIDATE_CONTAINER_SELECTORS:
            elements = await page.query_selector_all(selector)
            count = len(elements)
            print(f"   selector '{selector}' -> {count} عنصر")
            if count > 0 and not found:
                card_html = await elements[0].evaluate("el => el.outerHTML")
                out_card = Path("indeed_card_debug.html")
                out_card.write_text(card_html, encoding="utf-8")
                print(f"💾 اتحفظ أول كارت باستخدام '{selector}' في: {out_card.resolve()}")
                found = True

        if not found:
            print("⚠️  ولا selector من اللستة لقى حاجة. افتح indeed_page_debug.html يدوي وشوف الشكل.")

        print("\nاضغط Enter في الترمينال عشان تقفل المتصفح...")
        input()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())