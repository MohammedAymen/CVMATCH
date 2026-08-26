"""
سكريبت تجربة منفصل - بيختبر بس هل cloudscraper بيقدر يعدي تحدي Cloudflare
بتاع Wuzzuf ولا لأ. مش متكامل مع باقي المشروع، تشغيل مستقل بس للتشخيص.

تشغيل:
    pip install cloudscraper --break-system-packages
    python scripts/test_cloudscraper.py
"""

import cloudscraper

def main():
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )

    url = "https://wuzzuf.net/search/jobs?q=ai+engineer&l=egypt"
    print(f"جاري الطلب: {url}\n")

    response = scraper.get(url, timeout=20)

    print(f"Status Code: {response.status_code}")
    print(f"Content-Length: {len(response.text)} chars\n")

    if "Just a moment" in response.text or "Performing security verification" in response.text:
        print("❌ لسه واقف على تحدي Cloudflare - مش عدت")
        print("أول 300 حرف من الرد:")
        print(response.text[:300])
    elif "css-pkv5jc" in response.text:
        print("✅ عدت! لقينا الـ job cards في الـ HTML")
    else:
        print("⚠️  حاجة تالتة غير متوقعة - مش تحدي Cloudflare ومش لقينا job cards")
        print("أول 500 حرف من الرد:")
        print(response.text[:500])


if __name__ == "__main__":
    main()
