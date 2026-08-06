# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat-route helpers: request models and node labels."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Re-exported so the router (and any other server-side consumer) can keep
# importing NODE_LABELS from here. The dict itself now lives next to the
# graph definition so the agent pipeline can also use it without depending
# on the server layer.
from gsf.retrieval.text_to_sql.node_labels import NODE_LABELS

__all__ = ["NODE_LABELS", "ChatRequest", "ChatRequestWithEvidence"]


class ChatRequest(BaseModel):
    """Payload sent by the frontend to start a chat completion."""

    question: str = Field(..., min_length=1)


class ChatRequestWithEvidence(BaseModel):
    """Payload for chat requests that include an evidence hint."""

    question: str = Field(..., min_length=1)
    database: str = Field(..., min_length=1)
    evidence: str = Field(default="")
