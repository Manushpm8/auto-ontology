# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM client construction and structured-output invocation wrappers."""

import logging
import os
import random
import threading
import time
from typing import Type, TypeVar

import requests as _requests
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

RETRY_MAX_ATTEMPTS = int(os.environ.get("LLM_RETRY_MAX_ATTEMPTS", "5"))
# Transient saturation on the shared endpoint arrives as 429/503 and clears on a
# timescale of tens of seconds. Three attempts at 2**(n+1) spent ~7s total, which
# abandoned candidates inside a single such event; capped exponential backoff over
# five attempts spends ~60s instead. Honors Retry-After when the server sends one.
RETRY_BACKOFF_CAP_S = float(os.environ.get("LLM_RETRY_BACKOFF_CAP_S", "30"))
# gpt-5.x structured SQL generation routinely exceeds 50s under parallel load
# (BIRD_NCAND>1 × multi-worker eval). Override with LLM_INVOKE_TIMEOUT_S.
LLM_INVOKE_TIMEOUT_S = float(os.environ.get("LLM_INVOKE_TIMEOUT_S", "120"))

# Bound total concurrent LLM requests across all worker threads so the pipeline's
# nested parallelism (tables × terms) doesn't saturate the hosted endpoint's
# per-worker request cap (which surfaces as HTTP 503 ResourceExhausted).
LLM_MAX_INFLIGHT = int(os.environ.get("LLM_MAX_INFLIGHT", "6"))
_INFLIGHT = threading.BoundedSemaphore(LLM_MAX_INFLIGHT)

# Substrings that indicate a transient, retryable server condition.
_RETRYABLE_TOKENS = (
    "429",
    "Too Many Requests",
    "503",
    "ResourceExhausted",
    "Service Unavailable",
    "ReadTimeout",
    "timed out",
    "Timeout",
)


def _is_read_timeout(exc: BaseException) -> bool:
    """True for requests/httpx/openai client read timeouts.

    ChatOpenAI uses httpx, so timeouts arrive as ``httpx.ReadTimeout`` (not
    ``requests.exceptions.ReadTimeout``). Treating only the requests type as
    retryable caused immediate failure + full traceback under parallel eval.
    """
    if isinstance(exc, _requests.exceptions.Timeout):
        return True
    name = type(exc).__name__
    if name in {
        "ReadTimeout",
        "WriteTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "TimeoutException",
        "APITimeoutError",
    }:
        return True
    msg = str(exc).lower()
    return "read operation timed out" in msg or "request timed out" in msg


def _retry_after(exc: BaseException) -> float | None:
    """Seconds the server asked us to wait, if it said so."""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _backoff_s(attempt: int, exc: BaseException) -> float:
    """Capped exponential backoff, deferring to the server's Retry-After."""
    asked = _retry_after(exc)
    if asked is not None:
        return min(asked, RETRY_BACKOFF_CAP_S) + random.uniform(0, 1)
    return min(2 ** (attempt + 1), RETRY_BACKOFF_CAP_S) + random.uniform(0, 1)


class _TimeoutSession(_requests.Session):
    """requests.Session that enforces a default timeout on every request."""

    def __init__(self, timeout: float = LLM_INVOKE_TIMEOUT_S, **kwargs):
        super().__init__(**kwargs)
        self._default_timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(method, url, **kwargs)


T = TypeVar("T", bound=BaseModel)

