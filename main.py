# api/main.py

import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
import json
import shutil
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.logger import logger
from core.config import settings
from core.ai_client import AIClient          # ← الجديد
from profile_data.cv_parser import load_cv_documents
from profile_data.github_fetcher import load_github_profile_documents
from profile_data.embedder import build_profile_embeddings, ProfileEmbedder
from matching.pipeline import MatchingPipeline
from notion.dashboard import push_to_notion_dashboard, NotionDashboard
from collectors.wuzzuff import WuzzufScraper


# ============================================================
# Pydantic Schemas
# ============================================================

class ProfileSetupResponse(BaseModel):
    status: str
    user_id: str
    message: str
    total_chunks: int
    embedding_model: str


class JobSearchRequest(BaseModel):
    query: str = "python developer"
    location: str = "egypt"
    max_jobs: int = 20


class JobResponse(BaseModel):
    title: str
    company: str
    location: str
    match_score: int
    confidence: str
    strengths: List[str]
    gaps: List[str]
    recommendations: List[str]
    apply_link: Optional[str] = None
    scored_by: Optional[str] = None    # ← أي provider استخدمناه


class SearchResponse(BaseModel):
    total_jobs: int
    qualified_jobs: int
    jobs: List[JobResponse]
    notion_link: Optional[str] = None
    provider_stats: Optional[Dict] = None   # ← إحصائيات الـ providers


# ============================================================
# Lifespan
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting Job Matcher API...")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.state.sessions = {}

    # ─── بناء AIClient مرة واحدة للـ app بأكمله ───
    app.state.ai_client = AIClient(
        groq_api_key=getattr(settings, "groq_api_key", None),
        gemini_api_key=getattr(settings, "gemini_api_key", None),
        ollama_url=getattr(settings, "ollama_url", "http://localhost:11434"),
    )

    yield
    logger.info("🛑 Shutting down Job Matcher API...")


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="Job Matcher AI API",
    description="AI-powered job matching — Groq → Gemini → Qwen fallback",
    version="2.0.0",
    lifespan=lifespan,
)

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Helper Functions
# ============================================================

def build_embedder(
    cv_path: Optional[str] = None,
    github_username: Optional[str] = None,
) -> ProfileEmbedder:
    cv_documents     = []
    github_documents = []

    if cv_path and Path(cv_path).exists():
        logger.info(f"📄 Loading CV from {cv_path}")
        cv_documents = load_cv_documents(cv_path)
        logger.info(f"   ✅ CV: {len(cv_documents)} documents")

    if github_username:
        logger.info(f"🐙 Loading GitHub profile for @{github_username}")
        try:
            github_documents = load_github_profile_documents(
                username=github_username,
                token=settings.github_token,
                max_repos=20,
            )
            logger.info(f"   ✅ GitHub: {len(github_documents)} documents")
        except Exception as e:
            logger.warning(f"GitHub fetch failed: {e}")

    if not cv_documents and not github_documents:
        raise HTTPException(status_code=400, detail="No profile data provided.")

    embedder = build_profile_embeddings(
        cv_documents=cv_documents,
        github_documents=github_documents,
        persist_directory=settings.chroma_persist_directory,
        reset_collection=True,
    )
    return embedder


def get_user_session(user_id: str) -> Dict[str, Any]:
    session = app.state.sessions.get(user_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="User not found. Please setup profile first.",
        )
    return session


# ============================================================
# Endpoints
# ============================================================

@app.get("/")
async def root():
    return {
        "message": "Job Matcher AI API",
        "status":  "running",
        "version": "2.0.0",
        "llm_chain": "Groq → Gemini → Qwen 2.5 (Ollama)",
    }


@app.get("/health")
async def health_check():
    ai_stats = app.state.ai_client.get_stats() if hasattr(app.state, "ai_client") else {}
    return {
        "status":       "healthy",
        "sessions":     len(app.state.sessions),
        "ai_providers": ai_stats,
    }


@app.post("/profile/setup", response_model=ProfileSetupResponse)
async def setup_profile(
    cv_file: Optional[UploadFile] = File(None),
    github_username: Optional[str] = Query(None),
    notion_database_id: Optional[str] = Query(None),
):
    if not cv_file and not github_username:
        raise HTTPException(
            status_code=400,
            detail="Provide at least a CV file or GitHub username.",
        )

    cv_path = None
    if cv_file:
        if not cv_file.filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are allowed.")
        cv_path = UPLOAD_DIR / f"cv_{uuid.uuid4().hex[:8]}.pdf"
        with open(cv_path, "wb") as f:
            shutil.copyfileobj(cv_file.file, f)
        logger.info(f"📄 CV saved: {cv_path}")

    try:
        embedder = build_embedder(
            cv_path=str(cv_path) if cv_path else None,
            github_username=github_username or None,
        )
        stats = embedder.get_collection_stats()

        user_id = str(uuid.uuid4())

        notion_data_source_id = None
        if not notion_database_id:
            try:
                notion_dashboard    = NotionDashboard()
                notion_database_id  = notion_dashboard.database_id
                notion_data_source_id = notion_dashboard.data_source_id
                logger.info(f"📝 Notion DB created: {notion_database_id}")
            except Exception as e:
                logger.warning(f"Notion DB creation failed: {e}")
                notion_database_id = None
        else:
            try:
                notion_dashboard      = NotionDashboard(database_id=notion_database_id)
                notion_data_source_id = notion_dashboard.data_source_id
            except Exception as e:
                logger.warning(f"Failed to resolve data_source_id: {e}")
                notion_database_id    = None
                notion_data_source_id = None

        app.state.sessions[user_id] = {
            "embedder":             embedder,
            "cv_path":              str(cv_path) if cv_path else None,
            "github_username":      github_username,
            "notion_database_id":   notion_database_id,
            "notion_data_source_id": notion_data_source_id,
            "created_at":           datetime.now().isoformat(),
        }

        return ProfileSetupResponse(
            status="success",
            user_id=user_id,
            message="Profile processed successfully.",
            total_chunks=stats.get("total_chunks", 0),
            embedding_model=stats.get("embedding_model", "unknown"),
        )

    except Exception as e:
        logger.error(f"❌ Profile setup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/jobs/search", response_model=SearchResponse)
