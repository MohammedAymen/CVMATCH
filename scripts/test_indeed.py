# test_indeed.py
"""
تيست خارجي مستقل لـ IndeedScraper - يتشغل لوحده من غير ما يلمس باقي
الـ pipeline (embedder, Notion, إلخ). الهدف بس إنك تتأكد إن الـ scraper
شغال والـ selectors لسه صح قبل ما تدمجه في حاجة تانية.

تشغيل:
    python test_indeed.py
    python test_indeed.py "python developer" "Cairo, Egypt" 5
"""

import asyncio
import sys
from pathlib import Path

# نفس الفيكس بتاع الـ ProactorEventLoop على ويندوز
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from collectors.indeed import IndeedScraper


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "python developer"
    location = sys.argv[2] if len(sys.argv) > 2 else "Egypt"
    max_jobs = 1
#int(sys.argv[3]) if len(sys.argv) > 3 else 5
    print(f"🔍 بنبحث عن: '{query}' | location='{location}' | max_jobs={max_jobs}")
    print("-" * 60)

    # headless=False عشان تتابع بعينك إيه اللي بيحصل ولو فيه CAPTCHA
    scraper = IndeedScraper(headless=False)

    jobs = await scraper.scrape(query=query, location=location, max_jobs=max_jobs)

    print("-" * 60)
    print(f"✅ النتيجة: {len(jobs)} وظيفة\n")

    for i, job in enumerate(jobs, 1):
        print(f"[{i}] {job.title} @ {job.company}")
        print(f"    📍 {job.location}")
        print(f"    🔗 {job.apply_link}")
        print(f"    🆔 {job.external_id}")
        print(f"    📝 description: {len(job.description)} حرف")
        print(f"    📋  description: {job.description}")
        print(f"    📋 requirements: {len(job.requirements)} حرف")
        print(f"    📋 requirements: {job.requirements}")
        print()

    if not jobs:
        print("⚠️  مفيش نتايج. احتمالات:")
        print("   - الـ selectors اتغيرت (افتح الموقع يدوي وقارن).")
        print("   - اتحظرت (CAPTCHA) - جرب تاني بعد شوية.")
        print("   - الكويري/اللوكيشن مفيهاش وظايف.")


if __name__ == "__main__":
    asyncio.run(main())