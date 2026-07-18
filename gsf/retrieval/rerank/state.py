# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
LangGraph agent state and API payload types for the rerank flow.

Kept separate from the graph module to avoid circular imports (agents import
state; the graph imports agents).
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage


class RerankPayload(TypedDict):
    """Payload for the rerank agent flow."""

    question: str
    path_state: NotRequired[dict]


class RerankState(TypedDict):
    """State object passed through the rerank LangGraph."""

    llm: BaseChatModel
    initial_question: str
    messages: list[HumanMessage]
    path_state: dict
    decision: str


def get_original_question(state: RerankState) -> str:
    """Raw user question as submitted."""
    return state.get("initial_question", "")


__all__ = [
    "RerankPayload",
    "RerankState",
    "get_original_question",
]
