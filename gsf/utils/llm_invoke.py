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

from gsf.utils.model_config import resolve

logger = logging.getLogger(__name__)

RETRY_MAX_ATTEMPTS = 3
LLM_INVOKE_TIMEOUT_S = 50

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
)


class _TimeoutSession(_requests.Session):
    """requests.Session that enforces a default timeout on every request."""

    def __init__(self, timeout: float = LLM_INVOKE_TIMEOUT_S, **kwargs):
        super().__init__(**kwargs)
        self._default_timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(method, url, **kwargs)


T = TypeVar("T", bound=BaseModel)

# Main (reasoning) model triplet. Each field falls back to DEFAULT_MODELS_<field>
# (and the API key additionally to the legacy NVIDIA_API_KEY) when unset.
_BASE_URL = resolve("REASONING", "ENDPOINT")
_MODEL_NAME = resolve("REASONING", "MODEL")
_API_KEY = resolve("REASONING", "API_KEY")

# Non-reasoning model. Kept fully separate (key/endpoint/model) so it can point at
# a different endpoint than the main model (e.g. inference vs integrate API). Each
# field falls back to DEFAULT_MODELS_<field> when unset.
_NON_REASONING_BASE_URL = resolve("NON_REASONING", "ENDPOINT")
_NON_REASONING_MODEL_NAME = resolve("NON_REASONING", "MODEL")
_NON_REASONING_API_KEY = resolve("NON_REASONING", "API_KEY")


def _build_client(
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    """Build a chat client for the given model/endpoint.

    OpenAI-family models (``openai/``, ``azure/``, ``aws/`` prefixes) go through
    ``ChatOpenAI``, which supports structured output. Everything else uses
    ``ChatNVIDIA``.
    """
    if model.startswith(("openai/", "azure/", "aws/")):
        from langchain_openai import ChatOpenAI

        # gpt-5.x / o-series reject an explicit temperature, but support
        # reasoning effort. gpt-4o / gpt-4o-mini (and most other chat models)
        # honor temperature, which is what makes BIRD_NCAND_TEMP diversify
        # candidates 2..N.
        model_leaf = model.rsplit("/", 1)[-1].lower()
        is_reasoning_model = model_leaf.startswith(("gpt-5", "o1", "o3", "o4"))
        kwargs: dict = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "max_tokens": max_tokens,
            "timeout": LLM_INVOKE_TIMEOUT_S,
            "max_retries": 0,
        }
        if is_reasoning_model:
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["temperature"] = temperature
        # langchain sends parallel_tool_calls=False alongside a named tool_choice
        # for method="function_calling". litellm's Bedrock adapter mistranslates
        # that pair and the gateway rejects the request as
        # "tool_choice/type conflicts with toolConfig.toolChoice.tool" — which
        # names the wrong field, so it reads like a tool_choice problem. Dropping
        # this one parameter makes the identical payload succeed; a single tool
        # choice already implies no parallelism.
        if model.startswith("aws/"):
            kwargs["disabled_params"] = {"parallel_tool_calls": None}
        return ChatOpenAI(**kwargs)

    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    client = ChatNVIDIA(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    client._client.get_session_fn = lambda: _TimeoutSession(LLM_INVOKE_TIMEOUT_S)
    return client


def get_llm_client(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Create an LLM client for the main reasoning model.

    Parameters
    ----------
    model : str | None
        Override the default ``REASONING_MODEL`` env var for this client.
    """
    if not _API_KEY:
        raise EnvironmentError("REASONING_API_KEY is not set")

    return _build_client(
        model=model or _MODEL_NAME,
        api_key=_API_KEY,
        base_url=_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_non_reasoning_llm_client(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Create an LLM client for the non-reasoning model.

    Uses ``NON_REASONING_API_KEY`` / ``NON_REASONING_ENDPOINT`` /
    ``NON_REASONING_MODEL`` so it can target a different endpoint than the
    main agent model.

    Parameters
    ----------
    model : str | None
        Override the default ``NON_REASONING_MODEL`` env var for this client.
    """
    if not _NON_REASONING_API_KEY:
        raise EnvironmentError("NON_REASONING_API_KEY is not set")

    return _build_client(
        model=model or _NON_REASONING_MODEL_NAME,
        api_key=_NON_REASONING_API_KEY,
        base_url=_NON_REASONING_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
    )


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

    ``tool_choice=None`` suppresses the tool-choice langchain would otherwise set.
    Bedrock behind LiteLLM rejects the request when one is present, because the gateway
    both maps it into ``toolConfig.toolChoice`` and forwards the original
    ``tool_choice.type``::

        The additional field tool_choice/type conflicts with the existing field
        toolConfig.toolChoice.tool. Remove tool_choice/type and try again.

    Measured against the live endpoint: every explicit value ("auto", "any",
    "required", and langchain's default of naming the tool) hits that conflict, and only
    omitting it succeeds. The model still calls the tool without being forced to, and
    the caller retries if it ever answers without one.
    """
    model = str(getattr(llm, "model_name", "") or getattr(llm, "model", "") or "")
    lowered = model.lower()
    if "anthropic" in lowered or "claude" in lowered:
        return {"method": "function_calling", "tool_choice": None}
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
        except _requests.exceptions.ReadTimeout:
            logger.error(
                "LLM invoke timed out after %ds on attempt %d/%d for %s",
                LLM_INVOKE_TIMEOUT_S,
                attempt + 1,
                RETRY_MAX_ATTEMPTS,
                schema_name,
            )
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                wait = 2 ** (attempt + 1) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            raise
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
            else:
                logger.error(
                    f"Validation failed after {RETRY_MAX_ATTEMPTS} attempts for {schema_name}"
                )
                raise
        except Exception as e:
            is_retryable = any(tok in str(e) for tok in _RETRYABLE_TOKENS)
            if is_retryable and attempt < RETRY_MAX_ATTEMPTS - 1:
                wait = 2 ** (attempt + 1) + random.uniform(0, 1)
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