async def search_jobs(
    request: JobSearchRequest,
    user_id: str = Query(..., description="User ID from /profile/setup"),
):
    session  = get_user_session(user_id)
    embedder = session.get("embedder")
    notion_db_id = session.get("notion_database_id")
    notion_ds_id = session.get("notion_data_source_id")

    if not embedder:
        raise HTTPException(status_code=400, detail="Embedder not found. Re-setup profile.")

    # ─── Scraping ───
    scraper = WuzzufScraper(headless=True)
    try:
        jobs_raw = await scraper.scrape(
            query=request.query,
            location=request.location,
            max_jobs=request.max_jobs,
        )
    finally:
        await scraper.close()

    if not jobs_raw:
        return SearchResponse(total_jobs=0, qualified_jobs=0, jobs=[], notion_link=None)

    jobs_dict = [
        {
            "title":       j.title,
            "company":     j.company,
            "location":    j.location,
            "description": j.description,
            "requirements": j.requirements,
            "apply_link":  j.apply_link,
            "source":      j.source,
        }
        for j in jobs_raw
    ]

    # ─── Pipeline (يستخدم الـ AIClient المشترك) ───
    pipeline = MatchingPipeline(
        embedder=embedder,
        similarity_threshold=0.50,
        llm_score_threshold=60.0,
        top_k_chunks=10,
        ai_client=app.state.ai_client,   # ← مشترك على مستوى الـ app
        max_workers=3,                    # ↑ زودنا لأن API calls أسرع من Ollama
    )
    results = pipeline.process_jobs(jobs_dict)

    # ─── Checkpoint ───
    checkpoint_dir  = Path("data/checkpoints")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"jobs_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "user_id":              user_id,
                    "notion_database_id":   notion_db_id,
                    "notion_data_source_id": notion_ds_id,
                    "saved_at":             datetime.now().isoformat(),
                    "provider_stats":       results.get("provider_stats", {}),
                    "jobs":                 results["stage3_final"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info(
            f"💾 Checkpoint saved: {checkpoint_path} "
            f"({len(results['stage3_final'])} jobs)"
        )
    except Exception as e:
        logger.error(f"⚠️ Checkpoint save failed (non-fatal): {e}")

    # ─── Notion upload ───
    if results["stage3_final"] and notion_db_id and notion_ds_id:
        try:
            notion_result = push_to_notion_dashboard(
                jobs=results["stage3_final"],
                database_id=notion_db_id,
                data_source_id=notion_ds_id,
                skip_duplicates=True,
            )
            logger.info(f"📤 Notion upload: {notion_result}")
        except Exception as e:
            logger.error(f"❌ Notion upload failed: {e}")
            logger.info(
                f"ℹ️  Retry with: scripts/upload_checkpoint.py {checkpoint_path}"
            )

    # ─── Build response ───
    job_responses = [
        JobResponse(
            title=job.get("title", "Unknown"),
            company=job.get("company", "Unknown"),
            location=job.get("location", "N/A"),
            match_score=job.get("llm_score", 0),
            confidence=job.get("llm_confidence", "Medium"),
            strengths=job.get("strengths", [])[:5],
            gaps=job.get("gaps", [])[:5],
            recommendations=job.get("recommendations", [])[:3],
            apply_link=job.get("apply_link"),
            scored_by=job.get("scored_by"),
        )
        for job in results["stage3_final"][:20]
    ]

    notion_link = (
        f"https://www.notion.so/{notion_db_id.replace('-', '')}"
        if notion_db_id
        else None
    )

    return SearchResponse(
        total_jobs=len(jobs_raw),
        qualified_jobs=len(results["stage3_final"]),
        jobs=job_responses,
        notion_link=notion_link,
        provider_stats=results.get("provider_stats"),
    )


@app.get("/ai/stats")
async def ai_provider_stats():
    """إحصائيات استخدام الـ LLM providers."""
    return app.state.ai_client.get_stats()


@app.post("/ai/reset/{provider}")
async def reset_provider(provider: str):
    """إعادة تفعيل provider بعد انتهاء الـ cooldown يدوياً."""
    try:
        p = LLMProvider(provider)
        app.state.ai_client.reset_provider(p)
        return {"status": "reset", "provider": provider}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")


@app.delete("/profile/{user_id}")
async def delete_profile(user_id: str):
    session = get_user_session(user_id)
    if session.get("cv_path"):
        try:
            Path(session["cv_path"]).unlink()
        except Exception:
            pass
    del app.state.sessions[user_id]
    return {"status": "deleted", "user_id": user_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)