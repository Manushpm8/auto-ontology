# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Blocking text-to-SQL tool endpoint.

This is the integration surface for external agent harnesses such as the
NVIDIA AI-Q Blueprint. AI-Q registers a NeMo Agent Toolkit tool that POSTs a
natural-language question here and gets back a single JSON payload with the
generated SQL and executed rows — no SSE, no streaming, no warm-pool slot.

The heavy lifting (`get_agent_response`) is synchronous and spends most of its
time in C-blocked LLM/DB calls, so this path is declared as a plain ``def``
handler: FastAPI runs sync path operations in a worker thread, keeping the
event loop free. Note this deliberately bypasses the single-slot concurrency
lock used by the SSE chat route — see ``chat/router.py`` — which is acceptable
for a tool call but means parallel tool calls share the agent's rate budget.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from nemo_retriever.tabular_data.retrieval.text_to_sql.main import (
    get_agent_response,
)

from gsf.connectors import get_connectors
from gsf.server.chat.settings_dal import fetch_acronyms, fetch_custom_prompts
from gsf.utils import get_retriever

logger = logging.getLogger(__name__)

router = APIRouter()

# The text-to-SQL agent stores its results under these keys on the result dict
# (mirrors dev_tools/evaluation/eval_chatbot.py).
_RESPONSE_KEY = "response"
_SQL_KEY = "sql_code"
_DB_RESULT_KEY = "sql_response_from_db"


class StructuredQueryRequest(BaseModel):
    """A single natural-language question over structured data."""

    question: str = Field(..., min_length=1)


class StructuredQueryResponse(BaseModel):
    """Single-shot answer for a tool/function call."""

    answer: str = Field(
        default="",
        description="User-facing natural-language answer from the agent.",
    )
    sql: str = Field(
        default="",
        description="The SQL the agent generated for the question.",
    )
    rows: Any = Field(
        default=None,
        description="Executed query result rows (JSON-safe; shape varies).",
    )


def _json_safe(value: Any) -> Any:
    """Coerce arbitrary agent output into something JSON-serialisable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


@router.post("/tools/structured-query", response_model=StructuredQueryResponse)
def structured_query(request: StructuredQueryRequest) -> StructuredQueryResponse:
    """Answer a natural-language question against the configured databases.

    Builds the same agent payload as the SSE chat route and the eval harness,
    then runs the blocking text-to-SQL pipeline once and returns the result.
    """
    payload = {
        "question": request.question,
        "retriever": get_retriever(),
        "connectors": get_connectors(),
        "path_state": {},
        "acronyms": fetch_acronyms(),
        "custom_prompts": fetch_custom_prompts(),
    }

    try:
        result = get_agent_response(payload) or {}
    except Exception as exc:  # noqa: BLE001 — surface a clean 500 to the caller
        logger.exception("structured-query agent run failed")
        raise HTTPException(
            status_code=500,
            detail=f"Agent run failed: {exc}",
        ) from exc

    return StructuredQueryResponse(
        answer=str(result.get(_RESPONSE_KEY, "") or ""),
        sql=str(result.get(_SQL_KEY, "") or ""),
        rows=_json_safe(result.get(_DB_RESULT_KEY)),
    )
