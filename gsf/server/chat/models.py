"""Pydantic models and constants for the chat streaming endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Payload sent by the frontend to start a chat completion."""

    question: str = Field(..., min_length=1)
    dialect: str | None = None
    connector_id: str | None = None
    acronyms: str | None = None
    custom_prompts: str | None = None


NODE_LABELS: dict[str, str] = {
    "entities_extraction": "Extracting entities…",
    "retrieve_candidates": "Searching relevant data…",
    "prepare_candidates": "Searching relevant data…",
    "construct_sql_from_candidates": "Constructing SQL query…",
    "construct_sql_not_from_snippets": "Constructing SQL query…",
    "validate_sql_query": "Validating SQL…",
    "validate_intent": "Validating SQL…",
    "reconstruct_sql": "Reconstructing SQL…",
    "execute_sql_query": "Executing query…",
    "format_and_respond": "Formatting response…",
    "unconstructable_sql_response": "Query could not be constructed",
}
