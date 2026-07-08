# matching/scorer.py

import json
import re
from typing import List, Dict, Any, Optional

from core.logger import logger
from core.ai_client import AIClient, LLMProvider


class JobScorer:
    """
    يقيّم مدى تطابق الوظيفة مع الـ profile.

    يستخدم AIClient اللي بيتنقل تلقائياً:
        Groq (Llama 3.3 70B) → Gemini Flash → Qwen 2.5 (Ollama)
    """

    SYSTEM_PROMPT = """You are an expert technical recruiter and career advisor.
Your job is to evaluate how well a candidate's profile (CV + GitHub) matches a job posting.
Be precise, objective, and base your assessment strictly on the provided evidence.
Always respond with valid JSON only — no markdown, no preamble, no extra text."""

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
        # للتوافق مع الكود القديم اللي بيبعت model_name
        model_name: Optional[str] = None,
    ):
        # لو مفيش client جاهز، ننشئ واحد تلقائياً من env vars
        self.client = ai_client or AIClient()
        if model_name:
            logger.info(
                f"ℹ️  model_name='{model_name}' ignored — AIClient handles provider selection automatically"
            )

    # ─────────────────────────────────────────
    # Scoring
    # ─────────────────────────────────────────

    def score_job(
        self,
        job_title: str,
        job_description: str,
        job_requirements: str,
        relevant_chunks: List[Dict],
    ) -> Dict[str, Any]:
        """
        تقييم الوظيفة مع الـ profile chunks.
        بيرجع dict فيه score, confidence, strengths, gaps, recommendations.
        """
        if not relevant_chunks:
            logger.warning("No relevant chunks for scoring.")
            return {"score": 0, "error": "No profile chunks"}

        job_text = (
            f"Job Title: {job_title}\n"
            f"Description: {job_description}\n"
            f"Requirements: {job_requirements}"
        )

        # بناء الـ context من أفضل 5 chunks
        context_parts = []
        for i, chunk in enumerate(relevant_chunks[:5], 1):
            source = chunk.get("metadata", {}).get("source", "unknown")
            repo   = chunk.get("metadata", {}).get("repo_name", "")
            label  = f"{source} ({repo})" if repo else source
            text   = chunk.get("text", "")[:600]
            context_parts.append(f"[Source {i}: {label}]\n{text}")
        context = "\n\n---\n\n".join(context_parts)

        prompt = f"""Evaluate how well the candidate's profile matches this job.

## Job Details:
{job_text}

## Candidate Profile (most relevant snippets from CV and GitHub):
{context}

## Evaluation Criteria:
1. Technical skills alignment (programming languages, frameworks, tools)
2. Experience level and domain relevance
3. Project evidence from GitHub supporting the role
4. Education / certifications if mentioned
5. Gaps that would prevent performing the job

## Required JSON Output:
{{
  "score": 75,
  "confidence": "Medium",
  "strengths": [
    "Strong Python experience with FastAPI (3+ projects on GitHub)",
    "Relevant experience with async programming"
  ],
  "gaps": [
    "No Docker/containerization evidence",
    "Missing cloud platform experience (AWS/GCP/Azure)"
  ],
  "recommendations": [
    "Add a Dockerized project to GitHub",
    "Highlight any deployment experience in CV"
  ],
  "summary": "Solid backend developer with Python expertise but lacks DevOps/cloud skills required for this role."
}}

Rules:
- score must be an integer 0-100
- confidence must be exactly "Low", "Medium", or "High"
- strengths, gaps, recommendations must be non-empty arrays of strings
- Base ONLY on evidence in the provided profile snippets
- Return ONLY the JSON object, nothing else"""

        try:
            response = self.client.complete(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                temperature=0.15,
                max_tokens=3000,  # كافي لـ JSON كامل حتى مع jobs كبيرة
            )
            if response.provider_used.value == "gemini":
                logger.debug(f"[gemini raw] len={len(response.text)} | {response.text[:300]!r}")
            parsed = self._parse_score_response(response.text)
            parsed["job_title"]     = job_title
            parsed["chunks_used"]   = len(relevant_chunks)
            parsed["provider_used"] = response.provider_used.value  # للتتبع

            if relevant_chunks:
                avg_sim = sum(
                    c.get("similarity_score", 0) for c in relevant_chunks[:3]
                ) / min(3, len(relevant_chunks))
                parsed["avg_similarity"] = avg_sim

            logger.info(
                f"📊 Score: {parsed.get('score', 0)}% "
                f"[{response.provider_used.value}] "
                f"confidence={parsed.get('confidence', '?')} "
                f"| {job_title}"
            )
            return parsed

        except Exception as e:
            logger.error(f"❌ Scoring failed for '{job_title}': {e}")
            return {"score": 0, "error": str(e), "job_title": job_title}

    # ─────────────────────────────────────────
    # Parsing
    # ─────────────────────────────────────────

    @staticmethod
    def _parse_score_response(text: str) -> Dict[str, Any]:
        """
        تحليل رد الـ LLM مع دعم الـ partial JSON المقطوع.
        """
        # 1. إزالة ``` code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

        # 2. محاولة JSON مباشرة
        try:
            data = json.loads(cleaned)
            data["score"] = max(0, min(100, int(data.get("score", 0))))
            conf = data.get("confidence", "Medium")
            data["confidence"] = conf if conf in ("Low", "Medium", "High") else "Medium"
            return data
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # 3. استخراج JSON من وسط النص
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                data["score"] = max(0, min(100, int(data.get("score", 0))))
                return data
            except (json.JSONDecodeError, ValueError):
                pass

        # 4. الـ JSON مقطوع — نحاول نكمّله
        # بنجيب كل اللي فيه قبل التقطيع
        if "{" in cleaned:
            partial = cleaned[cleaned.index("{"):]
            # استخراج الـ score على الأقل
            result: Dict[str, Any] = {
                "score": 0,
                "confidence": "Medium",
                "strengths": [],
                "gaps": [],
                "recommendations": [],
                "summary": "",
                "_truncated": True,
            }

            score_match = re.search(r'"score"\s*:\s*(\d{1,3})', partial)
            if score_match:
                result["score"] = max(0, min(100, int(score_match.group(1))))

            conf_match = re.search(r'"confidence"\s*:\s*"(Low|Medium|High)"', partial, re.IGNORECASE)
            if conf_match:
                result["confidence"] = conf_match.group(1)

            def _extract_array(key: str) -> List[str]:
                # يجيب العناصر الكاملة حتى لو الـ array مقطوع
                pat = rf'"{key}"\s*:\s*\[(.*?)(?:\]|$)'
                m = re.search(pat, partial, re.DOTALL)
                if m:
                    return re.findall(r'"([^"]+)"', m.group(1))
                return []

            result["strengths"]       = _extract_array("strengths")
            result["gaps"]            = _extract_array("gaps")
            result["recommendations"] = _extract_array("recommendations")

            if result["score"] > 0:
                logger.warning(
                    f"⚠️ Partial JSON recovered: score={result['score']} "
                    f"strengths={len(result['strengths'])} gaps={len(result['gaps'])}"
                )
                return result

        # 5. فشل كل حاجة
        logger.warning("⚠️ Could not parse JSON response, falling back to regex extraction")
        result = {
            "score": 0,
            "confidence": "Low",
            "strengths": [],
            "gaps": [],
            "recommendations": [],
            "summary": text[:500],
        }
        score_match = re.search(r'"?score"?\s*[:=]\s*(\d{1,3})', text, re.IGNORECASE)
        if score_match:
            result["score"] = max(0, min(100, int(score_match.group(1))))
        conf_match = re.search(r'"?confidence"?\s*[:=]\s*"?(Low|Medium|High)"?', text, re.IGNORECASE)
        if conf_match:
            result["confidence"] = conf_match.group(1).capitalize()
        return result


# ─────────────────────────────────────────────
# Helper function (للاستخدام في pipeline)
# ─────────────────────────────────────────────

def score_job_with_profile(
    job_title: str,
    job_description: str,
    job_requirements: str,
    embedder,
    scorer: JobScorer,
    top_k_chunks: int = 10,
) -> Dict[str, Any]:
    """دالة مساعدة تجمع الـ retrieval والـ scoring في خطوة واحدة."""
    query_text = f"{job_description}\n{job_requirements}"
    similar = embedder.retrieve_similar_chunks(query_text, top_k=top_k_chunks)

    if not similar:
        logger.warning(f"No similar chunks for job: {job_title}")
        return {"score": 0, "error": "No profile matches"}

    result = scorer.score_job(job_title, job_description, job_requirements, similar)
    result["similar_chunks"] = similar[:3]
    return result