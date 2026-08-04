"""
RAGManager — lightweight local document chunking + vector index.

Default backend: pure-Python/NumPy hash embeddings persisted as JSON
(no C++ build tools, works on Python 3.14 / 8GB RAM machines).

Optional backend: ChromaDB when installed and importable.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any

import numpy as np

from src.core.config import CONFIG
from src.core.context import CTX
from src.core.event_bus import BUS, EventType
from src.core.logging_setup import get_logger
from src.core.paths import chroma_dir, data_dir, documents_dir
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


class HashEmbedder:
    """Zero-dependency bag-of-hashes embeddings (no torch / no MSVC)."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        if not tokens:
            return vec
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = float(np.linalg.norm(vec)) or 1.0
        return vec / norm

    def embed_many(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self.embed(t) for t in texts])


class LocalVectorStore:
    """JSON-persisted cosine index — no native extensions required."""

    def __init__(self, path: Path, dim: int = 384) -> None:
        self.path = path
        self.embedder = HashEmbedder(dim=dim)
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict[str, Any]] = []
        self.vectors: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.ids = list(payload.get("ids", []))
            self.documents = list(payload.get("documents", []))
            self.metadatas = list(payload.get("metadatas", []))
            vectors = payload.get("vectors", [])
            if vectors:
                self.vectors = np.asarray(vectors, dtype=np.float32)
            else:
                self.vectors = self.embedder.embed_many(self.documents)
            log.info("Loaded local vector store (%d chunks) from %s", len(self.ids), self.path)
        except Exception:  # noqa: BLE001
            log.exception("Failed loading local vector store — starting empty")
            self.ids, self.documents, self.metadatas = [], [], []
            self.vectors = np.zeros((0, self.embedder.dim), dtype=np.float32)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ids": self.ids,
            "documents": self.documents,
            "metadatas": self.metadatas,
            "vectors": self.vectors.tolist(),
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def delete_where_doc_id(self, doc_id: str) -> None:
        keep = [i for i, m in enumerate(self.metadatas) if m.get("doc_id") != doc_id]
        if len(keep) == len(self.ids):
            return
        self.ids = [self.ids[i] for i in keep]
        self.documents = [self.documents[i] for i in keep]
        self.metadatas = [self.metadatas[i] for i in keep]
        self.vectors = self.vectors[keep] if keep else np.zeros((0, self.embedder.dim), dtype=np.float32)

    def add(self, ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
        vecs = self.embedder.embed_many(documents)
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)
        self.vectors = np.vstack([self.vectors, vecs]) if self.vectors.size else vecs
        self.save()

    def query(self, text: str, top_k: int = 4) -> tuple[list[str], list[dict[str, Any]]]:
        if not self.documents:
            return [], []
        q = self.embedder.embed(text)
        # Cosine similarity (vectors are L2-normalized)
        scores = self.vectors @ q
        k = min(top_k, len(scores))
        idx = np.argpartition(-scores, kth=k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        docs = [self.documents[i] for i in idx]
        metas = [self.metadatas[i] for i in idx]
        return docs, metas

    def count(self) -> int:
        return len(self.ids)


class RAGManager:
    def __init__(self) -> None:
        self.config = CONFIG.rag
        self._lock = threading.RLock()
        self._collection: Any = None
        self._local: LocalVectorStore | None = None
        self._backend = "none"
        self._init_store()

    def _init_store(self) -> None:
        # Prefer pure-local store (always works). Optionally try Chroma.
        try:
            store_path = data_dir() / "rag_index.json"
            self._local = LocalVectorStore(store_path, dim=384)
            self._backend = "local"
            log.info("RAG backend=local chunks=%d", self._local.count())
        except Exception:  # noqa: BLE001
            log.exception("Local RAG store failed to init")
            self._local = None

        try:
            import chromadb
            from chromadb.config import Settings

            class _HashEF:
                def __init__(self) -> None:
                    self._h = HashEmbedder(384)

                def name(self) -> str:
                    return "hash-embed-v1"

                def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A003
                    return [self._h.embed(t).tolist() for t in input]

            client = chromadb.PersistentClient(
                path=str(chroma_dir()),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = client.get_or_create_collection(
                name=self.config.collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=_HashEF(),
            )
            self._backend = "chroma+local"
            log.info("Chroma also available (optional) count=%s", self._collection.count())
        except Exception:  # noqa: BLE001
            # Expected on Python 3.14 / machines without MSVC — local store is enough
            log.info("ChromaDB not available — using local JSON vector store only")
            self._collection = None

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
        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {"doc_id": doc_id, "filename": src.name, "doc_type": dtype, "chunk": i}
            for i in range(len(chunks))
        ]

        with self._lock:
            if self._local is not None:
                self._local.delete_where_doc_id(doc_id)
                self._local.add(ids=ids, documents=chunks, metadatas=metadatas)

            if self._collection is not None:
                try:
                    existing = self._collection.get(where={"doc_id": doc_id})
                    if existing and existing.get("ids"):
                        self._collection.delete(ids=existing["ids"])
                    self._collection.add(ids=ids, documents=chunks, metadatas=metadatas)
                except Exception:  # noqa: BLE001
                    log.exception("Chroma ingest failed (local store still updated)")

        record = get_db().upsert_document(
            filename=src.name,
            path=str(dest),
            doc_type=dtype,
            chunk_count=len(chunks),
            checksum=checksum,
            doc_id=doc_id,
        )

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
        log.info("Indexed %s type=%s chunks=%d backend=%s", src.name, dtype, len(chunks), self._backend)
        return {
            "doc_id": record.id,
            "filename": record.filename,
            "chunks": record.chunk_count,
            "doc_type": record.doc_type,
        }

    def query(self, text: str, top_k: int | None = None) -> str:
        if not text.strip():
            return ""
        k = top_k or self.config.top_k
        docs: list[str] = []
        metas: list[dict[str, Any]] = []

        with self._lock:
            if self._local is not None and self._local.count() > 0:
                docs, metas = self._local.query(text, top_k=k)
            elif self._collection is not None:
                result = self._collection.query(query_texts=[text], n_results=k)
                docs = (result.get("documents") or [[]])[0]
                metas = (result.get("metadatas") or [[]])[0]

        blocks = [f"[{(meta or {}).get('filename', 'doc')}] {doc}" for doc, meta in zip(docs, metas)]
        joined = "\n\n".join(blocks)
        CTX.rag_context = joined
        return joined

    def refresh_context_from_latest(self) -> str:
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
            if self._local is not None:
                self._local.delete_where_doc_id(doc_id)
                self._local.save()
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
        return path.read_text(encoding="utf-8", errors="ignore")

    def _extract_pdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:  # noqa: BLE001
            log.exception("PDF extract failed for %s", path)
            return ""

    def _extract_docx(self, path: Path) -> str:
        try:
            import docx

            document = docx.Document(str(path))
            return "\n".join(p.text for p in document.paragraphs)
        except Exception:  # noqa: BLE001
            log.exception("DOCX extract failed for %s", path)
            return ""
