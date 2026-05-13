# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Thin operators around the nv-ingest-client VDB abstraction.

Mirrors :mod:`nemo_retriever.vdb.operators` but routes the ``"lancedb"``
backend through :class:`vdb.lancedb.LanceDB` so the upsert / top-level
``id`` patches live inside GSF.
"""

from __future__ import annotations

from typing import Any

from nemo_retriever.graph.abstract_operator import AbstractOperator
from nemo_retriever.vdb.adt_vdb import VDB
from nemo_retriever.vdb.factory import get_vdb_op_cls
from nemo_retriever.vdb.records import to_client_vdb_records
from nemo_retriever.vdb.sidecar_metadata import (
    apply_sidecar_metadata_to_client_batches,
    build_sidecar_lookup,
    materialize_sidecar_dataframe,
    split_sidecar_from_vdb_kwargs,
)

from .lancedb import LanceDB


def _construct_vdb(
    *,
    vdb: VDB | None = None,
    vdb_op: str | None = None,
    vdb_kwargs: dict[str, Any] | None = None,
) -> VDB:
    if vdb is not None and vdb_op is not None:
        raise ValueError("Pass either vdb or vdb_op, not both.")
    if vdb is None and vdb_op is None:
        raise ValueError("Either vdb or vdb_op is required.")

    if vdb is not None:
        return vdb

    op_name = str(vdb_op)
    cleaned_kwargs = dict(vdb_kwargs or {})
    # Route lancedb through the local subclass so the id-column + upsert
    # patches are used even when callers configure the operator with a
    # string ``vdb_op="lancedb"`` rather than passing a constructed VDB.
    if op_name == "lancedb":
        return LanceDB(**cleaned_kwargs)
    return get_vdb_op_cls(op_name)(**cleaned_kwargs)


class IngestVdbOperator(AbstractOperator):
    """Upload already-embedded graph output through an nv-ingest-client VDB."""

    def __init__(
        self,
        *,
        vdb: VDB | None = None,
        vdb_op: str | None = None,
        vdb_kwargs: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(vdb_kwargs or {})
        clean_kwargs, sidecar = split_sidecar_from_vdb_kwargs(merged)
        super().__init__(vdb=vdb, vdb_op=vdb_op, vdb_kwargs=clean_kwargs)
        self._vdb_kwargs = clean_kwargs
        self._sidecar_spec = sidecar
        self._sidecar_lookup: dict[str, dict[str, Any]] | None = None
        if sidecar is not None:
            _df = materialize_sidecar_dataframe(sidecar)
            self._sidecar_lookup = build_sidecar_lookup(
                _df,
                sidecar["meta_source_field"],
                sidecar["meta_fields"],
            )
        self._vdb = _construct_vdb(vdb=vdb, vdb_op=vdb_op, vdb_kwargs=clean_kwargs)

    def preprocess(self, data: Any, **kwargs: Any) -> Any:
        return data

    def process(self, data: Any, **kwargs: Any) -> Any:
        # Compatibility shim: graph_pipeline emits flat embedded rows, while
        # nv-ingest-client VDB.run still expects nested NV-Ingest records.
        records = to_client_vdb_records(data)
        if self._sidecar_spec is not None and self._sidecar_lookup is not None:
            records = apply_sidecar_metadata_to_client_batches(
                records,
                lookup=self._sidecar_lookup,
                meta_fields=self._sidecar_spec["meta_fields"],
                join_key=self._sidecar_spec["meta_join_key"],
            )
        self._vdb.run(records)
        return data

    def postprocess(self, data: Any, **kwargs: Any) -> Any:
        return data


class UpsertVdbOperator(AbstractOperator):
    """Incrementally update an existing VDB table on a stable row key.

    Unlike :class:`IngestVdbOperator` (which orchestrates create_index +
    write_to_index, optionally overwriting the whole table), this operator
    calls ``vdb.upsert(records, ...)`` so that only rows whose ``key`` is in
    ``records`` are touched. Rows in the table that are not referenced are
    left untouched, and existing rows that match by ``key`` are replaced.

    The underlying VDB implementation must expose an ``upsert(records, key)``
    method; currently this is implemented by
    :class:`~nemo_retriever.vdb.lancedb.LanceDB`. Passing an unsupported VDB
    raises :class:`NotImplementedError` at construction time so misuse fails
    fast rather than silently no-oping at runtime.
    """

    def __init__(
        self,
        *,
        vdb: VDB | None = None,
        vdb_op: str | None = None,
        vdb_kwargs: dict[str, Any] | None = None,
        key: str = "id",
        table_name: str | None = None,
    ) -> None:
        merged = dict(vdb_kwargs or {})
        clean_kwargs, sidecar = split_sidecar_from_vdb_kwargs(merged)
        super().__init__(
            vdb=vdb,
            vdb_op=vdb_op,
            vdb_kwargs=clean_kwargs,
            key=key,
            table_name=table_name,
        )
        self._vdb_kwargs = clean_kwargs
        self._sidecar_spec = sidecar
        self._sidecar_lookup: dict[str, dict[str, Any]] | None = None
        if sidecar is not None:
            _df = materialize_sidecar_dataframe(sidecar)
            self._sidecar_lookup = build_sidecar_lookup(
                _df,
                sidecar["meta_source_field"],
                sidecar["meta_fields"],
            )
        self._vdb = _construct_vdb(vdb=vdb, vdb_op=vdb_op, vdb_kwargs=clean_kwargs)
        if not hasattr(self._vdb, "upsert"):
            raise NotImplementedError(
                f"VDB backend {type(self._vdb).__name__!r} does not implement upsert(); "
                "only LanceDB is currently supported for incremental updates."
            )
        self._key = key
        self._table_name = table_name

    def preprocess(self, data: Any, **kwargs: Any) -> Any:
        return data

    def process(self, data: Any, **kwargs: Any) -> Any:
        records = to_client_vdb_records(data)
        if self._sidecar_spec is not None and self._sidecar_lookup is not None:
            records = apply_sidecar_metadata_to_client_batches(
                records,
                lookup=self._sidecar_lookup,
                meta_fields=self._sidecar_spec["meta_fields"],
                join_key=self._sidecar_spec["meta_join_key"],
            )
        self._vdb.upsert(records, table_name=self._table_name, key=self._key)
        return data

    def postprocess(self, data: Any, **kwargs: Any) -> Any:
        return data
