"""
RAGManager — lightweight local document chunking + ChromaDB vector index.

Optimized for 8GB RAM: uses in-process Chroma with a small sentence-transformer
embedding model (or hash embeddings fallback if torch is too heavy).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Iterable

from src.core.config import CONFIG
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.core.paths import chroma_dir, documents_dir
from src.data.database import get_db

log = get_logger("rag")


def _guess_doc_type(filename: str) -> str:
    lower = filename.lower()
    if "resume" in lower or "cv" in lower:
        return "resume"
    if "job" in lower or "jd" in lower or "description" in lower:
        return "jd"
    if lower.endswith(".pdf"):
        return "pdf"
    return "notes"


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        # Prefer break on sentence boundary
        window = text[start:end]
        if end < n:
            for sep in (". ", "? ", "! ", "\n"):
                idx = window.rfind(sep)
                if idx > chunk_size // 3:
                    end = start + idx + len(sep)
                    window = text[start:end]
                    break
        chunks.append(window.strip())
        if end >= n:
            break
        start = max(0, end - overlap)
    return [c for c in chunks if c]


class HashEmbeddingFunction:
    """
    Zero-dependency bag-of-hashes embedding for ultra-low RAM machines.
    Dimensionality is fixed; quality is lower than MiniLM but keeps the app fluid.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def name(self) -> str:
        return "hash-embed-v1"

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A003 — chroma API
        return [self._embed(t) for t in input]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        if not tokens:
            return vec
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        # L2 normalize
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]


class RAGManager:
    def __init__(self) -> None:
        self.config = CONFIG.rag
        self._lock = threading.RLock()
        self._client: Any = None
        self._collection: Any = None
        self._embedder: Any = None
        self._init_store()

    def _init_store(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings

            self._embedder = self._build_embedder()
            self._client = chromadb.PersistentClient(
                path=str(chroma_dir()),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = self._client.get_or_create_collection(
                name=self.config.collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=self._embedder,
            )
            log.info(
                "Chroma ready collection=%s count=%s",
                self.config.collection_name,
                self._collection.count(),
            )
        except Exception:  # noqa: BLE001
            log.exception("ChromaDB init failed — RAG disabled")
            self._client = None
            self._collection = None

    def _build_embedder(self) -> Any:
        # Try lightweight sentence-transformers; fall back to hash embedder
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            ef = SentenceTransformerEmbeddingFunction(model_name=self.config.embed_model)
            log.info("Using SentenceTransformer embeddings: %s", self.config.embed_model)
            return ef
        except Exception:  # noqa: BLE001
            log.warning("SentenceTransformer unavailable — using hash embeddings (low RAM mode)")
            return HashEmbeddingFunction(dim=384)

    # ---- document IO ----

    def ingest_file(self, path: str | Path, doc_type: str | None = None) -> dict[str, Any]:
        src = Path(path)
        if not src.is_file():
            raise FileNotFoundError(str(src))

        text = self._extract_text(src)
        if not text.strip():
            raise ValueError(f"No extractable text in {src.name}")

        dtype = doc_type or _guess_doc_type(src.name)
        dest = documents_dir() / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)

        chunks = chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        doc_id = checksum[:16]

        with self._lock:
            if self._collection is not None:
                # Remove prior chunks for same doc
                try:
                    existing = self._collection.get(where={"doc_id": doc_id})
                    if existing and existing.get("ids"):
                        self._collection.delete(ids=existing["ids"])
                except Exception:  # noqa: BLE001
                    pass

                ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
                metadatas = [
                    {"doc_id": doc_id, "filename": src.name, "doc_type": dtype, "chunk": i}
                    for i in range(len(chunks))
                ]
                self._collection.add(ids=ids, documents=chunks, metadatas=metadatas)

        record = get_db().upsert_document(
            filename=src.name,
            path=str(dest),
            doc_type=dtype,
            chunk_count=len(chunks),
            checksum=checksum,
            doc_id=doc_id,
        )

        # Promote resume/JD into context slots
        if dtype == "resume":
            CTX.resume_summary = text[:6000]
        elif dtype == "jd":
            CTX.job_description = text[:6000]

        BUS.publish(
            EventType.DOCUMENT_INDEXED,
            doc_id=doc_id,
            filename=src.name,
            chunks=len(chunks),
            doc_type=dtype,
        )
        BUS.publish(EventType.STATUS, message=f"Indexed {src.name} ({len(chunks)} chunks)")
        log.info("Indexed %s type=%s chunks=%d", src.name, dtype, len(chunks))
        return {
            "doc_id": record.id,
            "filename": record.filename,
            "chunks": record.chunk_count,
            "doc_type": record.doc_type,
        }

    def query(self, text: str, top_k: int | None = None) -> str:
        if not text.strip() or self._collection is None:
            return ""
        k = top_k or self.config.top_k
        with self._lock:
            result = self._collection.query(query_texts=[text], n_results=k)
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        blocks: list[str] = []
        for doc, meta in zip(docs, metas):
            name = (meta or {}).get("filename", "doc")
            blocks.append(f"[{name}] {doc}")
        joined = "\n\n".join(blocks)
        CTX.rag_context = joined
        return joined

    def refresh_context_from_latest(self) -> str:
        """Pull RAG context using latest transcript + OCR as the query."""
        q = " ".join(
            [
                CTX.transcript_block(8),
                CTX.latest_ocr_text[:500],
            ]
        ).strip()
        if not q:
            q = "interview preparation resume skills projects"
        return self.query(q)

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "doc_type": d.doc_type,
                "chunks": d.chunk_count,
                "indexed_at": d.indexed_at.isoformat(),
            }
            for d in get_db().list_documents()
        ]

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            if self._collection is not None:
                try:
                    existing = self._collection.get(where={"doc_id": doc_id})
                    if existing and existing.get("ids"):
                        self._collection.delete(ids=existing["ids"])
                except Exception:  # noqa: BLE001
                    log.exception("Failed deleting chroma chunks for %s", doc_id)
        get_db().delete_document(doc_id)

    # ---- extractors ----

    def _extract_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".markdown", ".csv", ".json", ".py", ".java", ".cpp", ".c", ".sql"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix in {".docx"}:
            return self._extract_docx(path)
        # Best-effort binary decode
        return path.read_text(encoding="utf-8", errors="ignore")

    def _extract_pdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            return "\n".join(pages)
        except Exception:  # noqa: BLE001
            log.exception("PDF extract failed for %s", path)
            return ""

    def _extract_docx(self, path: Path) -> str:
        try:
            import docx  # python-docx

            document = docx.Document(str(path))
            return "\n".join(p.text for p in document.paragraphs)
        except Exception:  # noqa: BLE001
            log.exception("DOCX extract failed for %s", path)
            return ""
