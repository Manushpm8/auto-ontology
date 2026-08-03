# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from pytest import MonkeyPatch

from gsf.utils.model_config import resolve

_TRIPLET_ENV = [
    "DEFAULT_MODELS_API_KEY",
    "DEFAULT_MODELS_ENDPOINT",
    "DEFAULT_MODELS_MODEL",
    "MODEL_NAME",
    "BASE_URL",
    "NVIDIA_API_KEY",
    "EMBED_MODEL",
    "RERANK_MODEL",
    "REASONING_MODEL",
    "NON_REASONING_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: MonkeyPatch) -> None:
    for name in _TRIPLET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEFAULT_MODELS_API_KEY", "sk-test")


@pytest.mark.parametrize("shared_var", ["DEFAULT_MODELS_MODEL", "MODEL_NAME"])
@pytest.mark.parametrize("prefix", ["EMBED", "RERANK"])
def test_embed_and_rerank_models_ignore_shared_chat_model(
    monkeypatch: MonkeyPatch,
    prefix: str,
    shared_var: str,
) -> None:
    """A chat model in the shared default must not leak into embed/rerank."""
    monkeypatch.setenv(shared_var, "openai/openai/gpt-5.5")

    model = resolve(prefix, "MODEL")

    assert model != "openai/openai/gpt-5.5"
    assert (
        model
        == {
            "EMBED": "nvidia/nvidia/llama-nemotron-embed-vl-1b-v2",
            "RERANK": "nvidia/nvidia/llama-3.2-nv-rerankqa-1b-v2",
        }[prefix]
    )


@pytest.mark.parametrize("prefix", ["EMBED", "RERANK"])
def test_explicit_model_still_wins(monkeypatch: MonkeyPatch, prefix: str) -> None:
    monkeypatch.setenv("DEFAULT_MODELS_MODEL", "openai/openai/gpt-5.5")
    monkeypatch.setenv(f"{prefix}_MODEL", "nvidia/custom-model")

    assert resolve(prefix, "MODEL") == "nvidia/custom-model"


@pytest.mark.parametrize("prefix", ["REASONING", "NON_REASONING"])
def test_chat_triplets_still_use_shared_model(
    monkeypatch: MonkeyPatch, prefix: str
) -> None:
    monkeypatch.setenv("DEFAULT_MODELS_MODEL", "openai/openai/gpt-5.5")

    assert resolve(prefix, "MODEL") == "openai/openai/gpt-5.5"


@pytest.mark.parametrize("prefix", ["EMBED", "RERANK"])
def test_endpoint_and_key_still_share_defaults(
    monkeypatch: MonkeyPatch, prefix: str
) -> None:
    """Only MODEL is excluded — endpoint and key still come from the shared set."""
    monkeypatch.setenv("DEFAULT_MODELS_ENDPOINT", "https://example.test/v1")

    assert resolve(prefix, "ENDPOINT") == "https://example.test/v1"
    assert resolve(prefix, "API_KEY") == "sk-test"
