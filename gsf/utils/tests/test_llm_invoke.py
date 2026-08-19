# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for LLM invocation helpers."""

from typing import Any
from unittest.mock import patch

from gsf.utils.llm_invoke import (
    LLM_INVOKE_TIMEOUT_S,
    _build_client,
    _structured_output_kwargs,
)


class _Model:
    """Stand-in exposing the model name the way langchain clients do."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


def test_claude_uses_function_calling_without_a_tool_choice() -> None:
    """Bedrock behind LiteLLM rejects any explicit tool choice.

    The gateway maps tool_choice into ``toolConfig.toolChoice`` *and* forwards the
    original ``tool_choice.type``, and Bedrock refuses the pair:

        The additional field tool_choice/type conflicts with the existing field
        toolConfig.toolChoice.tool.

    Measured against the live endpoint: "auto", "any", "required" and langchain's
    default (naming the tool) all hit it; only omitting the choice succeeds.
    """
    kwargs = _structured_output_kwargs(_Model("aws/anthropic/bedrock-claude-opus-4-8"))

    assert kwargs == {"method": "function_calling", "tool_choice": None}


def test_claude_is_matched_on_either_vendor_or_family_name() -> None:
    for name in (
        "aws/anthropic/bedrock-claude-opus-4-8",
        "claude-3-5-sonnet",
        "ANTHROPIC/Claude",
    ):
        assert _structured_output_kwargs(_Model(name))["method"] == "function_calling"


def test_other_models_keep_the_langchain_default() -> None:
    """json_schema is preferred where supported, so nothing is forced for them."""
    for name in ("nvidia/nemotron-3-nano-30b-a3b", "gpt-4o", ""):
        assert _structured_output_kwargs(_Model(name)) == {}


def test_a_client_exposing_model_instead_of_model_name_still_matches() -> None:
    """Some langchain clients expose `model` rather than `model_name`."""

    class _AltModel:
        model: Any = "aws/anthropic/bedrock-claude-opus-4-8"

    assert _structured_output_kwargs(_AltModel())["tool_choice"] is None


@patch("langchain_openai.ChatOpenAI")
def test_openai_client_uses_requested_configuration(chat_openai) -> None:
    _build_client(
        model="openai/gpt-4o",
        api_key="non-reasoning-key",
        base_url="https://non-reasoning.example/v1",
        temperature=0.7,
        max_tokens=1024,
    )

    chat_openai.assert_called_once_with(
        model="openai/gpt-4o",
        api_key="non-reasoning-key",
        base_url="https://non-reasoning.example/v1",
        temperature=0.7,
        max_tokens=1024,
        timeout=LLM_INVOKE_TIMEOUT_S,
        max_retries=0,
    )


@patch("langchain_openai.ChatOpenAI")
def test_gpt5_omits_unsupported_temperature(chat_openai) -> None:
    _build_client(
        model="openai/gpt-5.2",
        api_key="key",
        base_url="https://example.test/v1",
        temperature=0.7,
        max_tokens=1024,
    )

    assert "temperature" not in chat_openai.call_args.kwargs


@patch("langchain_openai.ChatOpenAI")
def test_bedrock_disables_parallel_tool_calls(chat_openai) -> None:
    _build_client(
        model="aws/anthropic/bedrock-claude-opus-4-8",
        api_key="key",
        base_url="https://example.test/v1",
        temperature=0.0,
        max_tokens=1024,
    )

    assert chat_openai.call_args.kwargs["disabled_params"] == {
        "parallel_tool_calls": None
    }
