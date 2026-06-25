# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Singleton NeMo Retrievers wired to the app pgvector store.

The text-to-SQL flow needs two retrievers over two different VDB collections:
a data-layer retriever (tabular schema/data embeddings) and a taxonomies
retriever (semantic-layer / ontology embeddings). They are separate
collections, so each needs its own ``Retriever`` instance — mirroring the
eval harness wiring in ``dev_tools/evaluation/eval_chatbot.py``.
"""

from __future__ import annotations

from nemo_retriever.graph.retriever import Retriever

from gsf.utils.embedding import get_embed_kwargs
from gsf.vdb import get_semantic_vdb, get_vdb

_retriever: Retriever | None = None
_taxonomies_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    """Data-layer retriever over the tabular pgvector collection."""
    global _retriever
    if _retriever is None:
        vdb = get_vdb()
        _retriever = Retriever(
            vdb_kwargs={"vdb": vdb},
            embed_kwargs=get_embed_kwargs(),
        )
    return _retriever


def get_taxonomies_retriever() -> Retriever:
    """Semantic-layer retriever over the ontology/taxonomies collection."""
    global _taxonomies_retriever
    if _taxonomies_retriever is None:
        _taxonomies_retriever = Retriever(
            vdb_kwargs={"vdb": get_semantic_vdb()},
            embed_kwargs=get_embed_kwargs(),
        )
    return _taxonomies_retriever
