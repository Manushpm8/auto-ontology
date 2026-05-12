"""Tabular ingestion building blocks for the GSF server.

Hosts:

* :mod:`server.ingestion.embeddings` — Neo4j → embedding-ready DataFrame
  (full or filtered by ``node_ids``).
* :mod:`server.ingestion.params` — shared :class:`EmbedParams` /
  :class:`VdbUploadParams` builders used by both the PATCH-driven
  incremental flow and the dev-tools ingest CLI.
"""
