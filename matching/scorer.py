import json
import re
from typing import List, Dict, Any, Optional

from core.logger import logger
from core.ai_client import AIClient, LLMProvider


class JobScorer:
   

    SYSTEM_PROMPT = """You are an expert technical recruiter and career advisor.
Your job is to evaluate how well a candidate's profile (CV + GitHub) matches a job posting,
AND to give a final actionable decision — not just a score.
Be precise, objective, and base your assessment strictly on the provided evidence.

Decision rubric (use judgment, this is guidance not a rigid formula):
- "Apply": strong overall match, gaps (if any) are minor/moderate and wouldn't block doing the job.
- "Improve then apply": decent match, but there's one or more "critical" gaps that are realistically
  learnable in a short time (days to a few weeks) before applying would make sense.
- "Skip": weak match, or critical gaps that are fundamental to the role (e.g. a required language/
  framework with zero evidence), or the role is simply a different domain than the candidate's profile.

Experience-level mismatch is its own gap type, separate from skills, and must be checked explicitly:
compare the years of experience required by the job (if stated) against the candidate's actual total
professional experience (not coursework/personal projects). A large gap (e.g. job wants 5+ years,
candidate has ~1 year) is a "high" impact mismatch and must block "Apply" — even when technical
skills overlap looks strong — because seniority/scope of responsibility cannot be closed by learning
a tool in a few weeks the way a skill gap can.

Always respond with valid JSON only — no markdown, no preamble, no extra text."""

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
        
        model_name: Optional[str] = None,
    ):
        
        self.client = ai_client or AIClient()
        if model_name:
            logger.info(
                f"ℹ️  model_name='{model_name}' ignored — AIClient handles provider selection automatically"
            )

   
    def score_job(
        self,
        job_title: str,
        job_description: str,
        job_requirements: str,
        relevant_chunks: List[Dict],
        
        custom_api_key: Optional[str] = None,
        custom_base_url: Optional[str] = None,
        custom_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        
        if not relevant_chunks:
            logger.warning("No relevant chunks for scoring.")
            return {"score": 0, "error": "No profile chunks"}

        job_text = (
            f"Job Title: {job_title}\n"
            f"Description: {job_description}\n"
            f"Requirements: {job_requirements}"
        )

        
        context_parts = []
        for i, chunk in enumerate(relevant_chunks[:8], 1):
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
2. Experience level and domain relevance — explicitly extract the years of experience the job
   requires (if stated) and the candidate's actual total professional experience, then quantify
   the gap between them (see "experience_gap" in the output)
3. Project evidence from GitHub supporting the role
4. Education / certifications if mentioned
5. Gaps that would prevent performing the job

## Required JSON Output:
IMPORTANT: the example below shows the required FORMAT and field types only. The specific skill
names, numbers, and wording in it (e.g. which skill is missing, how many years) are placeholders
from an unrelated example role — do NOT copy them or let them influence your answer. Every value
you output must come strictly from THIS job's text and THIS candidate's profile snippets above,
never from this example.
{{
  "score": 75,
  "confidence": "Medium",
  "decision": "Improve then apply",
  "explanation": "Strong data-pipeline match, but the missing message-queue experience is a real gap for this role — learnable in about a week.",
  "experience_gap": {{
    "required_years": 5,
    "candidate_years": 1,
    "impact": "high"
  }},
  "strengths": [
    "Strong Go experience with gRPC (3+ projects on GitHub)",
    "Relevant experience with distributed systems"
  ],
  "gaps": [
    {{
      "skill": "Message queue systems (Kafka / RabbitMQ)",
      "severity": "critical",
      "effort_estimate": "1 week",
      "learning_direction": "Build a small producer/consumer demo with RabbitMQ, then read Kafka fundamentals"
    }},
    {{
      "skill": "GraphQL API design",
      "severity": "moderate",
      "effort_estimate": "2-3 weeks",
      "learning_direction": "Convert one existing REST endpoint to GraphQL using a schema-first approach"
    }}
  ],
  "recommendations": [
    "Add a message-queue-based project to GitHub",
    "Highlight any distributed-systems experience in CV"
  ],
  "summary": "Solid backend developer with strong core language skills but lacks certain integration skills required for this role."
}}

Rules:
- score must be an integer 0-100
- confidence must be exactly "Low", "Medium", or "High"
- decision must be exactly "Apply", "Improve then apply", or "Skip"
- explanation is 1-2 sentences, plain language, justifying the decision specifically (not generic)
- strengths, recommendations must be non-empty arrays of strings
- gaps must be an array of objects with: skill, severity ("minor"|"moderate"|"critical"),
  effort_estimate (short string like "2 days", "1 week", "1 month"), learning_direction (concrete, not generic)
- gaps can be an empty array if there are truly no gaps
- experience_gap.required_years and experience_gap.candidate_years must be integers (use 0 if the
  job posting doesn't state a required number of years, or if candidate experience can't be
  determined from the profile)
- IMPORTANT: required_years must come ONLY from an explicit number stated in the job text (e.g.
  "5+ years", "3-5 years experience"). If the job text has no explicit number — even if the title
  says "Senior"/"Lead"/"Junior" — required_years MUST be 0 and experience_gap.impact MUST be "none".
  Do not infer a year count from a seniority label, job title, or general tone of the posting.
- experience_gap.impact must be exactly "none", "low", "medium", or "high", based on how large the
  gap is relative to what's required (e.g. required=5, candidate=1 → "high"; required=3, candidate=2
  → "low"/"medium"; required not stated → "none")
