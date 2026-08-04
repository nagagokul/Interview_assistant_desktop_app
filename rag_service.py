"""Top-level re-export for RAGManager."""
from src.services.rag_service import LocalVectorStore, RAGManager, chunk_text

__all__ = ["LocalVectorStore", "RAGManager", "chunk_text"]