_BASE_URL = os.environ.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
_MODEL_NAME = os.environ.get("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
_API_KEY = os.environ.get("NVIDIA_API_KEY", "")


def get_llm_client(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Create an LLM client.

    Parameters
    ----------
    model : str | None
        Override the default ``MODEL_NAME`` env var for this client.
    """
    if not _API_KEY:
        raise EnvironmentError("NVIDIA_API_KEY is not set")

    resolved_model = model or _MODEL_NAME

    if resolved_model.startswith(("openai/", "azure/", "aws/")):
        from langchain_openai import ChatOpenAI

        # gpt-5.x / o-series reject an explicit temperature and only allow the
        # provider default. gpt-4o / gpt-4o-mini (and most other chat models)
        # honor temperature, which is what makes BIRD_NCAND_TEMP actually
        # diversify candidates 2..N.
        model_leaf = resolved_model.rsplit("/", 1)[-1].lower()
        omit_temperature = model_leaf.startswith(("gpt-5", "o1", "o3", "o4"))
        kwargs: dict = {
            "model": resolved_model,
            "api_key": _API_KEY,
            "base_url": _BASE_URL,
            "max_tokens": max_tokens,
            "timeout": LLM_INVOKE_TIMEOUT_S,
            "max_retries": 0,
        }
        if not omit_temperature:
            kwargs["temperature"] = temperature
        return ChatOpenAI(**kwargs)

    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    client = ChatNVIDIA(
        model=resolved_model,
        api_key=_API_KEY,
        base_url=_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    client._client.get_session_fn = lambda: _TimeoutSession(LLM_INVOKE_TIMEOUT_S)
    return client


def _ensure_non_system_message(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Guarantee at least one non-system message.

    Anthropic/Bedrock (via the OpenAI-compatible gateway) reject requests that
    contain only system messages with "bedrock requires at least one non-system
    message". Many agents build a single ``SystemMessage`` prompt, so when no
    user/assistant message is present we promote the last system message to a
    ``HumanMessage`` (its content is the actual instruction). No-op when a
    non-system message already exists.
    """
    if not messages or any(not isinstance(m, SystemMessage) for m in messages):
        return messages
    converted = list(messages)
    converted[-1] = HumanMessage(content=converted[-1].content)
    return converted


def _structured_output_kwargs(llm: BaseChatModel) -> dict:
    """Pick the ``with_structured_output`` method for *llm*.

    Anthropic/Claude models served through the OpenAI-compatible gateway (e.g.
    ``aws/anthropic/bedrock-claude-opus-4-8``) reject the default json_schema /
    ``response_format`` path ("output_config.format: Extra inputs are not
    permitted"), but they support tool calling — so force ``function_calling``
    for them. Everything else keeps langchain's default (json_schema for
    OpenAI), which is preferred where supported.
    """
    model = str(getattr(llm, "model_name", "") or getattr(llm, "model", "") or "")
    lowered = model.lower()
    if "anthropic" in lowered or "claude" in lowered:
        return {"method": "function_calling"}
    return {}


def invoke_text(llm: BaseChatModel, prompt: str) -> str:
    """Invoke the LLM with a single system-message *prompt* and return its text.

    Free-text counterpart to :func:`invoke_with_structured_output`, for callers
    that parse the raw response themselves (e.g. the text-to-PQL pipeline).
    """
    response = llm.invoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def safe_invoke_with_structured_output(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    schema: Type[T],
) -> T:
    """LLM structured call with retry."""
    current_messages = _ensure_non_system_message(messages.copy())
    schema_name = getattr(schema, "__name__", str(schema))
    structured_kwargs = _structured_output_kwargs(llm)

    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            model_llm = llm.with_structured_output(schema, **structured_kwargs)
            with _INFLIGHT:
                result = model_llm.invoke(current_messages)
        except ValidationError as e:
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                current_messages.append(
                    SystemMessage(
                        content=(
                            "Your previous output did not validate. "
                            f"Validation errors:\n{str(e)}\n"
                            "Please return a **fully valid** object that satisfies the schema. "
                            "Do not omit required fields. Do not include extra keys."
                        )
                    )
                )
                continue
            logger.error(
                f"Validation failed after {RETRY_MAX_ATTEMPTS} attempts for {schema_name}"
            )
            raise
        except Exception as e:
            if _is_read_timeout(e):
                logger.error(
                    "LLM invoke timed out after %ss on attempt %d/%d for %s (%s)",
                    LLM_INVOKE_TIMEOUT_S,
                    attempt + 1,
                    RETRY_MAX_ATTEMPTS,
                    schema_name,
                    type(e).__name__,
                )
                if attempt < RETRY_MAX_ATTEMPTS - 1:
                    time.sleep(_backoff_s(attempt, e))
                    continue
                raise

            is_retryable = any(tok in str(e) for tok in _RETRYABLE_TOKENS)
            if is_retryable and attempt < RETRY_MAX_ATTEMPTS - 1:
                wait = _backoff_s(attempt, e)
                logger.warning(
                    "Retryable LLM error (endpoint saturated/rate-limited) on attempt "
                    "%d/%d for %s — retrying in %.1fs",
                    attempt + 1,
                    RETRY_MAX_ATTEMPTS,
                    schema_name,
                    wait,
                )
                time.sleep(wait)
                continue
            logger.error(
                f"Unexpected error on attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS} for {schema_name}: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise

        if result is None:
            logger.warning(
                "LLM returned None for %s on attempt %d/%d — retrying.",
                schema_name,
                attempt + 1,
                RETRY_MAX_ATTEMPTS,
            )
            continue
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)


def invoke_with_structured_output(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    schema: Type[T],
) -> T | None:
    """Safe wrapper that returns None on failure."""
    try:
        schema_name = getattr(schema, "__name__", str(schema))
        return safe_invoke_with_structured_output(llm, messages, schema)
    except Exception as e:
        logger.error(
            f"invoke_with_structured_output failed for {schema_name} after {RETRY_MAX_ATTEMPTS} attempts: "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return None
