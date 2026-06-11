import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # تأخيرات السكرابر (بالثواني)
    scrape_delay_min = float(os.getenv("SCRAPE_DELAY_MIN", "3"))
    scrape_delay_max = float(os.getenv("SCRAPE_DELAY_MAX", "7"))

    # الحد الأقصى للوظايف لكل مصدر
    max_jobs_per_source = int(os.getenv("MAX_JOBS_PER_SOURCE", "50"))

    # عدد صفحات النتائج
    max_pages = int(os.getenv("MAX_PAGES", "3"))

    # تشغيل المتصفح بدون واجهة (للخوادم)
    headless = os.getenv("HEADLESS", "False").lower() == "true"

settings = Settings()