"""
سكريبت قياس الـ accuracy الحقيقي لنظام CVMatch.

ليه محتاجين ده بدل ما نعتمد على تشغيل pytest مرة واحدة:
النظام بيعتمد على LLM، وحتى مع temperature=0 مفيش ضمان حتمية 100% (خصوصًا مع Groq).
يعني تشغيلة واحدة بتقولك "نجح" أو "فشل" بس، من غير ما توريك هل ده مستقر ولا كان
احتمال حظه. الـ accuracy الحقيقي = نسبة النجاح عبر عدة تشغيلات، مش نتيجة تشغيلة واحدة.

الاستخدام:
    python scripts/run_accuracy_eval.py --runs 5

هيشغل كل الـ 5 وظايف في GOLDEN_TEST_SET، كل واحدة N مرة (افتراضيًا 5)،
ويطلعلك في الآخر:
  - نسبة نجاح كل وظيفة على حدة (عشان تعرف مين "مستقر" ومين "فلاكي")
  - نسبة النجاح الكلية للنظام
  - متوسط وانحراف معياري لكل مقياس من التلاتة (context_relevance, groundedness, answer_relevance)

ملحوظة: كل تشغيلة بتستهلك استدعاءات LLM حقيقية (Groq/Gemini)، يعني لو شغلت
--runs 5 على 5 وظايف هيبقى عندك 25 دورة تقييم كاملة — كل واحدة فيها أكتر من
استدعاء LLM (score_job + الـ3 judges بتوع الـ RAG triad). خد بالك من TPM limit
بتاعت Groq.
"""

import argparse
import os
import statistics
import sys
import time
from collections import defaultdict

