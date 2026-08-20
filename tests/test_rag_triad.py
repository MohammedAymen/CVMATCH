import os

import pytest

from core.ai_client import AIClient
from evaluation.dataset import GOLDEN_TEST_SET
from evaluation.rag_triad import RAGTriadJudge
from matching.scorer import JobScorer
from profile_data.embedder import ProfileEmbedder

pytestmark = pytest.mark.integration


def _has_llm_provider() -> bool:
    return bool(os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OLLAMA_MODEL"))


@pytest.fixture(scope="module")
def embedder():
    emb = ProfileEmbedder(persist_directory="data/chroma_db")
    if emb.collection.count() == 0:
        pytest.skip("Chroma collection is empty — build profile embeddings before running this suite")
    return emb


@pytest.fixture(scope="module")
def ai_client():
    if not _has_llm_provider():
        pytest.skip("No LLM provider configured (GROQ_API_KEY / GEMINI_API_KEY / OLLAMA_MODEL)")
    return AIClient()


@pytest.fixture(scope="module")
def scorer(ai_client):
    return JobScorer(ai_client=ai_client)


@pytest.fixture(scope="module")
def judge(ai_client):
    return RAGTriadJudge(ai_client=ai_client)


def _run_triad(job, embedder, scorer, judge, top_k=5):
    query_text = f"Job title: {job['title']}\n{job.get('description', '')}\n{job.get('requirements', '')}"
    # بنستخدم نفس مسار الإنتاج الحقيقي (hybrid retrieval) في score_job_with_profile،
    # عشان التيست يقيس السلوك الفعليبدل من مسار دينس بس مختلف عن الإنتاج.
    if hasattr(embedder, "retrieve_hybrid_chunks"):
        contexts = embedder.retrieve_hybrid_chunks(
            query_text, job_text=query_text, dense_top_k=top_k, max_keyword_extra=3
        )
    else:
        contexts = embedder.retrieve_similar_chunks(query_text, top_k=top_k)
    response = scorer.score_job(
        job_title=job["title"],
        job_description=job.get("description", ""),
        job_requirements=job.get("requirements", ""),
        relevant_chunks=contexts,
    ) if contexts else {"score": 0, "explanation": "", "strengths": [], "gaps": [], "summary": ""}
    return judge.evaluate(job_title=job["title"], query=query_text, contexts=contexts, response=response)


@pytest.mark.parametrize("job", GOLDEN_TEST_SET, ids=[j["title"] for j in GOLDEN_TEST_SET])
def test_golden_job_passes_rag_triad(job, embedder, scorer, judge):
 
    result = _run_triad(job, embedder, scorer, judge)
    domain_match = job.get("expected_domain_match", "high")

    if domain_match == "low":
        assert result.context_relevance.overall_score < judge.thresholds["context_relevance"], (
            f"'{job['title']}' is a deliberate domain-mismatch test job, but context_relevance "
            f"came back high ({result.context_relevance.overall_score:.2f}) — retrieval may be "
            f"hallucinating relevance for an unrelated job posting."
        )
    elif domain_match == "partial":
   
        assert result.context_relevance.overall_score > 0.05, (
            f"'{job['title']}' is a deliberate partial-match test job, but context_relevance "
            f"came back essentially zero ({result.context_relevance.overall_score:.2f}) — expected "
            f"at least some overlap (e.g. AWS/Docker) even though the hard requirements aren't met."
        )
    elif domain_match == "thin_posting":
   
        pass
    else:
        assert result.context_relevance.overall_score >= judge.thresholds["context_relevance"], (
            f"Context relevance too low ({result.context_relevance.overall_score:.2f}) for '{job['title']}' — "
            f"retrieval is pulling irrelevant profile chunks for this query."
        )
    assert result.groundedness.overall_score >= judge.thresholds["groundedness"], (
        f"Groundedness too low ({result.groundedness.overall_score:.2f}) for '{job['title']}' — "
        f"the LLM is making claims not backed by retrieved context (hallucination). "
        f"Unsupported claims: {[c.claim for c in result.groundedness.unsupported_claims]}"
    )
    assert result.answer_relevance.overall_score >= judge.thresholds["answer_relevance"], (
        f"Answer relevance too low ({result.answer_relevance.overall_score:.2f}) for '{job['title']}' — "
        f"the response isn't actually addressing the job query."
    )


def test_thin_posting_does_not_hallucinate_a_confident_match(embedder, scorer, judge):
    """
    Regression guard: a nearly-empty job posting ('Test.' / 'Attention to detail.')
    should NOT produce a confidently-grounded 'Apply' — if it does, the LLM is
    likely inventing fit rather than reasoning from evidence.
    """
    thin_job = next(j for j in GOLDEN_TEST_SET if j["title"] == "Junior QA Tester")
    result = _run_triad(thin_job, embedder, scorer, judge)

    assert not (
        result.groundedness.overall_score >= 0.8
        and result.context_relevance.overall_score >= 0.8
    ), (
        "A near-empty job posting produced a highly grounded, highly relevant "
        "match — check that the pipeline isn't hallucinating confidence from thin input."
    )


def test_wrong_domain_job_is_flagged_low_relevance(embedder, scorer, judge):
    """A sales role should retrieve/match poorly against a technical profile."""
    sales_job = next(j for j in GOLDEN_TEST_SET if "Sales" in j["title"])
    result = _run_triad(sales_job, embedder, scorer, judge)

    assert result.context_relevance.overall_score < 0.6, (
        "Retrieval considered a technical CV/GitHub profile highly relevant to a "
        "non-technical sales role — investigate the embedding/retrieval step."
    )