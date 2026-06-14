import fitz
from pathlib import Path
#from langchain.schema import Document
from core.logger import logger
#from core.config import Settings
from typing import List
import re

def _clean_text(text: str) -> str:
    text = re.sub(r"[•▪►■○*-]", "• ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def load_cv(cv_path: str) -> str:
    path = Path(cv_path)

    if not path.exists():
        logger.warning(f"CV file not found: {path}")
        return ""
    
    text = ""


    try:
        doc = fitz.open(str(path))
        for page in doc:
            text += page.get_text()
        doc.close()
        logger.info(f"CV extracted via PyMuPDF: {len(text)} chars")
        return _clean_text(text)
    except ImportError:
        logger.error("PyMuPDF not installed. Install it for better CV parsing.")
        return ""


def chunk_cv(cv_text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
    """
    Split CV text into overlapping chunks for RAG retrieval.
    Each chunk = ~chunk_size characters with overlap.
    """
    if not cv_text:
        return []

    chunks = []
    start = 0
    text_length = len(cv_text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = cv_text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap
    logger.debug(f"CV chunked into {len(chunks)} pieces")
    return chunks