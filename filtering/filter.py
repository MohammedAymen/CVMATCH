# filtering/filter.py

from typing import List, Dict, Any
from core.logger import logger


def filter_jobs_by_score(
    jobs_with_scores: List[Dict[str, Any]],
    min_score: float = 60.0,
) -> List[Dict[str, Any]]:
   
    if not jobs_with_scores:
        logger.warning("No jobs provided for filtering")
        return []
    
    filtered = []
    for job in jobs_with_scores:
        score = job.get("score", 0)
        if score >= min_score:
            filtered.append(job)
        else:
            logger.debug(f"Filtered out: {job.get('job_title', 'Unknown')} (score: {score}%)")
    
    logger.info(f"✅ Filtered: {len(filtered)} jobs passed (min_score={min_score}%) out of {len(jobs_with_scores)}")
    return filtered


def get_top_jobs(
    jobs_with_scores: List[Dict[str, Any]],
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """
    إرجاع أفضل N وظائف بناءً على درجة المطابقة (score).
    """
    if not jobs_with_scores:
        return []
    
    sorted_jobs = sorted(
        jobs_with_scores,
        key=lambda x: x.get("score", 0),
        reverse=True
    )
    return sorted_jobs[:top_n]


def sort_jobs_by_score(
    jobs_with_scores: List[Dict[str, Any]],
    reverse: bool = True
) -> List[Dict[str, Any]]:

    return sorted(jobs_with_scores, key=lambda x: x.get("score", 0), reverse=reverse)