import os
import hashlib
import concurrent.futures
from typing import List, Dict, Any, Optional
from datetime import datetime
from notion_client import Client
from notion_client.errors import APIResponseError
from core.logger import logger
from core.config import settings


class NotionDashboard:

    
    PROPERTIES_SCHEMA = {
        "Job ID":          {"rich_text": {}},
        "Company":         {"rich_text": {}},
        "Location":        {"rich_text": {}},
        "Match Score":     {"number": {"format": "number"}},
        "Confidence":      {"select": {"options": [
                               {"name": "Low",    "color": "red"},
                               {"name": "Medium", "color": "yellow"},
                               {"name": "High",   "color": "green"},
                           ]}},
        "Strengths":       {"rich_text": {}},
        "Gaps":            {"rich_text": {}},
        "Recommendations": {"rich_text": {}},
        "Decision":        {"select": {"options": [
                               {"name": "Apply",              "color": "green"},
                               {"name": "Improve then apply", "color": "yellow"},
                               {"name": "Skip",                "color": "red"},
                           ]}},
        "Apply URL":       {"url": {}},
        "Status":          {"select": {"options": [
                               {"name": "Pending",      "color": "gray"},
                               {"name": "Applied",      "color": "blue"},
                               {"name": "Interviewing", "color": "yellow"},
                               {"name": "Rejected",     "color": "red"},
                               {"name": "Accepted",     "color": "green"},
                           ]}},
        "Posted Date":     {"date": {}},
        "Source":          {"rich_text": {}},
        "Last Updated":    {"date": {}},
    }

    def __init__(
        self,
        token: Optional[str] = None,
        database_id: Optional[str] = None,
        data_source_id: Optional[str] = None,
    ):
        self.token = token or getattr(settings, "NOTION_TOKEN", None) or os.getenv("NOTION_TOKEN")
        self.database_id    = database_id    or getattr(settings, "NOTION_DATABASE_ID", None)    or os.getenv("NOTION_DATABASE_ID")
        self.data_source_id = data_source_id or getattr(settings, "NOTION_DATA_SOURCE_ID", None) or os.getenv("NOTION_DATA_SOURCE_ID")

        if not self.token:
            raise ValueError("❌ Missing NOTION_TOKEN in .env")

        self.client = Client(auth=self.token)
        logger.info("✅ Notion client initialized")

        if not self.database_id:
            self.database_id, self.data_source_id = self._bootstrap_database()
            self._persist_ids(self.database_id, self.data_source_id)
        elif not self.data_source_id:
            self.data_source_id = self._resolve_data_source_id(self.database_id)
            self._persist_ids(self.database_id, self.data_source_id)
        else:
           
            self._ensure_schema()

        logger.info(f"📋 DB: {self.database_id}")
        logger.info(f"📋 DS: {self.data_source_id}")

   

    def _resolve_data_source_id(self, database_id: str) -> Optional[str]:
        try:
            db = self.client.databases.retrieve(database_id=database_id)
            data_sources = db.get("data_sources", [])
            if data_sources:
                ds_id = data_sources[0]["id"]
                logger.info(f"✅ Resolved data_source_id: {ds_id}")
                return ds_id
        except Exception as e:
            logger.warning(f"Could not resolve data_source_id: {e}")
        return None

    def _bootstrap_database(self) -> tuple:
        
        parent_id = getattr(settings, "NOTION_PARENT_PAGE_ID", None) or os.getenv("NOTION_PARENT_PAGE_ID")

        if not parent_id:
            logger.info("🔍 Searching for a workspace page...")
            results = self.client.search(
                filter={"property": "object", "value": "page"},
                page_size=5,
            ).get("results", [])
            if not results:
                raise RuntimeError(
                    "❌ No Notion pages found. Set NOTION_PARENT_PAGE_ID in .env."
                )
            parent_id = results[0]["id"]
            logger.info(f"📌 Using page {parent_id} as parent")

        
        response = self.client.databases.create(
            parent={"page_id": parent_id},
            title=[{"type": "text", "text": {"content": "🤖 Job Matcher Dashboard"}}],
        )
        db_id = response["id"]
        logger.info(f"✅ Database created: {db_id}")

        
        ds_id = None
        data_sources = response.get("data_sources", [])
        if data_sources:
            ds_id = data_sources[0]["id"]
            logger.info(f"✅ data_source_id: {ds_id}")

        
        if ds_id:
            self._add_schema_to_data_source(ds_id)
        else:
            logger.warning("⚠️ No data_source_id found — schema not applied")

        return db_id, ds_id

    def _add_schema_to_data_source(self, ds_id: str) -> None:
       
        try:
            self.client.data_sources.update(
                ds_id,
                properties=self.PROPERTIES_SCHEMA,
            )
            logger.info(f"✅ Schema applied ({len(self.PROPERTIES_SCHEMA)} columns)")
        except Exception as e:
            logger.error(f"❌ Failed to apply schema: {e}")

    def _ensure_schema(self) -> None:
        
        try:
            db = self.client.databases.retrieve(database_id=self.database_id)
            existing_props = db.get("properties", {})
            missing = [k for k in self.PROPERTIES_SCHEMA if k not in existing_props]

            if missing:
                logger.info(f"⚠️ Missing {len(missing)} columns — adding schema...")
                self._add_schema_to_data_source(self.data_source_id)
            else:
                logger.info(f"✅ Schema OK ({len(existing_props)} columns)")
        except Exception as e:
            logger.warning(f"Could not verify schema: {e}")

    def _persist_ids(self, db_id: str, ds_id: Optional[str]) -> None:
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        try:
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            targets = {"NOTION_DATABASE_ID": db_id}
            if ds_id:
                targets["NOTION_DATA_SOURCE_ID"] = ds_id

            updated_keys = set()
            for i, line in enumerate(lines):
                for key, value in targets.items():
                    if line.startswith(f"{key}="):
                        lines[i] = f"{key}={value}\n"
                        updated_keys.add(key)
                        break

            for key, value in targets.items():
                if key not in updated_keys:
                    lines.append(f"\n{key}={value}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info("💾 Saved Notion IDs to .env")
        except Exception as e:
            logger.warning(f"⚠️ Couldn't save to .env: {e}")

    

    @staticmethod
    def _job_id(job: Dict) -> str:
        key = f"{job.get('title','').lower().strip()}|{job.get('company','').lower().strip()}|{job.get('apply_link','')}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _get_existing_job_ids(self) -> set:
        existing = set()
        cursor = None
        while True:
            try:
                kwargs = {"page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                resp = self.client.data_sources.query(self.data_source_id, **kwargs)
            except Exception as e:
                logger.error(f"❌ Query failed: {e}")
                break

            for page in resp.get("results", []):
                props = page.get("properties", {})
                jid = self._get_rich_text(props.get("Job ID"))
                if jid:
                    existing.add(jid)

            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]
        return existing

   
    def _build_properties(self, job: Dict) -> Dict:
        status = job.get("status", "Pending")
        if status not in {"Pending", "Applied", "Interviewing", "Rejected", "Accepted"}:
            status = "Pending"

        def rt(text: str, limit: int = 2000) -> list:
            return [{"text": {"content": str(text)[:limit]}}]

        def format_gaps(gaps: list, limit: int = 3) -> str:
            """بيدعم الشكل الجديد (list of dicts فيها skill/severity) والقديم (list of strings)."""
            parts = []
            for g in gaps[:limit]:
                if isinstance(g, dict):
                    skill = g.get("skill", "")
                    severity = g.get("severity", "")
                    parts.append(f"{skill} ({severity})" if severity else skill)
                else:
                    parts.append(str(g))
            return ", ".join(parts)

        decision = job.get("decision", "")
        if decision not in {"Apply", "Improve then apply", "Skip"}:
            decision = None

     
        return {
            "Name":            {"title": rt(job.get("title", "Unknown"))},
            "Job ID":          {"rich_text": rt(self._job_id(job))},
            "Company":         {"rich_text": rt(job.get("company", "Unknown"))},
            "Location":        {"rich_text": rt(job.get("location", "N/A"))},
            "Match Score":     {"number": job.get("llm_score", 0)},
            "Confidence":      {"select": {"name": job.get("llm_confidence", "Medium")}},
            "Strengths":       {"rich_text": rt(", ".join(job.get("strengths", [])[:3]))},
            "Gaps":            {"rich_text": rt(format_gaps(job.get("gaps", [])))},
            "Recommendations": {"rich_text": rt(", ".join(job.get("recommendations", [])[:2]))},
            "Decision":        {"select": {"name": decision}} if decision else {"select": None},
            "Apply URL":       {"url": job.get("apply_link") or None},
            "Status":          {"select": {"name": status}},
            "Posted Date":     {"date": {"start": job.get("posted_date", datetime.now().date().isoformat())}},
            "Source":          {"rich_text": rt(job.get("source", "Wuzzuf"))},
            "Last Updated":    {"date": {"start": datetime.now().date().isoformat()}},
        }

   
    def _add_single_job(self, job: Dict) -> Optional[str]:
        try:
            resp = self.client.pages.create(
                parent={"data_source_id": self.data_source_id},
                properties=self._build_properties(job),
            )
            return resp["id"]
        except APIResponseError as e:
            logger.error(f"❌ Failed to add '{job.get('title')}': {e}")
            return None

    def add_jobs_batch(
        self,
        jobs: List[Dict],
        skip_duplicates: bool = True,
        max_workers: int = 5,
    ) -> Dict[str, int]:
        to_add = jobs

        if skip_duplicates:
            logger.info("🔍 Checking for duplicates...")
            existing_ids = self._get_existing_job_ids()
            to_add = [j for j in jobs if self._job_id(j) not in existing_ids]
            skipped = len(jobs) - len(to_add)
            if skipped:
                logger.info(f"⏭️  Skipping {skipped} duplicate(s)")
        else:
            skipped = 0

        if not to_add:
            logger.info("✅ Nothing new to add.")
            return {"added": 0, "skipped": skipped, "failed": 0}

        logger.info(f"📤 Uploading {len(to_add)} job(s) to Notion...")
        results = {"added": 0, "skipped": skipped, "failed": 0}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._add_single_job, job): job for job in to_add}
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    results["added"] += 1
                else:
                    results["failed"] += 1

        logger.info(
            f"✅ Done — Added: {results['added']} | "
            f"Skipped: {results['skipped']} | "
            f"Failed: {results['failed']}"
        )
        return results

    
    def update_job_status(self, page_id: str, new_status: str) -> bool:
        if new_status not in {"Pending", "Applied", "Interviewing", "Rejected", "Accepted"}:
            return False
        try:
            self.client.pages.update(
                page_id=page_id,
                properties={
                    "Status":       {"select": {"name": new_status}},
                    "Last Updated": {"date": {"start": datetime.now().date().isoformat()}},
                },
            )
            return True
        except APIResponseError as e:
            logger.error(f"❌ Failed to update: {e}")
            return False

    def get_all_jobs(self, filter_status: Optional[str] = None) -> List[Dict]:
        kwargs = {}
        if filter_status:
            kwargs["filter"] = {"property": "Status", "select": {"equals": filter_status}}
        try:
            results = self.client.data_sources.query(
                self.data_source_id, **kwargs
            ).get("results", [])
            return [
                {
                    "page_id":     p["id"],
                    "title":       self._get_title_text(p["properties"].get("Name")),
                    "company":     self._get_rich_text(p["properties"].get("Company")),
                    "match_score": p["properties"].get("Match Score", {}).get("number", 0),
                    "status":      p["properties"].get("Status", {}).get("select", {}).get("name"),
                    "apply_url":   p["properties"].get("Apply URL", {}).get("url"),
                }
                for p in results
            ]
        except APIResponseError as e:
            logger.error(f"❌ Failed to fetch jobs: {e}")
            return []

    
    @staticmethod
    def _get_title_text(prop: Optional[Dict]) -> str:
        items = (prop or {}).get("title", [])
        return items[0]["text"]["content"] if items else ""

    @staticmethod
    def _get_rich_text(prop: Optional[Dict]) -> str:
        items = (prop or {}).get("rich_text", [])
        return items[0]["text"]["content"] if items else ""



def push_to_notion_dashboard(
    jobs: List[Dict],
    token: Optional[str] = None,
    database_id: Optional[str] = None,
    data_source_id: Optional[str] = None,
    skip_duplicates: bool = True,
) -> Dict[str, int]:
    dashboard = NotionDashboard(
        token=token,
        database_id=database_id,
        data_source_id=data_source_id,
    )
    now = datetime.now().date().isoformat()
    for job in jobs:
        job.setdefault("status", "Pending")
        job.setdefault("source", "Wuzzuf")
        job.setdefault("posted_date", now)
    return dashboard.add_jobs_batch(jobs, skip_duplicates=skip_duplicates)