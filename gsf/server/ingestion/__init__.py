"""Tabular ingestion building blocks for the GSF server.

Hosts :mod:`server.ingestion.params` — shared :class:`EmbedParams` /
:class:`VdbUploadParams` builders used by both the PATCH-driven
incremental flow and the dev-tools ingest CLI.

The Neo4j → embedding-ready DataFrame helpers (full or filtered by
``node_ids``) now live in
:mod:`nemo_retriever.tabular_data.ingestion.embeddings`.
"""
