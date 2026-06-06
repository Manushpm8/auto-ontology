"""Structured LLM invocation for semantic compilation."""

from __future__ import annotations

import logging
import os
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_BASE_URL = os.environ.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
_MODEL_NAME = os.environ.get("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
_API_KEY = os.environ.get("NVIDIA_API_KEY", "")


def invoke_structured(
    messages: list[SystemMessage | HumanMessage],
    schema: type[T],
    *,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> T:
    """Call the configured chat model with structured output."""
    if not _API_KEY:
        raise EnvironmentError("NVIDIA_API_KEY is not set")

    llm = ChatNVIDIA(
        model=_MODEL_NAME,
        api_key=_API_KEY,
        base_url=_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    structured = llm.with_structured_output(schema)
    result = structured.invoke(messages)
    if isinstance(result, schema):
        return result
    return schema.model_validate(result)