def _find_project_root(start: str) -> str:
    """
    بندور على روت المشروع بالصعود لفوق لحد ما نلاقي مجلد فيه كل من core/
    و evaluation/ جنب بعض (علامة مميزة لروت CVMATCH). كده السكريبت شغال
    من أي مكان تحطه فيه (scripts/, evaluation/, أو حتى روت المشروع مباشرة)
    من غير ما يفترض عدد مستويات ثابت.
    """
    d = start
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "core")) and os.path.isdir(os.path.join(d, "evaluation")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return start


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = _find_project_root(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.ai_client import AIClient
from evaluation.dataset import GOLDEN_TEST_SET
from evaluation.rag_triad import RAGTriadJudge
from matching.scorer import JobScorer
from profile_data.embedder import ProfileEmbedder


def _run_triad(job, embedder, scorer, judge, top_k=5):
    """نفس منطق _run_triad في tests/test_rag_triad.py بالظبط — بنمشي على نفس
    مسار الإنتاج الحقيقي (hybrid retrieval)."""
    query_text = f"Job title: {job['title']}\n{job.get('description', '')}\n{job.get('requirements', '')}"
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

    triad = judge.evaluate(job_title=job["title"], query=query_text, contexts=contexts, response=response)
    return triad, response


def _job_passes(job, triad, thresholds) -> bool:
    """نفس شروط النجاح المستخدمة في test_golden_job_passes_rag_triad بالظبط."""
    domain_match = job.get("expected_domain_match", "high")

    if domain_match == "low":
        if not (triad.context_relevance.overall_score < thresholds["context_relevance"]):
            return False
    elif domain_match == "partial":
        if not (triad.context_relevance.overall_score > 0.05):
            return False
    elif domain_match == "thin_posting":
        pass
    else:
        if not (triad.context_relevance.overall_score >= thresholds["context_relevance"]):
            return False

    if not (triad.groundedness.overall_score >= thresholds["groundedness"]):
        return False
    if not (triad.answer_relevance.overall_score >= thresholds["answer_relevance"]):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="قياس الـ accuracy الحقيقي عبر تكرار اختبارات RAG triad")
    parser.add_argument("--runs", type=int, default=5, help="عدد مرات تكرار كل وظيفة (افتراضي 5)")
    parser.add_argument("--job", type=str, default=None,
                         help="شغّل وظيفة واحدة بس (بحث جزئي في العنوان)، مفيد للتركيز على وظيفة فلاكية بعينها")
    parser.add_argument("--verbose", action="store_true",
                         help="اطبع التفاصيل الكاملة (كل claim وسببه) لأي تشغيلة فاشلة، مش بس الأرقام")
    args = parser.parse_args()

    print(f"\n🔧 تجهيز embedder / scorer / judge...")
    embedder = ProfileEmbedder(persist_directory="data/chroma_db")
    if embedder.collection.count() == 0:
        print("❌ مفيش chunks في الـ chroma collection — ابني الـ embeddings الأول.")
        sys.exit(1)

    ai_client = AIClient()
    scorer = JobScorer(ai_client=ai_client)
    judge = RAGTriadJudge(ai_client=ai_client)

    jobs_to_run = GOLDEN_TEST_SET
    if args.job:
        jobs_to_run = [j for j in GOLDEN_TEST_SET if args.job.lower() in j["title"].lower()]
        if not jobs_to_run:
            print(f"❌ مفيش وظيفة اسمها فيه '{args.job}'. الوظايف المتاحة:")
            for j in GOLDEN_TEST_SET:
                print(f"   - {j['title']}")
            sys.exit(1)
        print(f"🔍 هنشغل بس: {[j['title'] for j in jobs_to_run]}")

    # job_title -> list of (passed: bool, context_relevance, groundedness, answer_relevance)
    results = defaultdict(list)

    total_iterations = len(jobs_to_run) * args.runs
    done = 0
    t_start = time.time()

    for run_idx in range(1, args.runs + 1):
        print(f"\n{'=' * 60}\n🔁 التشغيلة {run_idx}/{args.runs}\n{'=' * 60}")
        for job in jobs_to_run:
            done += 1
            print(f"  [{done}/{total_iterations}] {job['title']}...", end=" ", flush=True)
            try:
                triad, response = _run_triad(job, embedder, scorer, judge)
                passed = _job_passes(job, triad, judge.thresholds)
                results[job["title"]].append((
                    passed,
                    triad.context_relevance.overall_score,
                    triad.groundedness.overall_score,
                    triad.answer_relevance.overall_score,
                ))
                status = "✅" if passed else "❌"
                print(f"{status} (ctx={triad.context_relevance.overall_score:.2f} "
                      f"grd={triad.groundedness.overall_score:.2f} "
                      f"ans={triad.answer_relevance.overall_score:.2f})")

                if args.verbose and not passed:
                    print(f"\n    {'─' * 60}")
                    print(f"    🔬 تفاصيل الفشل — {job['title']}")
                    print(f"    {'─' * 60}")
                    print(f"    📋 رد الموديل الفعلي (score={response.get('score')}%):")
                    print(f"       explanation: {response.get('explanation', '')}")
                    for s in response.get("strengths", []):
                        print(f"       + strength: {s}")
                    for g in response.get("gaps", []):
                        if isinstance(g, dict):
                            print(f"       - gap: {g.get('skill')} (severity={g.get('severity')})")
                        else:
                            print(f"       - gap: {g}")
                    print()
                    unsupported = [c for c in triad.groundedness.claims if c.verdict == "unsupported"]
                    if unsupported:
                        print(f"    ❌ Claims غير مدعومة (هلوسة محتملة):")
                        for c in unsupported:
                            print(f"       • \"{c.claim}\"")
                            print(f"         السبب: {c.reasoning}")
                    partial = [c for c in triad.groundedness.claims if c.verdict == "partially_supported"]
                    if partial:
                        print(f"    ⚠️  Claims مدعومة جزئيًا:")
                        for c in partial:
                            print(f"       • \"{c.claim}\"")
                            print(f"         السبب: {c.reasoning}")
                    if triad.context_relevance.overall_score < 0.5:
                        print(f"    📉 context_relevance منخفض ({triad.context_relevance.overall_score:.2f}) — تفاصيل الـ chunks:")
                        for cs in triad.context_relevance.chunk_scores:
                            print(f"       • chunk {cs.chunk_index} ({cs.source}): score={cs.relevance_score:.2f} — {cs.reasoning[:100]}")
                    print(f"    {'─' * 60}\n")
            except Exception as e:
                print(f"⚠️  خطأ: {e}")
                results[job["title"]].append((False, None, None, None))

    elapsed = time.time() - t_start

    # ===== التقرير النهائي =====
    print("\n" + "=" * 70)
    print("📊 تقرير الـ Accuracy")
    print("=" * 70)

    all_pass_flags = []
    print(f"\n{'الوظيفة':<45} {'نسبة النجاح':<15} {'متوسط Groundedness'}")
    print("-" * 70)
    for job in jobs_to_run:
        title = job["title"]
        runs = results[title]
        pass_flags = [r[0] for r in runs]
        all_pass_flags.extend(pass_flags)
        pass_rate = sum(pass_flags) / len(pass_flags) * 100

        grd_scores = [r[2] for r in runs if r[2] is not None]
        grd_avg = statistics.mean(grd_scores) if grd_scores else float("nan")
        grd_std = statistics.stdev(grd_scores) if len(grd_scores) > 1 else 0.0

        flag = "🟢" if pass_rate == 100 else ("🟡" if pass_rate >= 50 else "🔴")
        print(f"{flag} {title:<43} {pass_rate:>5.0f}%          {grd_avg:.2f} (±{grd_std:.2f})")

    overall_accuracy = sum(all_pass_flags) / len(all_pass_flags) * 100 if all_pass_flags else 0.0

    print("-" * 70)
    print(f"\n🎯 الـ Accuracy الكلي (نسبة نجاح كل التشغيلات على كل الوظايف): {overall_accuracy:.1f}%")
    print(f"   ({sum(all_pass_flags)}/{len(all_pass_flags)} تشغيلة ناجحة)")
    print(f"\n⏱  الوقت الكلي: {elapsed:.0f}s لعدد {total_iterations} تقييم")

    # تحذير من الوظايف الفلاكية (نجاح جزئي غير مستقر)
    flaky = [j["title"] for j in jobs_to_run
             if 0 < (sum(r[0] for r in results[j["title"]]) / len(results[j["title"]])) < 1]
    if flaky:
        print(f"\n⚠️  وظايف غير مستقرة (بتنجح أحيانًا وتفشل أحيانًا): {', '.join(flaky)}")
        print("   دي محتاجة نظر أكتر — إما البرومبت لسه فيه ثغرة، أو الـ threshold حساس جدًا.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()