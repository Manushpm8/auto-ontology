# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``BIRD_*`` flags may only be read in :mod:`gsf.flags`.

The point of centralizing them was to end up with one definition per flag. That
holds only if new code cannot quietly add a second reader with its own default
and its own idea of what ``off`` means, which is exactly how three flags ended up
with parsers that disagreed. These tests fail on the reintroduction rather than
waiting for the divergence to show up in a run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from gsf import flags

GSF_ROOT = Path(flags.__file__).resolve().parent
FLAGS_MODULE = Path(flags.__file__).resolve()

# Only the declarations in flags.py may name a BIRD_ variable to the environment.
_ENV_READERS = ("environ", "getenv")


def _source_files() -> list[Path]:
    return [
        path
        for path in sorted(GSF_ROOT.rglob("*.py"))
        if path != FLAGS_MODULE and "tests" not in path.parts
    ]


def _bird_env_reads(tree: ast.AST) -> list[str]:
    """Names of BIRD_ variables this module reads straight from the environment."""
    found: list[str] = []

    def _record(node: ast.AST) -> None:
        for const in ast.walk(node):
            if isinstance(const, ast.Constant) and isinstance(const.value, str):
                if const.value.startswith("BIRD_"):
                    found.append(const.value)

    for node in ast.walk(tree):
        # os.environ.get("BIRD_X") / os.getenv("BIRD_X")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("get", *_ENV_READERS):
                if any(part in ast.dump(func) for part in _ENV_READERS):
                    for arg in node.args:
                        _record(arg)
        # os.environ["BIRD_X"]
        elif isinstance(node, ast.Subscript):
            if any(part in ast.dump(node.value) for part in _ENV_READERS):
                _record(node.slice)

    return found


@pytest.mark.parametrize(
    "path", _source_files(), ids=lambda p: str(p.relative_to(GSF_ROOT))
)
def test_no_bird_env_reads_outside_the_flags_module(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = _bird_env_reads(tree)
    assert not offenders, (
        f"{path.relative_to(GSF_ROOT)} reads {sorted(set(offenders))} from the "
        "environment directly. Declare it in gsf/flags.py and call the flag."
    )


def test_source_files_were_actually_scanned():
    """Guard the guard: a broken glob would make the test above vacuous."""
    scanned = {p.name for p in _source_files()}
    assert "verify_revise.py" in scanned
    assert "sql_selection.py" in scanned
    assert "rerank.py" in scanned


# --- documentation references ---------------------------------------------

_BIRD_TOKEN = re.compile(r"BIRD_[A-Z0-9_]+")


def _documented_names() -> dict[str, set[Path]]:
    """Every BIRD_ name mentioned anywhere in the package, and where."""
    mentions: dict[str, set[Path]] = {}
    for path in _source_files():
        for token in _BIRD_TOKEN.findall(path.read_text()):
            mentions.setdefault(token, set()).add(path)
    return mentions


def test_no_references_to_undeclared_flags():
    """Catches docs left behind pointing at a flag that no longer exists."""
    declared = set(flags.names())
    stale: dict[str, set[Path]] = {}
    for token, paths in _documented_names().items():
        # A trailing wildcard stub like BIRD_CAND_KEEP_K_* is written as a prefix.
        if token in declared or any(name.startswith(token) for name in declared):
            continue
        stale[token] = paths
    assert not stale, f"references to flags that are not declared: {stale}"
