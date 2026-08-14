# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The assembled server publishes the curated surface and nothing else."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from gsf_mcp.config import DEFAULT_SPEC_PATH, ConfigError, Settings
from gsf_mcp.server import INSTRUCTIONS, build_client, build_server, load_spec
from gsf_mcp.tools import CURATED
from gsf_mcp import get_version


def _settings(spec_path: Path = DEFAULT_SPEC_PATH) -> Settings:
    return Settings(
        api_url="http://gsf.test",
        api_token="gsf_token",
        spec_path=spec_path,
        transport="stdio",
        host="127.0.0.1",
        port=3003,
        timeout_s=30.0,
        chat_timeout_s=900.0,
    )


def _tools() -> list[Any]:
    mcp, client = build_server(_settings())

    async def run() -> list[Any]:
        try:
            async with Client(mcp) as session:
                return await session.list_tools()
        finally:
            await client.aclose()

    return asyncio.run(run())


def test_publishes_exactly_the_curated_tools_plus_chat() -> None:
    names = {tool.name for tool in _tools()}

    assert names == {spec.name for spec in CURATED} | {"ask_data"}


def test_every_tool_carries_a_description() -> None:
    # An undescribed tool is close to unusable: selection is driven almost
    # entirely by this text.
    undescribed = [
        tool.name for tool in _tools() if not (tool.description or "").strip()
    ]

    assert undescribed == []


def test_generated_descriptions_are_replaced_end_to_end() -> None:
    # test_tools.py checks the override function in isolation; this checks it
    # was actually handed to FastMCP and ran over the real spec.
    leaked = [tool.name for tool in _tools() if "Api." in (tool.description or "")]

    assert leaked == []


def test_ask_data_reports_a_structured_answer() -> None:
    ask = next(tool for tool in _tools() if tool.name == "ask_data")

    assert ask.outputSchema is not None
    assert set(ask.outputSchema["properties"]) >= {"answer", "sql", "rows"}


def test_handshake_advertises_the_gsf_version() -> None:
    # Not FastMCP's, which is what gets reported if the version is left unset
    # and reads as a GSF version in a client's server list.
    mcp, client = build_server(_settings())

    async def run() -> Any:
        try:
            async with Client(mcp) as session:
                return session.initialize_result.serverInfo
        finally:
            await client.aclose()

    info = asyncio.run(run())

    assert info.name == "gsf"
    assert info.version == get_version()


def test_instructions_point_at_the_primary_tool() -> None:
    # Sequencing guidance lives here rather than in any one tool description,
    # so a host that shows only this still knows where to start.
    assert "ask_data" in INSTRUCTIONS
    assert "check_answerable" in INSTRUCTIONS


def test_client_authenticates_with_the_api_token() -> None:
    client = build_client(_settings())
    try:
        assert client.headers["x-api-key"] == "gsf_token"
    finally:
        asyncio.run(client.aclose())


def test_rejects_a_spec_that_is_not_json(tmp_path: Path) -> None:
    bad = tmp_path / "spec.json"
    bad.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_spec(_settings(bad))


def test_rejects_a_spec_with_no_paths(tmp_path: Path) -> None:
    empty = tmp_path / "spec.json"
    empty.write_text(json.dumps({"openapi": "3.1.0"}), encoding="utf-8")

    with pytest.raises(ConfigError, match="no paths"):
        load_spec(_settings(empty))


def test_refuses_to_start_when_a_curated_endpoint_disappeared(tmp_path: Path) -> None:
    # Starting anyway would produce a healthy-looking server missing a tool.
    spec = json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8"))
    del spec["paths"]["/api/terms"]
    trimmed = tmp_path / "spec.json"
    trimmed.write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(ConfigError, match="GET /api/terms"):
        load_spec(_settings(trimmed))
