# matching/pipeline.py

import concurrent.futures
#import threading
import time
from typing import List, Dict, Any, Optional

from core.logger import logger
from core.ai_client import AIClient, LLMProvider
from matching.scorer import JobScorer, score_job_with_profile


class MatchingPipeline:
    """
    Pipeline المطابقة:
      Stage 1 — Embedding similarity filter  (≥ threshold)
      Stage 2 — LLM scoring                  (Groq → Gemini → Qwen fallback)
      Stage 3 — LLM score filter             (≥ llm_score_threshold)

    الـ AIClient بيتعمل مرة واحدة وبيتشارك بين كل الـ threads —
    هو thread-safe لأن كل request مستقل (requests library).
    """

    def __init__(
        self,
        embedder,
        similarity_threshold: float = 0.50,
        llm_score_threshold: float = 60.0,
        top_k_chunks: int = 10,
        # ─── AIClient params ───
        ai_client: Optional[AIClient] = None,
        groq_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        # ─── Legacy param (متاح للتوافق) ───
        llm_model: Optional[str] = None,
        # ─── Execution params ───
        max_workers: int = 3,       # ممكن نزيد دلوقتي لأن الـ API calls سريعة
        max_retries: int = 2,
        retry_delay: float = 2.0,
    ):
        self.embedder              = embedder
        self.similarity_threshold  = similarity_threshold
        self.llm_score_threshold   = llm_score_threshold
        self.top_k_chunks          = top_k_chunks
        self.max_workers           = max_workers
        self.max_retries           = max_retries
        self.retry_delay           = retry_delay

        if llm_model:
            logger.info(
                f"ℹ️  llm_model='{llm_model}' ignored — "
                f"using AIClient (Groq → Gemini → Qwen)"
            )

        # ─── AIClient مشترك بين كل الـ threads ───
        self._ai_client = ai_client or AIClient(
            groq_api_key=groq_api_key,
            gemini_api_key=gemini_api_key,
            ollama_url=ollama_url,
        )

        # ─── JobScorer واحد بيستخدم نفس الـ client ───
        # الـ scorer نفسه stateless فـ thread-safe
        self._scorer = JobScorer(ai_client=self._ai_client)

    # ─────────────────────────────────────────
    # Embedding Stage
    # ─────────────────────────────────────────

    def _get_embedding_score(self, job_text: str) -> float:
        """حساب درجة التشابه — آمن في threads متعددة."""
        similar = self.embedder.retrieve_similar_chunks(job_text, top_k=3)
        if not similar:
            return 0.0
        return sum(c.get("similarity_score", 0) for c in similar) / len(similar)

    # ─────────────────────────────────────────
    # LLM Stage (with retry)
    # ─────────────────────────────────────────

    def _call_llm_with_retry(self, job: Dict) -> Dict:
        """
        استدعاء الـ LLM مع retry.
        الـ AIClient بيتولى الـ fallback تلقائياً،
        هنا بس بنتعامل مع الـ exceptions الكلية.
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                score_result = score_job_with_profile(
                    job_title=job.get("title", "Unknown"),
                    job_description=job.get("description", ""),
                    job_requirements=job.get("requirements", ""),
                    embedder=self.embedder,
                    scorer=self._scorer,
                    top_k_chunks=self.top_k_chunks,
                )
                return {"success": True, "score_result": score_result}

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"⚠️ Scoring failed for '{job.get('title', 'Unknown')}' "
                        f"(attempt {attempt+1}/{self.max_retries+1}). "
                        f"Retrying in {wait_time:.1f}s... | {e}"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"❌ All scoring attempts failed for '{job.get('title', 'Unknown')}': {e}"
                    )

        return {
            "success": False,
            "error": str(last_error) if last_error else "Unknown error",
        }

    # ─────────────────────────────────────────
    # Single Job Processing
    # ─────────────────────────────────────────

    def _process_single_job(self, job: Dict) -> Dict:
        """معالجة وظيفة واحدة (Stage 1 + Stage 2)."""
        job_text   = f"{job.get('description', '')}\n{job.get('requirements', '')}"
        embed_score = self._get_embedding_score(job_text)
        job["embedding_score"] = embed_score

        # Stage 1: Embedding filter
        if embed_score < self.similarity_threshold:
            return {
                "job": job,
                "stage": "embedding",
                "passed": False,
                "score": embed_score,
                "reason": (
                    f"Embedding score {embed_score:.1%} < {self.similarity_threshold:.0%}"
                ),
            }

        # Stage 2: LLM scoring
        llm_result = self._call_llm_with_retry(job)

        if not llm_result["success"]:
            return {
                "job": job,
                "stage": "llm_error",
                "passed": False,
                "score": 0,
                "reason": f"LLM error: {llm_result['error']}",
            }

        score_result = llm_result["score_result"]
        job.update({
            "llm_score":      score_result.get("score", 0),
            "llm_confidence": score_result.get("confidence", "Medium"),
            "strengths":      score_result.get("strengths", []),
            "gaps":           score_result.get("gaps", []),
            "recommendations": score_result.get("recommendations", []),
            "avg_similarity": score_result.get("avg_similarity", 0),
            "scored_by":      score_result.get("provider_used", "unknown"),  # للتتبع
        })

        return {
            "job": job,
            "stage": "llm",
            "passed": True,
            "score": job["llm_score"],
        }

    # ─────────────────────────────────────────
    # Main Pipeline
    # ─────────────────────────────────────────

    def process_jobs(self, jobs: List[Dict]) -> Dict[str, Any]:
        """
        تشغيل الـ pipeline الكامل على قائمة الوظائف.
        """
        if not jobs:
            logger.warning("No jobs provided")
            return {
                "total_jobs": 0,
                "stage1_passed": [],
                "stage2_scored": [],
                "stage3_final": [],
                "filtered_out": [],
            }

        logger.info(
            f"🚀 Starting matching pipeline for {len(jobs)} jobs "
            f"(max_workers={self.max_workers})"
        )

        # ─── Stage 1 + 2: Parallel processing ───
        results_parallel = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_job = {
                executor.submit(self._process_single_job, job): job
                for job in jobs
            }

            completed = 0
            total     = len(future_to_job)

            for future in concurrent.futures.as_completed(future_to_job):
                completed += 1
                try:
                    result = future.result()
                    results_parallel.append(result)
                except Exception as e:
                    job = future_to_job[future]
                    logger.error(
                        f"❌ Unexpected error for '{job.get('title', 'Unknown')}': {e}"
                    )
                    results_parallel.append({
                        "job": job,
                        "stage": "error",
                        "passed": False,
                        "score": 0,
                        "reason": str(e),
                    })

                if completed % 10 == 0 or completed == total:
                    logger.info(f"📈 Progress: {completed}/{total} jobs processed")

        # ─── Classify results ───
        stage1_passed = []
        stage2_scored = []
        filtered_out  = []

        for result in results_parallel:
            job         = result["job"]
            embed_score = job.get("embedding_score", 0)

            if embed_score >= self.similarity_threshold:
                stage1_passed.append(job)

            if result["passed"] and result["stage"] == "llm":
                stage2_scored.append(job)
            else:
                filtered_out.append({
                    "job":    job,
                    "stage":  result.get("stage", "unknown"),
                    "score":  result.get("score", 0),
                    "reason": result.get("reason", "No specific reason provided"),
                })

        # ─── Stage 3: LLM score filter ───
        stage3_final = []
        for job in stage2_scored:
            score = job.get("llm_score", 0)
            if score >= self.llm_score_threshold:
                stage3_final.append(job)
            else:
                filtered_out.append({
                    "job":    job,
                    "stage":  "llm_score",
                    "score":  score,
                    "reason": f"LLM score {score}% < {self.llm_score_threshold}%",
                })

        # ─── Provider usage stats ───
        provider_stats = self._ai_client.get_stats()

        results = {
            "total_jobs":     len(jobs),
            "stage1_passed":  stage1_passed,
            "stage2_scored":  stage2_scored,
            "stage3_final":   stage3_final,
            "filtered_out":   filtered_out,
            "provider_stats": provider_stats,
        }

        # ─── Summary log ───
        logger.info("=" * 55)
        logger.info("📊 Pipeline Summary:")
        logger.info(f"   Total jobs       : {len(jobs)}")
        logger.info(f"   Stage 1 (embed)  : {len(stage1_passed)} passed")
        logger.info(f"   Stage 2 (LLM)    : {len(stage2_scored)} scored")
        logger.info(f"   Stage 3 (final)  : {len(stage3_final)} qualified")
        logger.info(f"   Filtered out     : {len(filtered_out)}")
        logger.info("   Provider usage:")
        for provider, stats in provider_stats.items():
            if stats["total_calls"] > 0:
                logger.info(
                    f"     {provider:8s}: {stats['total_calls']} calls, "
                    f"{stats['total_failures']} failures"
                )
        logger.info("=" * 55)

        return results