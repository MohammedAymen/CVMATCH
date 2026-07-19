# core/cache.py

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.logger import logger


class ScoreCache:

    _lock = threading.Lock()  

    def __init__(self, cache_path: str = "data/cache/score_cache.json"):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache_path.exists():
            self._write({})

    @staticmethod
    def make_key(
        job_title: str,
        job_description: str,
        job_requirements: str,
        cache_scope: str = "",
    ) -> str:
        
        raw = f"{cache_scope}|{job_title.strip()}|{job_description.strip()}|{job_requirements.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._read()
            return data.get(key)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            
            clean_value = {k: v for k, v in value.items() if not k.startswith("_")}
            data[key] = clean_value
            self._write(data)
            logger.debug(f"💾 Cached score result (key={key[:12]}...)")

    def clear(self) -> None:
        with self._lock:
            self._write({})
            logger.info("🗑️ Score cache cleared")