"""Postgres + pgvector implementation of the NV-Ingest ``VDB`` operator.

Backed by :class:`langchain_postgres.PGVector` (the maintained replacement for
the deprecated ``langchain_community`` implementation). Records are carried as
:class:`langchain_core.documents.Document` objects throughout, and metadata is
stored as JSONB for efficient filtering.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_postgres import PGVector
from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
from nv_ingest_client.util.vdb.adt_vdb import VDB


logger = logging.getLogger(__name__)


class _UnusableEmbeddings(Embeddings):
    """Placeholder used when no query-side embedder is supplied.

    Ingestion via :meth:`PGVector.add_embeddings` does not call this — it only
    fires if someone tries to run :meth:`PostgresVDB.retrieval` without passing
    an ``embeddings`` instance to the constructor.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError(
            "PostgresVDB has no embeddings function configured. "
            "Pass `embeddings=<Embeddings>` to the constructor to enable retrieval."
        )

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _flatten(records: Iterable) -> Iterable[dict]:
    """Yield record dicts from possibly-nested NV-Ingest output."""
    for item in records:
        if isinstance(item, dict):
            yield item
        elif isinstance(item, list):
            yield from _flatten(item)


class PostgresVDB(VDB):
    """Concrete :class:`VDB` backed by Postgres + pgvector via LangChain.

    Each NV-Ingest record becomes a :class:`Document` whose ``page_content`` is
    the searchable text and whose ``metadata`` carries the original record
    metadata plus ``document_type``. Embeddings produced upstream by the NIM
    pipeline are bulk-loaded into PGVector via ``add_embeddings`` so we don't
    re-run the embedder on the write path.
    """

    def __init__(self, **kwargs: Any) -> None:
        connection_string = kwargs.get("connection_string")
        if not connection_string:
            raise ValueError(
                "PostgresVDB requires a 'connection_string' kwarg "
                "(e.g. postgresql://user:pass@host:5432/dbname)."
            )

        self.collection_name: str = kwargs.get(
            "collection_name", kwargs.get("index_name", "nv_ingest_tabular")
        )
        self.recreate: bool = bool(kwargs.get("recreate", True))
        if not self.recreate:
            # In order to support without recreate:
            # 1. The ingestion should return which tables/columns were added/updated/deleted
            # 2. The implemtation should support be fault tolerant and support incremental ingestion, which is challenging.
            raise ValueError("Recreate False is not supported")
        self.nvidia_api_key = kwargs.get("nvidia_api_key")
        # required for NVIDIAEmbedding call if the endpoint is Nvidia build api.
        self.embedding_base_url = kwargs.get("embedding_base_url")
        self.embedding_model = kwargs.get("embedding_model")
        if self.nvidia_api_key:
            # hosted on build.nvidia.com
            self.embeddings = NVIDIAEmbeddings(
                api_key=self.nvidia_api_key,
                model_name=self.embedding_model,
            )
        elif self.embedding_base_url and self.embedding_model:
            # self deployed nim
            self.embeddings = NVIDIAEmbeddings(
                base_url=self.embedding_base_url,
                model_name=self.embedding_model,
            )
        else:
            self.embeddings = _UnusableEmbeddings()

        self.embeddings: Embeddings = kwargs.get("embeddings") or _UnusableEmbeddings()

        self._store: Optional[PGVector] = None
        super().__init__(**kwargs)

    def _build_store(self, pre_delete_collection: bool) -> PGVector:
        return PGVector(
            embeddings=self.embeddings,
            connection=self.connection_string,
            collection_name=self.collection_name,
            pre_delete_collection=pre_delete_collection,
            use_jsonb=True,
            create_extension=True,
        )

    def _get_store(self) -> PGVector:
        if self._store is None:
            self._store = self._build_store(pre_delete_collection=False)
        return self._store

    def create_index(self, **kwargs: Any) -> str:
        """Ensure the pgvector extension, LangChain tables, and collection exist."""
        recreate = bool(kwargs.get("recreate", self.recreate))
        self._store = self._build_store(pre_delete_collection=recreate)
        return self.collection_name

    def write_to_index(
        self,
        records: list,
        batch_size: int = 500,
        **kwargs: Any,
    ) -> int:
        """Bulk-insert NV-Ingest records, returning the number of rows written."""
        store = self._get_store()
        documents: list[Document] = []
        embeddings: list[list[float]] = []
        skipped = 0

        for record in _flatten(records):
            metadata = record.get("metadata") or {}
            embedding = metadata.get("embedding")
            if not embedding:
                skipped += 1
                continue

            doc_metadata = {
                "document_type": record.get("document_type"),
                **{k: v for k, v in metadata.items() if k != "embedding"},
            }
            documents.append(
                Document(page_content=record["text"] or "", metadata=doc_metadata)
            )
            embeddings.append([float(v) for v in embedding])

        if not documents:
            logger.info(
                "PostgresVDB.write_to_index: no rows to insert (skipped %d)", skipped
            )
            return 0

        inserted = 0
        for start in range(0, len(documents), batch_size):
            chunk_docs = documents[start : start + batch_size]
            chunk_embs = embeddings[start : start + batch_size]
            store.add_embeddings(
                texts=[d.page_content for d in chunk_docs],
                embeddings=chunk_embs,
                metadatas=[d.metadata for d in chunk_docs],
            )
            inserted += len(chunk_docs)

        logger.info(
            "PostgresVDB.write_to_index: inserted %d rows into %s (skipped %d)",
            inserted,
            self.collection_name,
            skipped,
        )
        return inserted

    def retrieval(
        self,
        queries: list,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[list[dict]]:
        """Cosine-similarity k-NN search for each query string.

        Requires the constructor to have been given an ``embeddings`` instance;
        the placeholder will raise otherwise.
        """
        store = self._get_store()
        results: list[list[dict]] = []
        for query in queries:
            hits = store.similarity_search_with_score(query, k=top_k)
            # PGVector returns distance here; convert to a similarity-style score where
            # higher is better by applying (1 - distance).
            results.append(
                [
                    {
                        "text": doc.page_content,
                        "metadata": doc.metadata,
                        "score": float(1 - score),
                    }
                    for doc, score in hits
                ]
            )
        return results

    def run(self, records: list) -> int:
        """Create the collection if needed, then write records to it."""
        self.create_index()
        return self.write_to_index(records)

    def close(self) -> None:
        # PGVector manages its SQLAlchemy engine internally; drop the reference
        # so subsequent calls re-bind to a fresh session.
        self._store = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