- if experience_gap.impact is "high", decision must NOT be "Apply" — pick "Improve then apply" or
  "Skip" instead, regardless of how strong the skills match is
- Base ONLY on evidence in the provided profile snippets
- Before listing any skill/tool in "gaps" as missing, re-read the candidate profile snippets above
  and check whether that exact skill/tool (or a close synonym) is mentioned anywhere in them. If it
  is mentioned, it is NOT a gap — do not include it, even if the job posting emphasizes it heavily
- Return ONLY the JSON object, nothing else"""

        try:
            response = self.client.complete(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=3500,  
                custom_api_key=custom_api_key,
                custom_base_url=custom_base_url,
                custom_model=custom_model,
            )
            if response.provider_used.value == "gemini":
                logger.debug(f"[gemini raw] len={len(response.text)} | {response.text[:300]!r}")
            parsed = self._parse_score_response(response.text)
            parsed["job_title"]     = job_title
            parsed["chunks_used"]   = len(relevant_chunks)
            parsed["provider_used"] = response.provider_used.value  # للتتبع
            parsed["improvement_plan"] = self._build_improvement_plan(parsed.get("gaps", []))

            # سيفتي نت كودي: لو نص الوظيفة معندوش رقم سنين صريح، منسمحش لـ required_years/impact
            # إنهم يكونوا حاجة تانية غير "0"/"none" — حتى لو الموديل استنتج رقم من العنوان (Senior/Junior).
            if not self._job_text_has_explicit_years(job_text):
                eg = parsed.get("experience_gap") or {}
                eg["required_years"] = 0
                eg["impact"] = "none"
                parsed["experience_gap"] = eg

            avg_sim = 0.0
            if relevant_chunks:
                avg_sim = sum(
                    c.get("similarity_score", 0) for c in relevant_chunks[:3]
                ) / min(3, len(relevant_chunks))
                parsed["avg_similarity"] = avg_sim

            # confidence_factors: مبني على حساب كودي مباشر (مش رأي الموديل عن نفسه) —
            # عشان "confidence: High" يبقى له سبب واضح تقدر تتحقق منه، مش كلام مرسل.
            parsed["confidence_factors"] = self._compute_confidence_factors(
                job_text, context, avg_sim
            )

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

    
    VALID_DECISIONS = ("Apply", "Improve then apply", "Skip")
    VALID_SEVERITIES = ("minor", "moderate", "critical")

    _STOPWORDS = {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
        "at", "by", "from", "as", "is", "are", "was", "were", "be", "been",
        "being", "this", "that", "these", "those", "you", "your", "we", "our",
        "will", "can", "may", "should", "must", "have", "has", "had", "do",
        "does", "did", "not", "no", "yes", "if", "then", "than", "into",
        "out", "about", "over", "under", "between", "within", "across",
        "per", "etc", "all", "any", "who", "what", "when", "where", "how",
        "job", "role", "work", "team", "years", "year", "experience",
    }

    _EXPLICIT_YEARS_RE = re.compile(r"\d+\s*(?:\+|-|to)?\s*\d*\s*\+?\s*years?", re.IGNORECASE)

    @classmethod
    def _job_text_has_explicit_years(cls, job_text: str) -> bool:
        """بيتأكد إن نص الوظيفة فيه رقم سنين مكتوب صراحة (زي '5+ years') —
        مستخدم كـ safety net كودي عشان الموديل ميخمّنش سنين من العنوان (Senior/Junior)."""
        return bool(cls._EXPLICIT_YEARS_RE.search(job_text or ""))

    @classmethod
    def _extract_keywords(cls, text: str) -> set:
        """
        استخراج بسيط للكلمات المفتاحية (skills/tech terms) من نص — من غير أي LLM call.
        ده مش NLP متقدم، ده overlap بسيط زي أدوات ATS، بس بيدي رقم قابل للتحقق
        بدل ما نصدّق "confidence: High" من غير أي سبب واضح.
        """
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}", text or "")
        return {
            t.lower() for t in tokens
            if t.lower() not in cls._STOPWORDS and len(t) >= 3
        }

    @classmethod
    def _compute_confidence_factors(
        cls, job_text: str, profile_context: str, avg_similarity: float
    ) -> Dict[str, Any]:
        
        job_keywords     = cls._extract_keywords(job_text)
        profile_keywords = cls._extract_keywords(profile_context)
        matched           = job_keywords & profile_keywords

        coverage_pct = (
            round(100 * len(matched) / len(job_keywords), 1) if job_keywords else 0.0
        )

        return {
            "avg_similarity": round(avg_similarity, 3),
            "matched_keywords_count": len(matched),
            "job_keywords_total": len(job_keywords),
            "keyword_coverage_pct": coverage_pct,
        }

    @classmethod
    def _parse_score_response(cls, text: str) -> Dict[str, Any]:
        """
        تحليل رد الـ LLM مع دعم الـ partial JSON المقطوع.
        بيرجع dict كامل ومطبّع (normalized) حتى لو الموديل رجع حاجة ناقصة.
        """
        data: Optional[Dict[str, Any]] = None

       
        cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

        
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        
        if data is None:
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except (json.JSONDecodeError, ValueError):
                    pass

        
        if data is None and "{" in cleaned:
            partial = cleaned[cleaned.index("{"):]
            data = {"_truncated": True}

            score_match = re.search(r'"score"\s*:\s*(\d{1,3})', partial)
            if score_match:
                data["score"] = int(score_match.group(1))

            conf_match = re.search(r'"confidence"\s*:\s*"(Low|Medium|High)"', partial, re.IGNORECASE)
            if conf_match:
                data["confidence"] = conf_match.group(1)

            dec_match = re.search(
                r'"decision"\s*:\s*"(Apply|Improve then apply|Skip)"', partial, re.IGNORECASE
            )
            if dec_match:
                data["decision"] = dec_match.group(1)

            expl_match = re.search(r'"explanation"\s*:\s*"([^"]*)"', partial)
            if expl_match:
                data["explanation"] = expl_match.group(1)

            def _extract_str_array(key: str) -> List[str]:
                pat = rf'"{key}"\s*:\s*\[(.*?)(?:\]|$)'
                m = re.search(pat, partial, re.DOTALL)
                if m:
                    return re.findall(r'"([^"]+)"', m.group(1))
                return []

            def _extract_gap_objects(key: str) -> List[dict]:
                # gaps شكلها array of objects مش نصوص بسيطة — لازم استخراج مختلف
                # عشان مانفقدش الـ gaps وقت الـ JSON التالف/المقطوع
                pat = rf'"{key}"\s*:\s*\[(.*?)(?:\]\s*,\s*"|\]\s*\}}|$)'
                m = re.search(pat, partial, re.DOTALL)
                if not m:
                    return []
                section = m.group(1)
                obj_strings = re.findall(r'\{[^{}]*\}', section, re.DOTALL)
                result = []
                for obj_str in obj_strings:
                    skill_m = re.search(r'"skill"\s*:\s*"([^"]*)"', obj_str)
                    if not skill_m:
                        continue
                    sev_m = re.search(r'"severity"\s*:\s*"([^"]*)"', obj_str)
                    effort_m = re.search(r'"effort_estimate"\s*:\s*"([^"]*)"', obj_str)
                    result.append({
                        "skill": skill_m.group(1),
                        "severity": sev_m.group(1) if sev_m else "unknown",
                        "effort_estimate": effort_m.group(1) if effort_m else "",
                    })
                return result

            data["strengths"]       = _extract_str_array("strengths")
            data["gaps"]            = _extract_gap_objects("gaps")
            data["recommendations"] = _extract_str_array("recommendations")

            if data.get("score"):
                logger.warning(
                    f"⚠️ Partial JSON recovered: score={data.get('score')} "
                    f"strengths={len(data['strengths'])} gaps={len(data['gaps'])}"
                )
            else:
                data = None  

        
        if data is None:
            logger.warning("⚠️ Could not parse JSON response, falling back to regex extraction")
            data = {"strengths": [], "gaps": [], "recommendations": [], "summary": text[:500]}
            score_match = re.search(r'"?score"?\s*[:=]\s*(\d{1,3})', text, re.IGNORECASE)
            if score_match:
                data["score"] = int(score_match.group(1))
            conf_match = re.search(r'"?confidence"?\s*[:=]\s*"?(Low|Medium|High)"?', text, re.IGNORECASE)
            if conf_match:
                data["confidence"] = conf_match.group(1).capitalize()

        return cls._normalize_result(data)

    @classmethod
    def _normalize_result(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        
        data["score"] = max(0, min(100, int(data.get("score", 0) or 0)))

        conf = data.get("confidence", "Medium")
        data["confidence"] = conf if conf in ("Low", "Medium", "High") else "Medium"

        data["strengths"]       = data.get("strengths") or []
        data["recommendations"] = data.get("recommendations") or []
        data["gaps"]            = cls._normalize_gaps(data.get("gaps") or [])
        data["experience_gap"]  = cls._normalize_experience_gap(data.get("experience_gap"))

        decision = data.get("decision")
        has_critical = any(g.get("severity") == "critical" for g in data["gaps"])
        high_exp_gap = data["experience_gap"]["impact"] == "high"
        if decision not in cls.VALID_DECISIONS:
            data["decision"] = cls._fallback_decision(data["score"], data["confidence"], has_critical or high_exp_gap)
            data["_decision_was_computed_locally"] = True
        else:
            data["decision"] = decision
            
            if data["decision"] == "Skip" and data["score"] >= 70 and not has_critical:
                data["_flagged_for_review"] = True
            elif data["decision"] == "Apply" and (data["score"] < 40 or has_critical):
                data["_flagged_for_review"] = True

        # سيفتي نت: لو الموديل قال "Apply" رغم إن experience_gap.impact = "high"،
        # ننزّل القرار — الـ rubric بيقول ده ممنوع، بس بنتأكد كوديًا لو الموديل اتجاهل التعليمة.
        if high_exp_gap and data["decision"] == "Apply":
            data["decision"] = "Improve then apply"
            data["_decision_downgraded_experience_gap"] = True

        if not data.get("explanation"):
            data["explanation"] = cls._fallback_explanation(
                data["decision"], data["score"], data["gaps"]
            )

        data.setdefault("summary", "")
        return data

    @staticmethod
    def _normalize_gaps(raw_gaps: List[Any]) -> List[Dict[str, str]]:
        
        normalized = []
        for g in raw_gaps:
            if isinstance(g, dict):
                severity = g.get("severity", "moderate")
                if severity not in JobScorer.VALID_SEVERITIES:
                    severity = "moderate"
                normalized.append({
                    "skill": g.get("skill", g.get("name", "Unspecified gap")),
                    "severity": severity,
                    "effort_estimate": g.get("effort_estimate", "unknown"),
                    "learning_direction": g.get("learning_direction", ""),
                })
            elif isinstance(g, str):
                
                normalized.append({
                    "skill": g,
                    "severity": "moderate",
                    "effort_estimate": "unknown",
                    "learning_direction": "",
                })
        return normalized

    VALID_IMPACTS = ("none", "low", "medium", "high")

    @staticmethod
    def _normalize_experience_gap(raw: Any) -> Dict[str, Any]:
        
        if not isinstance(raw, dict):
            return {"required_years": 0, "candidate_years": 0, "impact": "none"}

        def _to_int(v):
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                return 0

        impact = raw.get("impact", "none")
        if impact not in JobScorer.VALID_IMPACTS:
            impact = "none"

        return {
            "required_years": _to_int(raw.get("required_years", 0)),
            "candidate_years": _to_int(raw.get("candidate_years", 0)),
            "impact": impact,
        }

    @staticmethod
    def _fallback_decision(score: int, confidence: str, has_critical_gap: bool) -> str:
        
        if score >= 75 and confidence != "Low" and not has_critical_gap:
            return "Apply"
        if score >= 45 and not (has_critical_gap and confidence == "Low"):
            return "Improve then apply"
        return "Skip"

    @staticmethod
    def _fallback_explanation(decision: str, score: int, gaps: List[Dict]) -> str:
        
        critical = [g["skill"] for g in gaps if g.get("severity") == "critical"]
        if decision == "Apply":
            return f"Score of {score}% with no blocking gaps — strong enough match to apply now."
        if decision == "Improve then apply":
            if critical:
                return f"Score of {score}%, but {', '.join(critical)} should be addressed first."
            return f"Score of {score}% — a reasonable match worth strengthening before applying."
        return f"Score of {score}% with significant gaps — not a strong match for this role right now."

    @staticmethod
    def _build_improvement_plan(gaps: List[Dict]) -> List[Dict[str, str]]:
        """بيحول الـ gaps لخطة تحسين مرتبة بالأولوية — من غير أي LLM call إضافي."""
        severity_rank = {"critical": 0, "moderate": 1, "minor": 2}
        sorted_gaps = sorted(gaps, key=lambda g: severity_rank.get(g.get("severity", "moderate"), 1))
        return [
            {
                "skill": g["skill"],
                "priority": g.get("severity", "moderate"),
                "estimated_effort": g.get("effort_estimate", "unknown"),
                "learning_direction": g.get("learning_direction", ""),
            }
            for g in sorted_gaps
        ]




def score_job_with_profile(
    job_title: str,
    job_description: str,
    job_requirements: str,
    embedder,
    scorer: JobScorer,
    top_k_chunks: int = 5,
    custom_api_key: Optional[str] = None,
    custom_base_url: Optional[str] = None,
    custom_model: Optional[str] = None,
    use_cache: bool = True,
    cache_scope: Optional[str] = None,   
) -> Dict[str, Any]:
   
    from core.cache import ScoreCache  

    cache = ScoreCache() if use_cache else None
    cache_key = None
    if cache:
        cache_key = ScoreCache.make_key(job_title, job_description, job_requirements, cache_scope or "")
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"💾 Cache hit — skipping LLM call for '{job_title}'")
            cached["from_cache"] = True
            return cached

    query_text = f"{job_description}\n{job_requirements}"
    job_text_for_keywords = f"{job_title}\n{job_description}\n{job_requirements}"

    # hybrid retrieval: dense (semantic) top_k_chunks + أي chunk بيحتوي على مهارة/أداة
    # مذكورة صراحة في الوظيفة ومش طالعة أصلاً في نتائج الـ dense — عشان مانخسرش
    # معلومة حقيقية (زي Docker) لمجرد إن ترتيبها الدلالي طلع برا أول top_k_chunks،
    # من غير ما نزوّد عدد الـ chunks لكل الوظايف بشكل ثابت (توفير توكنز).
    if hasattr(embedder, "retrieve_hybrid_chunks"):
        similar = embedder.retrieve_hybrid_chunks(
            query_text,
            job_text=job_text_for_keywords,
            dense_top_k=top_k_chunks,
            max_keyword_extra=3,
        )
    else:
        similar = embedder.retrieve_similar_chunks(query_text, top_k=top_k_chunks)

    if not similar:
        logger.warning(f"No similar chunks for job: {job_title}")
        return {"score": 0, "error": "No profile matches"}

    result = scorer.score_job(
        job_title, job_description, job_requirements, similar,
        custom_api_key=custom_api_key,
        custom_base_url=custom_base_url,
        custom_model=custom_model,
    )
    result["similar_chunks"] = similar[:3]

    if cache and cache_key and not result.get("error"):
        cache.set(cache_key, result)

    return result