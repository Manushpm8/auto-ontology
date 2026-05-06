"""Stub of the AgentState TypedDict used by the LangGraph pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    llm: Any
    initial_question: str
    dialect: str | None
    connector: Any
    messages: List[Any]
    decision: str
    path_state: Dict[str, Any]
