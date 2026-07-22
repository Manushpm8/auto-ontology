# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for semantic compile and model-file CLI routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from gsf.semantic import __main__ as semantic_cli
from gsf.semantic.model_yaml import ImportSummary


def test_top_level_help_lists_interchange_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        semantic_cli.main(["--help"])

    output = capsys.readouterr().out
    assert "import" in output
    assert "export" in output


def test_legacy_arguments_still_run_semantic_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "gsf.semantic.compile.run_semantic_compilation",
        calls.append,
    )

    semantic_cli.main(["--database-name", "analytics"])

    assert calls == ["analytics"]


def test_import_command_reads_file_and_reports_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "model.yaml"
    input_path.write_text("version: test", encoding="utf-8")
    calls: list[dict] = []

    def fake_import(document: str, **kwargs: object) -> ImportSummary:
        calls.append({"document": document, **kwargs})
        return ImportSummary(
            model="sales",
            terms=2,
            column_attributes=5,
            sql_attributes=2,
            semantic_foreign_keys=1,
            embeddings=7,
        )

    monkeypatch.setattr(
        "gsf.semantic.model_yaml.import_model_yaml",
        fake_import,
    )

    semantic_cli.main(
        [
            "import",
            "--database-name",
            "analytics",
            "--input",
            str(input_path),
        ]
    )

    assert calls == [
        {
            "document": "version: test",
            "database_name": "analytics",
            "replace": True,
            "embed": True,
        }
    ]
    assert "Imported GSF model 'sales'" in capsys.readouterr().out


def test_export_command_writes_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "gsf.semantic.model_yaml.export_model_yaml",
        lambda **_kwargs: "version: '1.0'\n",
    )

    semantic_cli.main(["export", "--database-name", "analytics"])

    assert capsys.readouterr().out == "version: '1.0'\n"


def test_import_command_reports_invalid_utf8(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "invalid.yaml"
    input_path.write_bytes(b"\xff")

    with pytest.raises(SystemExit, match="1"):
        semantic_cli.main(
            [
                "import",
                "--database-name",
                "analytics",
                "--input",
                str(input_path),
            ]
        )

    assert "Error:" in capsys.readouterr().err
