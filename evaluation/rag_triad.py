
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.ai_client import AIClient
from core.logger import logger




@dataclass
class ChunkRelevance:
    chunk_index: int
    source: str
    relevance_score: float         
    reasoning: str


@dataclass
class ContextRelevanceResult:
    chunk_scores: List[ChunkRelevance] = field(default_factory=list)
    overall_score: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "chunk_scores": [
                {
                    "chunk_index": c.chunk_index,
                    "source": c.source,
                    "relevance_score": round(c.relevance_score, 3),
                    "reasoning": c.reasoning,
                }
                for c in self.chunk_scores
            ],
            "note": self.note,
        }


@dataclass
class ClaimVerdict:
    claim: str
    verdict: str        
    reasoning: str


@dataclass
class GroundednessResult:
    claims: List[ClaimVerdict] = field(default_factory=list)
    overall_score: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "claims": [
                {"claim": c.claim, "verdict": c.verdict, "reasoning": c.reasoning}
                for c in self.claims
            ],
            "note": self.note,
        }

    @property
    def unsupported_claims(self) -> List[ClaimVerdict]:
        return [c for c in self.claims if c.verdict == "unsupported"]


@dataclass
class AnswerRelevanceResult:
    overall_score: float = 0.0
    reasoning: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "reasoning": self.reasoning,
            "note": self.note,
        }


@dataclass
class RAGTriadResult:
    job_title: str
    context_relevance: ContextRelevanceResult
    groundedness: GroundednessResult
    answer_relevance: AnswerRelevanceResult
    thresholds: Dict[str, float]

    @property
    def average_score(self) -> float:
        return round(
            (
                self.context_relevance.overall_score
                + self.groundedness.overall_score
                + self.answer_relevance.overall_score
            )
            / 3,
            3,
        )

    @property
    def weakest_score(self) -> float:
        
        return round(
            min(
                self.context_relevance.overall_score,
                self.groundedness.overall_score,
                self.answer_relevance.overall_score,
            ),
            3,
        )

    @property
    def passed(self) -> bool:
        return (
            self.context_relevance.overall_score >= self.thresholds["context_relevance"]
            and self.groundedness.overall_score >= self.thresholds["groundedness"]
            and self.answer_relevance.overall_score >= self.thresholds["answer_relevance"]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_title": self.job_title,
            "context_relevance": self.context_relevance.to_dict(),
            "groundedness": self.groundedness.to_dict(),
            "answer_relevance": self.answer_relevance.to_dict(),
            "average_score": self.average_score,
            "weakest_score": self.weakest_score,
            "passed": self.passed,
            "thresholds": self.thresholds,
        }



