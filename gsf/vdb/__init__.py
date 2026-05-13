"""Local copy of nemo_retriever's VDB layer with incremental-upsert support.

Mirrors :mod:`nemo_retriever.vdb` so the GSF server can drive
``LanceDB.upsert`` and :class:`UpsertVdbOperator` without depending on a
specific fork of ``nemo_retriever``. The base ``VDB`` interface,
``get_vdb_op_cls`` factory and sidecar/records helpers are still imported
from ``nemo_retriever`` (unchanged upstream).
"""

from .lancedb import LanceDB
from .operators import (
    IngestVdbOperator,
    UpsertVdbOperator,
)

__all__ = [
    "LanceDB",
    "IngestVdbOperator",
    "UpsertVdbOperator",
]
