"""
تيست محلي بسيط لسكرابر وزوف — بيشغل الـ scraper الحقيقي (نفس الكود اللي شغال
في main.py، من غير أي تعديل) على عدد قليل من الوظائف، مرة headless=True (زي
اللي شغال على HF Spaces) ومرة headless=False (زي اللي شغال عندك عادي)،
عشان نقارن ونعرف المشكلة مرتبطة بالـ headless mode ولا بحاجة تانية (زي الـ IP
بتاع HF نفسه).

طريقة التشغيل:
    python scripts/test_wuzzuf_local.py

لو عايز تجرب query/location مختلفين:
    python scripts/test_wuzzuf_local.py "python developer" "cairo"
"""

import asyncio
import sys
from pathlib import Path

# عشان نقدر نعمل import للموديولز من روت المشروع لو شغلنا السكريبت من جوه scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.wuzzuff import WuzzufScraper  # noqa: E402
from core.logger import logger  # noqa: E402


async def run_case(headless: bool, query: str, location: str, max_jobs: int = 3):
    label = "HEADLESS=True (زي HF Spaces)" if headless else "HEADLESS=False (نافذة متصفح ظاهرة)"
    print("\n" + "=" * 70)
    print(f"🧪 التيست: {label}")
    print("=" * 70)

    scraper = WuzzufScraper(headless=headless)
    try:
        jobs = await scraper.scrape(query=query, location=location, max_jobs=max_jobs)
    finally:
        await scraper.close()

    if not jobs:
        print("❌ مفيش أي وظيفة اترجعت خالص (فشل حتى في صفحة النتائج نفسها).")
        return

    ok_count = 0
    for i, job in enumerate(jobs, start=1):
        has_content = bool(job.description.strip()) or bool(job.requirements.strip())
        status = "✅" if has_content else "❌"
        if has_content:
            ok_count += 1
        print(f"\n{status} [{i}] {job.title}")
        print(f"    الرابط: {job.apply_link}")
        print(f"    desc={len(job.description)} حرف | req={len(job.requirements)} حرف")
        if has_content:
            preview = (job.description or job.requirements)[:150].replace("\n", " ")
            print(f"    عينة من المحتوى: {preview}...")

    print(f"\n📊 الخلاصة: {ok_count}/{len(jobs)} وظيفة رجعلها description/requirements فعلي.")


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "ai engineer"
    location = sys.argv[2] if len(sys.argv) > 2 else "egypt"

    print(f"🔎 هنجرب البحث عن: '{query}' في '{location}'")

    # الحالة الأهم للمقارنة مع HF Spaces
    await run_case(headless=True, query=query, location=location)

    # مقارنة مع نفس الجهاز بس headless=False، عشان نستبعد إن السبب هو
    # الـ headless mode نفسه (مش بيئة HF/الـ IP)
    await run_case(headless=False, query=query, location=location)

    print("\n" + "=" * 70)
    print("لو الحالتين هنا رجعوا محتوى (✅) والمشكلة لسه موجودة بس على HF Spaces،")
    print("يبقى السبب غالبًا مرتبط ببيئة/IP الاستضافة نفسها مش بالكود أو بالـ headless mode.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())