class RAGTriadJudge:
   
    JUDGE_SYSTEM_PROMPT = (
        "You are a strict, impartial evaluator of a Retrieval-Augmented "
        "Generation (RAG) system. You are not being asked to solve the "
        "underlying task — only to judge, with evidence, how well a "
        "retrieval or generation step performed. Be skeptical: prefer "
        "flagging something as unsupported/irrelevant over giving the "
        "benefit of the doubt. Always respond with valid JSON only — no "
        "markdown, no preamble, no extra text."
    )

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
      
        context_relevance_threshold: float = 0.5,
        groundedness_threshold: float = 0.7,
        answer_relevance_threshold: float = 0.7,
        judge_temperature: float = 0.0,
        judge_model: Optional[str] = None,
    ):
        self.client = ai_client or AIClient()
        self.thresholds = {
            "context_relevance": context_relevance_threshold,
            "groundedness": groundedness_threshold,
            "answer_relevance": answer_relevance_threshold,
        }
        self.judge_temperature = judge_temperature
       
        self.judge_model = judge_model

    

    def _judge_call(self, prompt: str, max_tokens: int = 2000) -> Optional[Dict[str, Any]]:
        for attempt in range(2): 
            current_prompt = prompt
            if attempt == 1:
                current_prompt = (
                    prompt
                    + "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
                    "Respond with ONLY the raw JSON object — no markdown fences, no prose "
                    "before or after it."
                )
            try:
                response = self.client.complete(
                    prompt=current_prompt,
                    system_prompt=self.JUDGE_SYSTEM_PROMPT,
                    temperature=self.judge_temperature,
                    max_tokens=max_tokens,
                    custom_model=self.judge_model,
                )
            except Exception as e:
                logger.error(f"❌ RAG Triad judge call failed: {e}")
                return None

            data = self._extract_json(response.text)
            if data is not None:
                return data
            if attempt == 0:
                snippet = response.text[:200].replace("\n", " ")
                logger.warning(f"⚠️ RAG Triad judge: JSON parse failed, retrying once. Raw response: {snippet!r}")

        return None

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        start = cleaned.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(cleaned)):
                if cleaned[i] == "{":
                    depth += 1
                elif cleaned[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = cleaned[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except (json.JSONDecodeError, ValueError):
                            break

        return None

  

    def evaluate_context_relevance(
        self, query: str, contexts: List[Dict]
    ) -> ContextRelevanceResult:
        if not contexts:
            return ContextRelevanceResult(
                chunk_scores=[], overall_score=0.0, note="No chunks were retrieved."
            )

        chunk_blocks = []
        for i, c in enumerate(contexts, 1):
            source = c.get("metadata", {}).get("source", "unknown")
            text = c.get("text", "")[:800]
            chunk_blocks.append(f"[Chunk {i} | source={source}]\n{text}")
        chunks_text = "\n\n---\n\n".join(chunk_blocks)

        prompt = f"""Evaluate the RELEVANCE of each retrieved chunk to the query below.
This is a job-matching system: the query is a job posting, and the chunks are
snippets retrieved from a candidate's CV/GitHub profile via vector similarity.
A chunk is relevant if a recruiter could plausibly use it to argue for or
against this candidate for THIS job — not just topically similar text.

## Query (job posting):
{query[:2000]}

## Retrieved chunks:
{chunks_text}

Score each chunk 0-10 (0 = totally irrelevant noise, 10 = directly on-point
evidence for this specific job). Then give an overall_score 0.0-1.0 that
reflects what fraction of the retrieved chunks were actually useful (not
just the average — a batch full of irrelevant chunks should score low even
if one chunk is excellent).

Respond with ONLY this JSON shape:
{{
  "chunk_scores": [
    {{"chunk_index": 1, "relevance_score": 8, "reasoning": "one sentence"}},
    {{"chunk_index": 2, "relevance_score": 2, "reasoning": "one sentence"}}
  ],
  "overall_score": 0.65
}}"""

        data = self._judge_call(prompt)
        if data is None:
            return ContextRelevanceResult(
                chunk_scores=[], overall_score=0.0, note="Judge call failed or returned invalid JSON."
            )

        chunk_scores = []
        for cs in data.get("chunk_scores", []):
            try:
                idx = int(cs.get("chunk_index", 0))
                source = contexts[idx - 1].get("metadata", {}).get("source", "unknown") if 0 < idx <= len(contexts) else "unknown"
                chunk_scores.append(
                    ChunkRelevance(
                        chunk_index=idx,
                        source=source,
                        relevance_score=max(0.0, min(1.0, float(cs.get("relevance_score", 0)) / 10)),
                        reasoning=str(cs.get("reasoning", "")),
                    )
                )
            except (ValueError, TypeError):
                continue

      
        overall = (
            sum(c.relevance_score for c in chunk_scores) / len(chunk_scores)
            if chunk_scores else 0.0
        )
        overall = max(0.0, min(1.0, overall))

        return ContextRelevanceResult(chunk_scores=chunk_scores, overall_score=overall)

   

    def evaluate_groundedness(
        self, contexts: List[Dict], response: Dict[str, Any], job_query: str = ""
    ) -> GroundednessResult:
        if not contexts:
            return GroundednessResult(
                claims=[], overall_score=0.0, note="No context to ground claims in."
            )

        context_text = "\n\n---\n\n".join(
            c.get("text", "")[:800] for c in contexts
        )

      
        positive_claims_source = {
            "explanation": response.get("explanation", ""),
            "strengths": response.get("strengths", []),
            "summary": response.get("summary", ""),
        }
        gap_skills = [
            g.get("skill", "") for g in response.get("gaps", []) if isinstance(g, dict) and g.get("skill")
        ]

        prompt = f"""You are checking a job-match assessment for HALLUCINATION
about the CANDIDATE specifically (not about the job/role itself).

## Job posting (for reference only — statements sourced from THIS, describing
what the role/job requires or is about, are legitimate and NOT hallucination,
even though they won't appear in the candidate context below):
{job_query[:1500]}

## Retrieved candidate profile context (the ONLY evidence allowed for claims
ABOUT THE CANDIDATE):
{context_text}

## Assessment produced by the system (POSITIVE claims to verify — strengths,
explanation, summary — these assert the candidate DOES have something):
{json.dumps(positive_claims_source, ensure_ascii=False, indent=2)}

Break this down into individual factual claims. First classify each claim as
one of:
- a POSITIVE claim ABOUT THE CANDIDATE (asserts they DO have/demonstrate some
  skill or experience) — verify this against the candidate profile context
  ONLY; needs direct textual support to be "supported".
- a NEGATIVE/ABSENCE claim ABOUT THE CANDIDATE (asserts they do NOT have,
  lack, or fail to demonstrate some skill — e.g. "does not demonstrate
  Kubernetes experience", "lacks senior AWS expertise") — this is like a gap
  claim. It is "supported" simply by the skill being ABSENT from the
  context (the normal case for a real gap). Only mark it "unsupported" if
  the context DIRECTLY CONTRADICTS it by showing the candidate clearly DOES
  have that exact skill. Do NOT demand "positive evidence of an absence" —
  that is not a coherent verification, and doing so unfairly penalizes
  entirely correct gap statements just because they're phrased as prose
  instead of a structured list.
- a claim ABOUT THE ROLE/JOB (what it requires, what would be needed, opinions
  about job fit criteria) — these are grounded in the job posting above, not
  the candidate context, so do NOT mark them unsupported just because the
  candidate context doesn't mention them. Only mark a role/job claim
  unsupported if it also isn't backed by the job posting text.

For each claim, verdict must be exactly one of:
- "supported": directly backed by its relevant source (candidate context for
  candidate claims, job posting for role claims)
- "partially_supported": related to the source but with extrapolation not
  explicitly stated
- "unsupported": no evidence for this in the relevant source at all

Then give overall_score 0.0-1.0 = (supported + 0.5*partially_supported) / total_claims.

Respond with ONLY this JSON shape. The claim text below is a PLACEHOLDER
showing the format only — replace it entirely with the actual claims you
extracted from THIS assessment. Never copy the placeholder text verbatim:
{{
  "claims": [
    {{"claim": "<replace with an actual extracted claim>", "verdict": "supported", "reasoning": "<why>"}},
    {{"claim": "<replace with another actual extracted claim>", "verdict": "unsupported", "reasoning": "<why>"}}
  ],
  "overall_score": 0.75
}}"""

        data = self._judge_call(prompt, max_tokens=3000)
        if data is None:
            claims = []
        else:
            claims = [
                ClaimVerdict(
                    claim=str(c.get("claim", "")),
                    verdict=c.get("verdict") if c.get("verdict") in ("supported", "partially_supported", "unsupported") else "unsupported",
                    reasoning=str(c.get("reasoning", "")),
                )
                for c in data.get("claims", [])
            ]

        if gap_skills:
            claims.extend(self._verify_gaps_not_contradicted(context_text, gap_skills))

        if not claims:
            return GroundednessResult(
                claims=[], overall_score=0.0, note="Judge call failed or returned invalid JSON."
            )

        weight = {"supported": 1.0, "partially_supported": 0.5, "unsupported": 0.0}
        overall = sum(weight[c.verdict] for c in claims) / len(claims)
        overall = max(0.0, min(1.0, overall))

        return GroundednessResult(claims=claims, overall_score=overall)

    def _verify_gaps_not_contradicted(
        self, context_text: str, gap_skills: List[str]
    ) -> List[ClaimVerdict]:
      
        prompt = f"""The system claims the candidate is MISSING (has a gap in) each
of these skills. For each skill, answer a direct yes/no question:

    "Does the context below PROVE the candidate has hands-on experience
    with this exact skill?"

Simply not mentioning the skill means the answer is NO (the gap claim is
correct) — that is the expected, normal case. Only answer YES if the context
explicitly demonstrates hands-on experience with that exact skill.

## Candidate profile context:
{context_text}

## Skills claimed as gaps (missing):
{json.dumps(gap_skills, ensure_ascii=False)}

Respond with ONLY this JSON shape (candidate_has_skill is a boolean). The
skill names below are PLACEHOLDERS for format only — respond using the
actual skills listed above under "Skills claimed as gaps", not these names:
{{
  "claims": [
    {{"claim": "<one of the actual skills listed above>", "candidate_has_skill": false, "reasoning": "<why>"}},
    {{"claim": "<another of the actual skills listed above>", "candidate_has_skill": true, "reasoning": "<why>"}}
  ]
}}"""
        data = self._judge_call(prompt, max_tokens=1500)
        if data is None:
            return [
                ClaimVerdict(claim=f"gap: {s}", verdict="supported", reasoning="Gap check judge call failed; defaulting to supported.")
                for s in gap_skills
            ]

        result = []
        for c in data.get("claims", []):
            has_skill = bool(c.get("candidate_has_skill", False))
          
            verdict = "unsupported" if has_skill else "supported"
            result.append(
                ClaimVerdict(
                    claim=f"gap: {c.get('claim', '')}",
                    verdict=verdict,
                    reasoning=str(c.get("reasoning", "")),
                )
            )
        return result


    def evaluate_answer_relevance(self, query: str, response: Dict[str, Any]) -> AnswerRelevanceResult:
        answer_text = json.dumps(
            {
                "decision": response.get("decision", ""),
                "explanation": response.get("explanation", ""),
                "summary": response.get("summary", ""),
                "score": response.get("score", ""),
            },
            ensure_ascii=False,
            indent=2,
        )

        prompt = f"""Rate ANSWER RELEVANCY: does the answer below actually respond to
the QUERY (a specific job posting needing a fit assessment)? This measures
TOPICAL relevance only — NOT how detailed, specific, or well-argued the
answer is. A short, generic-sounding, but clearly on-topic answer about
THIS job should score HIGH.

Only score low if the answer:
- talks about a completely different job/topic than the one in the query, or
- is empty / non-responsive / doesn't engage with fit for this role at all.

Do NOT score low just because the explanation is brief, uses generic
phrasing, or doesn't cite every requirement from the posting — that is a
quality/completeness issue, not a relevance issue, and is evaluated
elsewhere.

CRITICAL: A confident "this candidate is NOT a good fit" verdict for THIS
job is just as RELEVANT as a confident "this candidate IS a good fit"
verdict — relevance is about whether the answer engages with the right
job, not about which way the decision goes. Do NOT score a correct,
well-reasoned rejection lower than a match just because it's negative.

Scoring anchors:
- 0.9-1.0: clearly discusses the candidate's fit (positive OR negative) for
  this specific job/title
- 0.5-0.8: on-topic but somewhat vague or only partially engages with the role
- 0.0-0.3: off-topic, empty, or about a different job entirely

## Query (job posting):
{query[:2000]}

## Answer produced by the system:
{answer_text}

Respond with ONLY this JSON shape:
{{
  "overall_score": 0.9,
  "reasoning": "one or two sentences justifying the score"
}}"""

        data = self._judge_call(prompt, max_tokens=1000)
        if data is None:
            return AnswerRelevanceResult(
                overall_score=0.0, note="Judge call failed or returned invalid JSON."
            )

        overall = max(0.0, min(1.0, float(data.get("overall_score", 0.0))))
        return AnswerRelevanceResult(overall_score=overall, reasoning=str(data.get("reasoning", "")))


    def evaluate(
        self,
        job_title: str,
        query: str,
        contexts: List[Dict],
        response: Dict[str, Any],
    ) -> RAGTriadResult:
        logger.info(f"🧪 Running RAG Triad for '{job_title}'...")

        context_relevance = self.evaluate_context_relevance(query, contexts)
        groundedness = self.evaluate_groundedness(contexts, response, job_query=query)
        answer_relevance = self.evaluate_answer_relevance(query, response)

        result = RAGTriadResult(
            job_title=job_title,
            context_relevance=context_relevance,
            groundedness=groundedness,
            answer_relevance=answer_relevance,
            thresholds=dict(self.thresholds),
        )

        logger.info(
            f"   context_relevance={context_relevance.overall_score:.2f} "
            f"groundedness={groundedness.overall_score:.2f} "
            f"answer_relevance={answer_relevance.overall_score:.2f} "
            f"-> {'✅ PASS' if result.passed else '❌ FAIL'}"
        )
        return result