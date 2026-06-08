# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Singleton NeMo Retriever wired to the app pgvector store."""

from __future__ import annotations

from nemo_retriever.retriever import Retriever

from gsf.utils.embedding import get_embed_kwargs
from gsf.vdb import get_vdb

_retriever: Retriever | None = None


def get_retriever(collection_name: str | None = None) -> Retriever:
    """Return a Retriever backed by the given pgvector collection."""

    vdb = get_vdb(collection_name=collection_name)
    _retriever = Retriever(
        vdb_kwargs={"vdb": vdb},
        embed_kwargs=get_embed_kwargs(),
    )
    return _retriever
