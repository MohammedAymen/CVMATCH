
import json
import random
from pathlib import Path
from typing import Dict, List




GOLDEN_TEST_SET: List[Dict[str, str]] = [
    {
        "title": "Python Backend Developer (FastAPI)",
        "description": (
            "Build and maintain REST APIs using FastAPI, integrate with PostgreSQL, "
            "write background workers for data pipelines, and collaborate with the "
            "frontend team on API contracts."
        ),
        "requirements": (
            "2+ years Python experience. Solid understanding of REST API design. "
            "Experience with async programming. Familiarity with Docker is a plus."
        ),
       
        "expected_domain_match": "high",
    },
    {
        "title": "Senior Cloud/DevOps Engineer",
        "description": (
            "Own our AWS infrastructure end to end: design multi-region architecture, "
            "manage Kubernetes clusters, and lead incident response for production "
            "outages."
        ),
        "requirements": (
            "5+ years hands-on AWS (EKS, VPC, IAM). Deep Kubernetes and Terraform "
            "experience mandatory. On-call rotation required."
        ),
       
        "expected_domain_match": "partial",
    },
    {
        "title": "AI/LLM Engineer",
        "description": (
            "Design and ship LLM-powered features: RAG pipelines, prompt engineering, "
            "vector databases, and evaluation of model outputs in production."
        ),
        "requirements": (
            "Experience with LLM APIs (OpenAI/Gemini/Groq or similar). Familiarity "
            "with embeddings and vector search (ChromaDB, FAISS, Pinecone). Python "
            "required."
        ),
        "expected_domain_match": "high",
    },
    {
        "title": "Enterprise Sales Account Executive",
        "description": (
            "Own the full sales cycle for enterprise accounts: prospecting, demos, "
            "negotiation, and closing. Travel to client sites as needed."
        ),
        "requirements": (
            "5+ years B2B SaaS sales experience. Proven quota attainment. Strong "
            "presentation and negotiation skills. No coding required."
        ),
       
        "expected_domain_match": "low",
    },
    {
        "title": "Junior QA Tester",
        "description": "Test.",
        "requirements": "Attention to detail.",
      
        "expected_domain_match": "thin_posting",
    },
]



def load_from_checkpoints(
    n: int = 5,
    checkpoints_dir: str = "data/checkpoints",
    seed: int = 42,
) -> List[Dict[str, str]]:
   
    directory = Path(checkpoints_dir)
    if not directory.exists():
        raise FileNotFoundError(f"Checkpoints directory not found: {directory}")

    all_jobs: List[Dict[str, str]] = []
    seen_titles = set()

    for file in sorted(directory.glob("jobs_*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for job in jobs:
            title = job.get("title", "").strip()
            if not title or title in seen_titles:
                continue
            if not job.get("description") and not job.get("requirements"):
                continue
            seen_titles.add(title)
            all_jobs.append(
                {
                    "title": title,
                    "description": job.get("description", ""),
                    "requirements": job.get("requirements", ""),
                }
            )

    if not all_jobs:
        raise ValueError(f"No usable jobs found under {directory}")

    rng = random.Random(seed)
    rng.shuffle(all_jobs)
    return all_jobs[:n]