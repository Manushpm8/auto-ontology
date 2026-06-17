# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM utilities for ontology construction using ChatNVIDIA / ChatOpenAI."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Type, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_BASE_URL = os.environ.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
_MODEL_NAME = os.environ.get("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 5  # seconds; actual delay = base * 2^attempt
_DEFAULT_TIMEOUT = 120  # seconds per LLM call

_IS_OPENAI_MODEL = _MODEL_NAME.startswith("openai/")
_IS_REASONING_MODEL = any(
    _MODEL_NAME.startswith(prefix)
    for prefix in ("openai/o", "openai/gpt-5", "nvidia/llama-3.1-nemotron-ultra")
)
_DEFAULT_MAX_TOKENS = 16384 if _IS_REASONING_MODEL else 1024


def get_llm(
    *,
    temperature: float = 0.0,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT,
) -> BaseChatModel:
    """Instantiate a chat model client from environment config."""
    if _IS_REASONING_MODEL:
        max_tokens = max(max_tokens, _DEFAULT_MAX_TOKENS)

    if _IS_OPENAI_MODEL:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=_MODEL_NAME,
            api_key=_API_KEY,
            base_url=_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    return ChatNVIDIA(
        model=_MODEL_NAME,
        api_key=_API_KEY,
        base_url=_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def invoke_structured(
    messages: list[BaseMessage],
    schema: Type[T],
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    timeout: float = _DEFAULT_TIMEOUT,
) -> T:
    """Invoke ChatNVIDIA with structured output, timeout, and retry with backoff.

    Each attempt has a hard *timeout* (seconds).  On transient failures
    (timeout, HTTP error, None response) the call is retried up to
    ``_MAX_RETRIES`` times with exponential backoff.
    """
    llm = get_llm(
        temperature=temperature, max_tokens=max_tokens, timeout=timeout
    )
    current_messages = messages.copy()
    schema_name = getattr(schema, "__name__", str(schema))

    for attempt in range(_MAX_RETRIES):
        t0 = time.monotonic()
        try:
            structured_llm = llm.with_structured_output(schema)
            raw_result = structured_llm.invoke(current_messages)
            elapsed = time.monotonic() - t0

            if raw_result is None:
                logger.warning(
                    "LLM returned None for %s (attempt %d/%d, %.1fs)",
                    schema_name,
                    attempt + 1,
                    _MAX_RETRIES,
                    elapsed,
                )
                if attempt < _MAX_RETRIES - 1:
                    _backoff(attempt, schema_name)
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

            if isinstance(raw_result, schema):
                result = raw_result
            else:
                result = schema.model_validate(raw_result)

            logger.debug(
                "LLM call for %s succeeded in %.1fs", schema_name, elapsed
            )
            return result

        except ValidationError as e:
            elapsed = time.monotonic() - t0
            if attempt < _MAX_RETRIES - 1:
                logger.warning(
                    "Validation error for %s (attempt %d/%d, %.1fs): %s",
                    schema_name,
                    attempt + 1,
                    _MAX_RETRIES,
                    elapsed,
                    e,
                )
                _backoff(attempt, schema_name)
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
                    "Validation failed after %d attempts for %s (%.1fs)",
                    _MAX_RETRIES,
                    schema_name,
                    elapsed,
                )
                raise

        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "LLM call attempt %d/%d failed for %s after %.1fs: %s: %s",
                attempt + 1,
                _MAX_RETRIES,
                schema_name,
                elapsed,
                type(e).__name__,
                e,
            )
            if attempt < _MAX_RETRIES - 1:
                _backoff(attempt, schema_name)
                continue
            raise

    raise RuntimeError(f"invoke_structured failed for {schema_name}")


def _backoff(attempt: int, context: str) -> None:
    """Sleep with exponential backoff between retries."""
    delay = _RETRY_BACKOFF_BASE * (2**attempt)
    logger.info(
        "  Retrying %s in %ds (attempt %d/%d)…",
        context,
        delay,
        attempt + 2,
        _MAX_RETRIES,
    )
    time.sleep(delay)


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
