"""Rendering RAG Triad results as a console table, a Markdown report, and JSON."""

import json
from datetime import datetime
from pathlib import Path
from typing import List

from evaluation.rag_triad import RAGTriadResult


def print_console_table(results: List[RAGTriadResult]) -> None:
    header = f"{'Job':<40} {'CtxRel':>8} {'Ground':>8} {'AnsRel':>8} {'Avg':>6} {'Min':>6}  Result"
    print("\n" + header)
    print("-" * len(header))

    for r in results:
        title = (r.job_title[:37] + "...") if len(r.job_title) > 40 else r.job_title
        status = "✅ PASS" if r.passed else "❌ FAIL"
        print(
            f"{title:<40} "
            f"{r.context_relevance.overall_score:>8.2f} "
            f"{r.groundedness.overall_score:>8.2f} "
            f"{r.answer_relevance.overall_score:>8.2f} "
            f"{r.average_score:>6.2f} "
            f"{r.weakest_score:>6.2f}  {status}"
        )

    print("-" * len(header))
    n = len(results)
    passed = sum(1 for r in results if r.passed)
    avg_ctx = sum(r.context_relevance.overall_score for r in results) / n
    avg_grd = sum(r.groundedness.overall_score for r in results) / n
    avg_ans = sum(r.answer_relevance.overall_score for r in results) / n
    print(
        f"{'AVERAGE':<40} {avg_ctx:>8.2f} {avg_grd:>8.2f} {avg_ans:>8.2f}"
        f"{'':>6} {'':>6}  {passed}/{n} passed\n"
    )

    # لو في claims اتحكم عليها unsupported، اطبعهم صريح — دي أكتر حاجة مهمة تتراجع
    for r in results:
        unsupported = r.groundedness.unsupported_claims
        if unsupported:
            print(f"⚠️  Unsupported claims in '{r.job_title}':")
            for c in unsupported:
                print(f"   - {c.claim}  ({c.reasoning})")
            print()


def save_json_report(results: List[RAGTriadResult], output_dir: str = "data/eval_reports") -> Path:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"rag_triad_{timestamp}.json"

    n = len(results)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_jobs": n,
            "passed": sum(1 for r in results if r.passed),
            "avg_context_relevance": round(sum(r.context_relevance.overall_score for r in results) / n, 3) if n else 0,
            "avg_groundedness": round(sum(r.groundedness.overall_score for r in results) / n, 3) if n else 0,
            "avg_answer_relevance": round(sum(r.answer_relevance.overall_score for r in results) / n, 3) if n else 0,
        },
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_markdown_report(results: List[RAGTriadResult], output_dir: str = "data/eval_reports") -> Path:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"rag_triad_{timestamp}.md"

    n = len(results)
    passed = sum(1 for r in results if r.passed)

    lines = [
        f"# RAG Triad Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**{passed}/{n} jobs passed** all three thresholds "
        f"(context_relevance ≥ {results[0].thresholds['context_relevance']}, "
        f"groundedness ≥ {results[0].thresholds['groundedness']}, "
        f"answer_relevance ≥ {results[0].thresholds['answer_relevance']})." if n else "No results.",
        "",
        "| Job | Context Relevance | Groundedness | Answer Relevance | Avg | Result |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        status = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.job_title} | {r.context_relevance.overall_score:.2f} | "
            f"{r.groundedness.overall_score:.2f} | {r.answer_relevance.overall_score:.2f} | "
            f"{r.average_score:.2f} | {status} |"
        )

    lines.append("")

    for r in results:
        unsupported = r.groundedness.unsupported_claims
        if unsupported:
            lines.append(f"### ⚠️ Unsupported claims — {r.job_title}")
            for c in unsupported:
                lines.append(f"- **{c.claim}** — {c.reasoning}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def sync_readme_table(results: List[RAGTriadResult], readme_path: str = "README.md") -> bool:
    """
    بتحدّث جدول النتايج بين علامتي RAG_TRIAD_RESULTS_START/END في README.md
    بنفس نتايج آخر run. بترجع False لو الملف أو العلامات مش موجودة (عشان
    تتنفذ بأمان في أي بيئة من غير ما تعمل crash لو حد شال العلامات بالغلط).
    """
    path = Path(readme_path)
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    start_marker = "<!-- RAG_TRIAD_RESULTS_START -->"
    end_marker = "<!-- RAG_TRIAD_RESULTS_END -->"
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return False

    n = len(results)
    passed = sum(1 for r in results if r.passed)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    table_lines = [
        start_marker,
        f"_Last run: {timestamp} — {passed}/{n} jobs passed._",
        "",
        "| Job | Context Relevance | Groundedness | Answer Relevance | Avg | Result |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        status = "✅" if r.passed else "❌"
        table_lines.append(
            f"| {r.job_title} | {r.context_relevance.overall_score:.2f} | "
            f"{r.groundedness.overall_score:.2f} | {r.answer_relevance.overall_score:.2f} | "
            f"{r.average_score:.2f} | {status} |"
        )
    table_lines.append(end_marker)

    new_text = text[:start] + "\n".join(table_lines) + text[end + len(end_marker):]
    path.write_text(new_text, encoding="utf-8")
    return True