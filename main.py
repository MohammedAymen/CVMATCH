# main.py

import os
from pathlib import Path

from core.config import settings
from core.logger import logger

from collectors.wuzzuff import WuzzufScraper

from profile_data.cv_parser import load_cv_documents
from profile_data.github_fetcher import load_github_profile_documents
from profile_data.embedder import build_profile_embeddings, ProfileEmbedder


def build_profile(embedder: ProfileEmbedder = None):
    """
    بناء ملف البروفايل (CV + GitHub) وتحويله إلى embeddings
    إذا تم تمرير embedder موجود، يستخدمه، وإلا ينشئ واحداً جديداً.
    إذا كانت البيانات موجودة بالفعل في قاعدة البيانات، يمكن تخطي هذه الخطوة.
    """
    logger.info("="*50)
    logger.info("Step 1: Preparing profile documents (CV + GitHub)")
    logger.info("="*50)

    # 1.1 تجهيز CV
    cv_path = Path("data/csv/MohamedAymanSalem (3).pdf")   # <-- ضع سيرتك الذاتية هنا
    if cv_path.exists():
        cv_docs = load_cv_documents(str(cv_path))
        logger.info(f"Loaded {len(cv_docs)} chunks from CV")
    else:
        logger.warning(f"CV not found at {cv_path}. Proceeding without it.")
        cv_docs = []

    # 1.2 تجهيز GitHub
    github_username = os.getenv("GITHUB_USERNAME", "your_username")  # <-- ضع اسم مستخدم GitHub الخاص بك
    github_token = settings.github_token
    if github_username and github_username != "your_username":
        github_docs = load_github_profile_documents(
            username=github_username,
            token=github_token,
            max_repos=20,
            fetch_languages=True,
        )
        logger.info(f"Loaded {len(github_docs)} chunks from GitHub")
    else:
        logger.warning("GitHub username not set. Proceeding without it.")
        github_docs = []

    if not cv_docs and not github_docs:
        logger.error("No profile data found. Exiting.")
        return None

    # 1.3 بناء embeddings (إذا لم يكن embedder موجوداً أو نريد إعادة بناء كامل)
    if embedder is None:
        embedder = build_profile_embeddings(
            cv_documents=cv_docs,
            github_documents=github_docs,
            collection_name="profile_chunks",
            model_name=settings.embedding_model,
            embedding_dim=None,  # يمكنك وضع 512 أو 256 لزيادة السرعة
            persist_directory=settings.chroma_persist_directory,
            reset_collection=True,   # يمسح القديم ويبني من جديد
        )
        logger.info("Profile embeddings built from scratch.")
    else:
        # إذا كان embedder موجوداً، يمكن استخدام embedder.embed_and_store_documents مباشرة
        # لكننا سنبني من الصفر هنا للتأكد من التكامل
        pass

    return embedder


def main():
    # 1. بناء embeddings من البروفايل (سيرتك الذاتية و GitHub)
    embedder = build_profile()
    if embedder is None:
        return

    # 2. عرض إحصائيات المجموعة للتأكد
    stats = embedder.get_collection_stats()
    logger.info(f"Collection stats: {stats}")

    # 3. جلب الوظائف
    logger.info("="*50)
    logger.info("Step 2: Scraping jobs from Wuzzuf")
    logger.info("="*50)

    scraper = WuzzufScraper(headless=False)  # غيّر إلى True لو أردت التشغيل بدون واجهة
    try:
        jobs = scraper.scrape("python", location="egypt", max_jobs=10)
        logger.info(f"Scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        return
    finally:
        scraper.close()

    if not jobs:
        logger.warning("No jobs scraped. Exiting.")
        return

    # 4. لكل وظيفة، استرجاع القطع المشابهة من البروفايل
    logger.info("="*50)
    logger.info("Step 3: Matching jobs with profile")
    logger.info("="*50)

    for job in jobs[:5]:   # عرض أول 5 وظائف فقط
        query_text = f"{job.title}\n{job.description}\n{job.requirements}"
        similar = embedder.retrieve_similar_chunks(
            query=query_text,
            top_k=5,
            filter_metadata=None   # يمكن تصفية مثلاً: {"source": "cv"}
        )

        if similar:
            avg_score = sum(s["similarity_score"] for s in similar[:3]) / min(3, len(similar))
        else:
            avg_score = 0.0

        print("\n" + "="*70)
        print(f"📌 Job: {job.title} at {job.company}")
        print(f"📍 Location: {job.location}")
        print(f"🔗 Apply: {job.apply_link}")
        print(f"📊 Match Score (avg of top {min(3, len(similar))} similar chunks): {avg_score:.2%}")

        if similar:
            print("🔍 Most similar profile chunks:")
            for i, chunk in enumerate(similar[:2]):
                source = chunk["metadata"].get("source", "unknown")
                if source == "github":
                    repo = chunk["metadata"].get("repo_name", "")
                    print(f"   {i+1}. [{source}] {repo} - similarity: {chunk['similarity_score']:.2%}")
                else:
                    print(f"   {i+1}. [{source}] - similarity: {chunk['similarity_score']:.2%}")
        print("="*70)

    logger.info("Done.")


if __name__ == "__main__":
    main()