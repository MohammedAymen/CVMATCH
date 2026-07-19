import fitz
from pathlib import Path
from langchain_core.documents import Document
from core.logger import logger
#from core.config import Settings
from typing import List
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import List, Dict, Any 

def _clean_text(text: str) -> str:
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
    except Exception as e:
        logger.error(f"Error parsing CV via PyMuPDF: {str(e)}")
        return ""
        


def chunk_cv(cv_text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:

    if not cv_text or not cv_text.strip():
        logger.warning("CV text is empty. No chunks generated.")
        return []

    try:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_text(cv_text)
        logger.info(f"CV split into {len(chunks)} chunks (chunk_size={chunk_size}, overlap={overlap})")
        return chunks
    except Exception as e:
        logger.error(f"Error chunking CV text: {str(e)}")
        return [cv_text]  
    


def load_cv_documents(cv_path: str,chunk_size: int = 500,overlap: int = 100) -> List[Dict[str, Any]]:
    
    full_text = load_cv(cv_path)
    if not full_text:
        return []
    
    chunks = chunk_cv(full_text, chunk_size, overlap)
    
    documents = []
    for i, chunk in enumerate(chunks):
        documents.append({
            "text": chunk,
            "metadata": {
                "source": "cv",
                "chunk_index": i,
                "total_chunks": len(chunks)
            }
        })
    return documents