from collectors.wuzzuff import WuzzufScraper
from core.logger import logger

def main():
    scraper = WuzzufScraper(headless=False)
    try:
        jobs = scraper.scrape("python", location="egypt", max_jobs=10)
        logger.info(f"✅ Total jobs scraped: {len(jobs)}")
        for idx, job in enumerate(jobs[:5], 1):
            print(f"{idx}. {job.title} - {job.company} ({job.location})")
            print(f"   Apply: {job.apply_link}\n")
            print(f"   Description: {job.description}...\n")
            print (f"   Requirements: {job.requirements}...\n")
            print("-" * 80)
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
    finally:
        scraper.close()
if __name__ == "__main__":
    main()