# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM utilities for ontology construction using ChatNVIDIA (LangChain)."""

from __future__ import annotations

import json
import logging
import os
from typing import Type, TypeVar

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
_MAX_RETRIES = 3


def get_llm(
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> ChatNVIDIA:
    """Instantiate a ChatNVIDIA client from environment config."""
    base_url = os.environ.get("BASE_URL", _DEFAULT_BASE_URL)
    model = os.environ.get("MODEL_NAME", _DEFAULT_MODEL)
    api_key = os.environ.get("NVIDIA_API_KEY", "")

    return ChatNVIDIA(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def invoke_structured(
    messages: list[BaseMessage],
    schema: Type[T],
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> T:
    """Invoke ChatNVIDIA with structured output and retry on validation errors.

    Follows the same pattern as nemo_retriever's ``llm_invoke.py``.
    """
    llm = get_llm(temperature=temperature, max_tokens=max_tokens)
    current_messages = messages.copy()
    schema_name = getattr(schema, "__name__", str(schema))

    for attempt in range(_MAX_RETRIES):
        try:
            model_llm = llm.with_structured_output(schema, method="function_calling")
            result = model_llm.invoke(current_messages)
            if result is None:
                logger.warning(
                    "LLM returned None for %s (attempt %d/%d)",
                    schema_name,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                if attempt < _MAX_RETRIES - 1:
                    current_messages.append(
                        SystemMessage(
                            content=(
                                "Your previous response could not be parsed. "
                                "Please return a **fully valid** JSON object "
                                "matching the required schema."
                            )
                        )
                    )
                    continue
                return schema()
            return result
        except ValidationError as e:
            if attempt < _MAX_RETRIES - 1:
                current_messages.append(
                    SystemMessage(
                        content=(
                            "Your previous output did not validate. "
                            f"Validation errors:\n{str(e)}\n"
                            "Please return a **fully valid** object that "
                            "satisfies the schema. Do not omit required fields."
                        )
                    )
                )
            else:
                logger.error(
                    "Validation failed after %d attempts for %s",
                    _MAX_RETRIES,
                    schema_name,
                )
                raise
        except Exception as e:
            logger.error(
                "LLM call attempt %d/%d failed for %s: %s: %s",
                attempt + 1,
                _MAX_RETRIES,
                schema_name,
                type(e).__name__,
                e,
            )
            if attempt < _MAX_RETRIES - 1:
                continue
            raise

    raise RuntimeError(f"invoke_structured failed for {schema_name}")


def invoke_text(
    messages: list[BaseMessage],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    """Invoke ChatNVIDIA and return the raw text response."""
    llm = get_llm(temperature=temperature, max_tokens=max_tokens)
    response = llm.invoke(messages)
    content = response.content
    if not content:
        logger.warning(
            "LLM returned empty content. response_metadata=%s",
            getattr(response, "response_metadata", {}),
        )
    return content


def invoke_json(
    messages: list[BaseMessage],
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> dict:
    """Invoke ChatNVIDIA and parse the response as JSON.

    Strips markdown fences if present before parsing.
    """
    raw = invoke_text(messages, temperature=temperature, max_tokens=max_tokens)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else text
    return json.loads(text)
