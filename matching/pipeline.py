import concurrent.futures
#import threading
import time
from typing import List, Dict, Any, Optional

from core.logger import logger
from core.ai_client import AIClient, LLMProvider
from matching.scorer import JobScorer, score_job_with_profile


class MatchingPipeline:
  

    def __init__(
        self,
        embedder,
        similarity_threshold: float = 0.50,
        llm_score_threshold: float = 60.0,
        top_k_chunks: int = 10,
       
        ai_client: Optional[AIClient] = None,
        groq_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        
        llm_model: Optional[str] = None,
        max_workers: int = 3,       
        max_retries: int = 2,
        retry_delay: float = 2.0,
        
        cache_scope: Optional[str] = None,

        # BYOK — لو المستخدم مدّى مفتاح خاص بيه من الـ settings، الـ pipeline
        # تستخدمه بدل الـ chain الافتراضي (Groq → Gemini → Qwen) لكل الوظائف
        # اللي بتتقيّم في البحث التلقائي، مش بس في التحليل اليدوي.
        custom_api_key: Optional[str] = None,
        custom_base_url: Optional[str] = None,
        custom_model: Optional[str] = None,
    ):
        self.embedder              = embedder
        self.similarity_threshold  = similarity_threshold
        self.llm_score_threshold   = llm_score_threshold
        self.top_k_chunks          = top_k_chunks
        self.max_workers           = max_workers
        self.max_retries           = max_retries
        self.retry_delay           = retry_delay
        self.cache_scope           = cache_scope
        self.custom_api_key        = custom_api_key
        self.custom_base_url       = custom_base_url
        self.custom_model          = custom_model

        if llm_model:
            logger.info(
                f"ℹ️  llm_model='{llm_model}' ignored — "
                f"using AIClient (Groq → Gemini → Qwen)"
            )

       
        self._ai_client = ai_client or AIClient(
            groq_api_key=groq_api_key,
            gemini_api_key=gemini_api_key,
            ollama_url=ollama_url,
        )

        self._scorer = JobScorer(ai_client=self._ai_client)

  

    def _get_embedding_score(self, job_text: str) -> float:
        
        similar = self.embedder.retrieve_similar_chunks(job_text, top_k=3)
        if not similar:
            return 0.0
        return sum(c.get("similarity_score", 0) for c in similar) / len(similar)

   

    def _call_llm_with_retry(self, job: Dict) -> Dict:
       
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
                    cache_scope=self.cache_scope,
                    custom_api_key=self.custom_api_key,
                    custom_base_url=self.custom_base_url,
                    custom_model=self.custom_model,
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

   

    def _process_single_job(self, job: Dict) -> Dict:
        
        job_text   = f"{job.get('description', '')}\n{job.get('requirements', '')}"
        embed_score = self._get_embedding_score(job_text)
        job["embedding_score"] = embed_score

        
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
            "llm_score":        score_result.get("score", 0),
            "llm_confidence":   score_result.get("confidence", "Medium"),
            "decision":         score_result.get("decision", "Skip"),
            "explanation":      score_result.get("explanation", ""),
            "strengths":        score_result.get("strengths", []),
            "gaps":             score_result.get("gaps", []),
            "improvement_plan": score_result.get("improvement_plan", []),
            "recommendations":  score_result.get("recommendations", []),
            "avg_similarity":   score_result.get("avg_similarity", 0),
            "experience_gap":   score_result.get("experience_gap", {"required_years": 0, "candidate_years": 0, "impact": "none"}),
            "confidence_factors": score_result.get("confidence_factors", {}),
            "scored_by":        score_result.get("provider_used", "unknown"),  
            "from_cache":       score_result.get("from_cache", False),
        })

        return {
            "job": job,
            "stage": "llm",
            "passed": True,
            "score": job["llm_score"],
        }

    
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

       
        provider_stats = self._ai_client.get_stats()

        results = {
            "total_jobs":     len(jobs),
            "stage1_passed":  stage1_passed,
            "stage2_scored":  stage2_scored,
            "stage3_final":   stage3_final,
            "filtered_out":   filtered_out,
            "provider_stats": provider_stats,
        }

        
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