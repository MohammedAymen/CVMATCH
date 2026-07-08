# scripts/login_indeed.py
"""
سكريبت تشغّله مرة واحدة بس عشان تسجل دخول بحسابك على Indeed يدويًا،
والسكريبت بيحفظ الكوكيز/الـ session بتاعتك في ملف (indeed_auth_state.json)
عشان الـ scraper يقدر يستخدمها بعد كده من غير ما تسجل دخول كل مرة.

تشغيل:
    python scripts/login_indeed.py

هيفتحلك نافذة Chrome حقيقية - سجل دخول بإيدك بحسابك، وبعد ما تخلص وترجع
لصفحة الوظايف عادي، ارجع للـ terminal ودوس Enter.
"""

import asyncio
import sys
from pathlib import Path

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright

AUTH_STATE_PATH = PROJECT_ROOT / "indeed_auth_state.json"


async def main():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = await p.chromium.launch(headless=False)

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto("https://eg.indeed.com/account/login")

        print("=" * 60)
        print("سجل دخول بإيدك في النافذة اللي فتحت (بحسابك على Indeed).")
        print("بعد ما تخلص تسجيل الدخول وترجع تشوف صفحة عادية (مش صفحة")
        print("تسجيل الدخول)، ارجع هنا ودوس Enter عشان نحفظ الـ session.")
        print("=" * 60)
        input("اضغط Enter بعد ما تخلص تسجيل الدخول... ")

        await context.storage_state(path=str(AUTH_STATE_PATH))
        print(f"✅ اتحفظت الـ session في: {AUTH_STATE_PATH}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())