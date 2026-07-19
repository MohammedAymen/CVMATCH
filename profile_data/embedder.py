import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from core.config import settings
from core.logger import logger

DEFAULT_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
DEFAULT_BATCH_SIZE = 32


NOMIC_DOCUMENT_PREFIX = "search_document: "
NOMIC_QUERY_PREFIX = "search_query: "


Document = Dict[str, Any]
MetadataValue = Union[str, int, float, bool]


class ProfileEmbedder:
   

    def __init__(
        self,
        collection_name: str = "profile_chunks",
        model_name: Optional[str] = None,
        persist_directory: str = "data/chroma_db",
        embedding_dim: Optional[int] = None,
        device: Optional[str] = None,
        reset_collection: bool = False,
        document_prefix: Optional[str] = None,
        query_prefix: Optional[str] = None,
    ):
        
        self.default_batch_size = getattr(settings, "embedder_batch_size", DEFAULT_BATCH_SIZE)
        self.default_top_k = getattr(settings, "embedder_top_k", 5)
        
        self.model_name = model_name or getattr(settings, "embedding_model", DEFAULT_MODEL_NAME)
        self.embedding_dim = embedding_dim

        is_nomic = "nomic-embed-text" in self.model_name.lower()
        self.document_prefix = (
            document_prefix if document_prefix is not None
            else (NOMIC_DOCUMENT_PREFIX if is_nomic else "")
        )
        self.query_prefix = (
            query_prefix if query_prefix is not None
            else (NOMIC_QUERY_PREFIX if is_nomic else "")
        )
        if is_nomic and document_prefix is None:
            logger.debug("Nomic model detected: auto-applying search_document:/search_query: prefixes")

        logger.info(f"Loading embedding model: {self.model_name}")
        model_kwargs: Dict[str, Any] = {"trust_remote_code": True}
        if device:
            model_kwargs["device"] = device
        if self.embedding_dim:
            logger.info(f"Using Matryoshka trick with embedding dimension: {self.embedding_dim}")
            model_kwargs["truncate_dim"] = self.embedding_dim
        self.model = SentenceTransformer(self.model_name, **model_kwargs)

      
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Persisting ChromaDB to: {self.persist_directory.absolute()}")

        self.chroma_client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        if reset_collection:
            try:
                self.chroma_client.delete_collection(collection_name)
                logger.debug(f"Deleted existing collection '{collection_name}' (reset_collection=True)")
            except Exception:
                pass  # المجموعة غير موجودة من الأساس - مفيش مشكلة

        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"Using ChromaDB collection '{collection_name}' "
            f"({self.collection.count()} existing chunks)"
        )


    def _encode(self, texts: List[str], batch_size: int, show_progress: bool) -> List[List[float]]:
        return self.model.encode(
            texts, batch_size=batch_size, show_progress_bar=show_progress
        ).tolist()

    @staticmethod
    def _sanitize_metadata(metadata: Optional[Dict]) -> Dict[str, MetadataValue]:
       
        if not metadata:
            return {}

        clean: Dict[str, MetadataValue] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                clean[key] = value
            elif isinstance(value, (list, tuple, set)):
                clean[key] = ", ".join(str(v) for v in value)
            else:
                clean[key] = str(value)
        return clean

    @staticmethod
    def _make_id(text: str, metadata: Dict[str, MetadataValue]) -> str:
       
        basis = repr(sorted(metadata.items())) + "|" + text
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]

   
    def embed_and_store(
        self,
        chunks: List[str],
        metadatas: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        show_progress: bool = False,
    ) -> int:
     
        if not chunks:
            logger.warning("No chunks provided to embed_and_store")
            return 0

        if metadatas is None:
            metadatas = [{} for _ in chunks]
        elif len(metadatas) != len(chunks):
            raise ValueError("metadatas length must match chunks length")

        clean_metadatas = [self._sanitize_metadata(m) for m in metadatas]

        if ids is None:
            ids = [self._make_id(chunk, meta) for chunk, meta in zip(chunks, clean_metadatas)]
        elif len(ids) != len(chunks):
            raise ValueError("ids length must match chunks length")

        prefixed_texts = (
            [self.document_prefix + c for c in chunks] if self.document_prefix else chunks
        )

        logger.info(f"Embedding {len(chunks)} chunks (model={self.model_name})...")
        embeddings = self._encode(prefixed_texts, batch_size, show_progress)

        
        self.collection.upsert(
            embeddings=embeddings,
            documents=chunks,  
            ids=ids,
            metadatas=clean_metadatas,
        )
        logger.info(
            f"Stored {len(chunks)} embeddings in ChromaDB "
            f"(collection now has {self.collection.count()})"
        )
        return len(chunks)

    def embed_and_store_documents(
        self,
        documents: List[Document],
        source: Optional[str] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        show_progress: bool = False,
    ) -> int:
     
        if not documents:
            logger.warning("No documents provided to embed_and_store_documents")
            return 0

        chunks: List[str] = []
        metadatas: List[Dict] = []
        for doc in documents:
            text = (doc.get("text") or "").strip()
            if not text:
                continue
            meta = dict(doc.get("metadata") or {})
            if source:
                meta.setdefault("source", source)
            chunks.append(text)
            metadatas.append(meta)

        return self.embed_and_store(
            chunks, metadatas=metadatas, batch_size=batch_size, show_progress=show_progress
        )

 
    def retrieve_similar_chunks(
        self,
        query: str,
        top_k: int = 5,
        filter_metadata: Optional[Dict] = None,
    ) -> List[Dict]:
      
        count = self.collection.count()
        if count == 0:
            logger.warning("Collection is empty, returning no results")
            return []

        top_k = min(top_k, count)

        prefixed_query = self.query_prefix + query if self.query_prefix else query
        query_embedding = self._encode([prefixed_query], batch_size=1, show_progress=False)

        
        where = filter_metadata if filter_metadata else None

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        retrieved: List[Dict] = []
        documents = results.get("documents") or [[]]
        if documents and documents[0]:
            distances = results.get("distances") or [[]]
            metadatas = results.get("metadatas") or [[]]
            for i, doc in enumerate(documents[0]):
                retrieved.append({
                    "text": doc,
                    "metadata": metadatas[0][i] if metadatas[0] else {},
                    "similarity_score": 1 - distances[0][i],
                })
        return retrieved


    def get_collection_stats(self) -> Dict[str, Any]:
        """إحصائيات عن قاعدة البيانات المتجهية."""
        return {
            "collection_name": self.collection.name,
            "total_chunks": self.collection.count(),
            "embedding_model": self.model_name,
            "embedding_dim": self.embedding_dim,
            "persist_directory": str(self.persist_directory.absolute()),
        }

    def delete_collection(self) -> None:
        
        try:
            self.chroma_client.delete_collection(self.collection.name)
            logger.info(f"Deleted collection '{self.collection.name}'")
        except Exception as e:
            logger.warning(f"Could not delete collection: {e}")

    def clear(self) -> None:
       
        name = self.collection.name
        try:
            self.chroma_client.delete_collection(name)
        except Exception:
            pass
        self.collection = self.chroma_client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"Cleared all data from collection '{name}'")

    def __repr__(self) -> str:
        return (
            f"ProfileEmbedder(collection='{self.collection.name}', "
            f"model='{self.model_name}', dim={self.embedding_dim}, "
            f"chunks={self.collection.count()})"
        )



def build_profile_embeddings(
    cv_documents: Union[List[str], List[Document]],
    github_documents: Union[List[str], List[Document]],
    collection_name: str = "profile_chunks",
    model_name: Optional[str] = None,
    embedding_dim: Optional[int] = None,
    persist_directory: str = "data/chroma_db",
    reset_collection: bool = True,
) -> ProfileEmbedder:
   
    def _normalize(items: Union[List[str], List[Document]], source: str) -> List[Document]:
        normalized: List[Document] = []
        for item in items:
            if isinstance(item, str):
                normalized.append({"text": item, "metadata": {"source": source}})
            else:
                meta = dict(item.get("metadata") or {})
                meta.setdefault("source", source)
                normalized.append({"text": item.get("text", ""), "metadata": meta})
        return normalized

    embedder = ProfileEmbedder(
        collection_name=collection_name,
        model_name=model_name,
        embedding_dim=embedding_dim,
        persist_directory=persist_directory,
        reset_collection=reset_collection,
    )

    documents = _normalize(cv_documents, "cv") + _normalize(github_documents, "github")
    stored = embedder.embed_and_store_documents(documents, show_progress=True)

    logger.info(f"Profile embeddings built: {stored} chunks total")
    return embedder