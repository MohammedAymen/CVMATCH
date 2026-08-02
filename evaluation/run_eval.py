
import argparse
import sys

from core.ai_client import AIClient
from core.logger import logger
from evaluation.dataset import GOLDEN_TEST_SET, load_from_checkpoints
from evaluation.rag_triad import RAGTriadJudge, RAGTriadResult
from evaluation.report import print_console_table, save_json_report, save_markdown_report, sync_readme_table
from matching.scorer import JobScorer
from profile_data.embedder import ProfileEmbedder


def parse_args():
    p = argparse.ArgumentParser(description="Run RAG Triad evaluation on CVMATCH's matching pipeline")
    p.add_argument("--source", choices=["golden", "checkpoints"], default="golden")
    p.add_argument("--n", type=int, default=5, help="how many jobs to sample when --source checkpoints")
    p.add_argument("--top-k", type=int, default=10, help="chunks to retrieve per job")
    p.add_argument("--persist-directory", default="data/chroma_db")
    p.add_argument("--judge-model", default=None, help="force a specific model for judging (e.g. to avoid self-grading bias)")
    p.add_argument("--context-threshold", type=float, default=0.5)
    p.add_argument("--groundedness-threshold", type=float, default=0.7)
    p.add_argument("--answer-threshold", type=float, default=0.7)
    p.add_argument("--no-report", action="store_true", help="skip writing json/markdown files")
    p.add_argument("--output-dir", default="data/eval_reports")
    p.add_argument("--update-readme", action="store_true", help="also refresh the results table in README.md")
    return p.parse_args()


def main():
    args = parse_args()

    if args.source == "golden":
        test_jobs = GOLDEN_TEST_SET
        logger.info(f"📋 Using golden test set ({len(test_jobs)} jobs)")
    else:
        test_jobs = load_from_checkpoints(n=args.n)
        logger.info(f"📋 Sampled {len(test_jobs)} real jobs from checkpoints")

    embedder = ProfileEmbedder(persist_directory=args.persist_directory)
    if embedder.collection.count() == 0:
        logger.error(
            "❌ Chroma collection is empty — build profile embeddings first "
            "(run the normal pipeline / build_profile_embeddings) before evaluating."
        )
        sys.exit(2)

    ai_client = AIClient()
    scorer = JobScorer(ai_client=ai_client)
    judge = RAGTriadJudge(
        ai_client=ai_client,
        context_relevance_threshold=args.context_threshold,
        groundedness_threshold=args.groundedness_threshold,
        answer_relevance_threshold=args.answer_threshold,
        judge_model=args.judge_model,
    )

    results: list[RAGTriadResult] = []

    for job in test_jobs:
        title = job["title"]
        query_text = f"Job title: {title}\n{job.get('description', '')}\n{job.get('requirements', '')}"

        contexts = embedder.retrieve_similar_chunks(query_text, top_k=args.top_k)
        if not contexts:
            logger.warning(f"⚠️ No chunks retrieved for '{title}' — scoring skipped, context_relevance=0")

        response = scorer.score_job(
            job_title=title,
            job_description=job.get("description", ""),
            job_requirements=job.get("requirements", ""),
            relevant_chunks=contexts,
        ) if contexts else {"score": 0, "explanation": "", "strengths": [], "gaps": [], "summary": ""}

        result = judge.evaluate(job_title=title, query=query_text, contexts=contexts, response=response)

        domain_match = job.get("expected_domain_match", "high")
        if domain_match == "low":
            result.thresholds["context_relevance"] = 0.0
            correctly_flagged_as_mismatch = contexts and result.context_relevance.overall_score < args.context_threshold
            if not (contexts == [] or correctly_flagged_as_mismatch):
                logger.warning(
                    f"⚠️ '{title}' متوقع يبقى mismatch لكن context_relevance جه عالي "
                    f"({result.context_relevance.overall_score:.2f}) — يستاهل مراجعة."
                )
        elif domain_match == "partial":
     
            result.thresholds["context_relevance"] = 0.0
            if contexts and result.context_relevance.overall_score < 0.05:
                logger.warning(
                    f"⚠️ '{title}' (partial match) context_relevance جه صفر تقريبًا "
                    f"({result.context_relevance.overall_score:.2f}) — ده أقل من المتوقع حتى للتداخل الجزئي."
                )
        elif domain_match == "thin_posting":
       
            result.thresholds["context_relevance"] = 0.0

        results.append(result)

    print_console_table(results)

    if not args.no_report:
        json_path = save_json_report(results, output_dir=args.output_dir)
        md_path = save_markdown_report(results, output_dir=args.output_dir)
        logger.info(f"📄 Reports saved: {json_path} | {md_path}")

    if args.update_readme:
        if sync_readme_table(results):
            logger.info("📝 README.md 'Latest results' table updated")
        else:
            logger.warning("⚠️ Could not update README.md — file or markers not found")

    all_passed = all(r.passed for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()