from typing import List, Dict, Optional, Any
from pathlib import Path

from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings as ChromaSettings

from core.logger import logger
from core.config import Settings


