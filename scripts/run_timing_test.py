"""
سكريبت تجربة سريع لقياس وقت السكرابينج.

الاستخدام:
    1. حط الملف ده في مجلد scripts/ بتاعك (زي باقي السكريبتات).
    2. حط wuzzuff_debug.py جوه مجلد collectors/ (جنب wuzzuff.py الأصلي،
       من غير ما تستبدله).
    3. شغّل من روت المشروع (مش من جوه scripts/) عشان الـ imports
       (core.config, core.logger, core.models, collectors.base) تتلاقي:
           python scripts/run_timing_test.py

هيجرب يجيب 5 وظايف بس (عشان التجربة تبقى سريعة) ويطبعلك تفصيل
الوقت في الآخر: قد ايه راح على networkidle، قد ايه راح على
wait_for_selector بتاع صفحة التفاصيل، وقد ايه إجمالي فتح كل تاب.
"""

import asyncio
import os
import sys

# بنضيف روت المشروع لـ sys.path يدويًا، عشان لو حد شغل السكريبت وهو
# واقف جوه مجلد scripts/ نفسه (مش من الروت)، الـ imports زي core.config
# و collectors.wuzzuff_debug برضو تتلاقي من غير ما يحتاج يغير مكانه.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from collectors.wuzzuff_debug import WuzzufScraper


async def main():
    scraper = WuzzufScraper(headless=True)
    try:
        jobs = await scraper.scrape(query="python developer", max_jobs=15)
        print(f"\n✅ اتجابت {len(jobs)} وظيفة بنجاح.\n")
        for j in jobs:
            print(f"- {j.title} | {j.company} | desc={len(j.description)}c req={len(j.requirements)}c")
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())