# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared embed/VDB parameter builders for the GSF tabular pipeline.

Lives in the production ``server.ingestion`` package (not in ``dev-tools``)
so that runtime code such as :mod:`server.datasources.vector_sync` can build
``EmbedParams`` / ``VdbUploadParams`` without depending on a CLI script. The
dev-tools ingest CLI imports the same helpers so both paths use identical
endpoints, model name, and LanceDB target.
"""

from __future__ import annotations

import os

from nemo_retriever.params import EmbedParams, VdbUploadParams


_DEFAULT_EMBED_INVOKE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_EMBED_MODEL = "nvidia/llama-nemotron-embed-1b-v2"
_DEFAULT_LANCEDB_URI = "lancedb"
_DEFAULT_LANCEDB_TABLE = "nv-ingest-tabular"


def require_nvidia_api_key() -> str:
    """Return ``NVIDIA_API_KEY`` from the environment or raise a friendly error.

    Resolved lazily so importing this module from the FastAPI process does
    not crash the server when the key is absent (catalog routes that never
    re-embed should still work). The PATCH path and the dev-tools ingest
    CLI both call this before kicking off an embed run.
    """
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "NVIDIA_API_KEY is not set. "
            "Export it before running:\n\n"
            "    export NVIDIA_API_KEY='nvapi-...'\n\n"
            "Get your key at https://build.nvidia.com"
        )
    return key


def get_embed_params() -> EmbedParams:
    """Build the :class:`EmbedParams` used by tabular re-embedding."""
    return EmbedParams(
        embed_invoke_url=os.environ.get(
            "GSF_EMBED_INVOKE_URL", _DEFAULT_EMBED_INVOKE_URL
        ),
        model_name=os.environ.get("GSF_EMBED_MODEL", _DEFAULT_EMBED_MODEL),
        api_key=require_nvidia_api_key(),
        embed_modality="text",
    )


def get_vdb_params() -> VdbUploadParams:
    """Build :class:`VdbUploadParams` pointing at the GSF LanceDB table.

    ``vdb_kwargs`` are forwarded straight to
    :class:`vdb.lancedb.LanceDB`.
    """
    return VdbUploadParams(
        vdb_op="lancedb",
        vdb_kwargs={
            "uri": os.environ.get("GSF_LANCEDB_URI", _DEFAULT_LANCEDB_URI),
            "table_name": os.environ.get("GSF_LANCEDB_TABLE", _DEFAULT_LANCEDB_TABLE),
            "overwrite": True,
        },
    )
