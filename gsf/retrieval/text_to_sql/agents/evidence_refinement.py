# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conservatively ground evidence literals against the prepared schema."""

from __future__ import annotations

import re
from typing import Any, Dict, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from gsf.retrieval.text_to_sql.base import BaseAgent, record_thought
from gsf.retrieval.text_to_sql.formatters_util import format_tables_for_prompt
from gsf.retrieval.text_to_sql.state import AgentState, get_question_for_processing
from gsf.utils.llm_invoke import StrictLLMOutputModel, safe_invoke_structured_nr
from gsf.utils.sample_values import stringify_sample_values

_GRAPH_NODE_NAME = "refine_evidence"
_COMPARISON_OPERATORS = frozenset({"=", "!=", "<>", ">", ">=", "<", "<="})
_QUOTED_LITERAL_RE = re.compile(r"""^(?:'[^'\n]*'|"[^"\n]*")$""")
_SCALAR_LITERAL_RE = re.compile(
    r"""^(?:'[^'\n]*'|"[^"\n]*"|-?\d+(?:\.\d+)?|true|false|null)$""",
    re.IGNORECASE,
)
_FORMULA_OPERATOR_RE = re.compile(r"[*+/]")
_OPERATOR_CUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:at least|no less than|starting from|on or after)\b", re.I),
        ">=",
    ),
    (
        re.compile(
            r"\b(?:greater than|larger than|(?<!no )more than|(?<!or )after)\b",
            re.I,
        ),
        ">",
    ),
    (re.compile(r"\b(?:at most|no more than|up to|on or before)\b", re.I), "<="),
    (
        re.compile(r"\b(?:(?<!no )less than|fewer than|(?<!or )before)\b", re.I),
        "<",
    ),
    (re.compile(r"\b(?:exactly|equal to)\b", re.I), "="),
)

_SYSTEM_PROMPT = """\
You refine evidence before SQL generation by correcting only mistakes that are
certain from the user's question or the supplied column sample values.

Return exact, minimal substring replacements on individual evidence lines.
Allowed repairs:
1. string_representation: fix a string value or its casing only when one sample
   value for the named table and column proves the exact stored representation;
2. constant_value: replace a mistaken scalar constant only when the sanitized
   question explicitly supplies the intended constant, or a column sample proves it;
3. predicate_operator: change only =, !=, <>, >, >=, <, or <= when the sanitized
   question makes the evidence operator unquestionably wrong. For example, "at
   least", "starting from", and "on or after" are inclusive; "more than" is strict.

Never change formulas, arithmetic, table names, column names, mappings, prose,
logical connectors, ordering, grouping, or projection instructions. Never add or
remove evidence. If a repair is uncertain, return no patch for it. An empty repair
list is the correct answer whenever the evidence may already be valid."""


class EvidenceRepairPatch(StrictLLMOutputModel):
    """One minimal replacement within a single evidence line."""

    line_number: int = Field(ge=1, description="One-based evidence line number.")
    old_text: str = Field(description="Exact literal or operator to replace.")
    new_text: str = Field(description="Exact replacement literal or operator.")
    kind: Literal["string_representation", "constant_value", "predicate_operator"]
    table_name: str = Field(
        default="",
        description="Table containing the value; required for sample-backed repairs.",
    )
    column_name: str = Field(
        default="",
        description="Column containing the value; required for sample-backed repairs.",
    )
    reason: str = Field(description="Why the correction is certain.")


class EvidenceRefinementResult(StrictLLMOutputModel):
    """Structured evidence refinement response."""

    reasoning: str = Field(default="")
    repairs: list[EvidenceRepairPatch] = Field(default_factory=list)


def _unquote_literal(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _question_contains_literal(question: str, literal: str) -> bool:
    value = _unquote_literal(literal)
    if not value:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", question))


def _unambiguous_question_operator(question: str) -> str | None:
    operators = {
        operator for pattern, operator in _OPERATOR_CUES if pattern.search(question)
    }
    return operators.pop() if len(operators) == 1 else None


def _column_samples(
    tables: list[dict[str, Any]],
    table_name: str,
    column_name: str,
) -> list[str] | None:
    """Return samples only when the table/column reference resolves uniquely."""
    wanted_table = table_name.casefold().strip()
    wanted_column = column_name.casefold().strip()
    matches: list[list[str]] = []

    for table in tables:
        actual_table = str(table.get("name") or "")
        qualified_table = ".".join(
            str(part)
            for part in (
                table.get("database_name"),
                table.get("schema_name"),
                actual_table,
            )
            if part
        )
        if wanted_table not in {
            actual_table.casefold(),
            qualified_table.casefold(),
        }:
            continue
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            if str(column.get("name") or "").casefold() != wanted_column:
                continue
            matches.append(stringify_sample_values(column.get("sample_values")))

    return matches[0] if len(matches) == 1 else None


def _sample_supports(
    patch: EvidenceRepairPatch,
    tables: list[dict[str, Any]],
) -> bool:
    if not patch.table_name or not patch.column_name:
        return False
    samples = _column_samples(tables, patch.table_name, patch.column_name)
    if not samples:
        return False
    return _unquote_literal(patch.new_text) in samples


def _valid_patch(
    patch: EvidenceRepairPatch,
    line: str,
    question: str,
    tables: list[dict[str, Any]],
) -> bool:
    if (
        not patch.old_text
        or not patch.new_text
        or patch.old_text == patch.new_text
        or "\n" in patch.old_text
        or "\n" in patch.new_text
        or line.count(patch.old_text) != 1
        or _FORMULA_OPERATOR_RE.search(line)
    ):
        return False

    if patch.kind == "predicate_operator":
        return (
            patch.old_text.strip() in _COMPARISON_OPERATORS
            and patch.new_text.strip() in _COMPARISON_OPERATORS
            and patch.new_text.strip() == _unambiguous_question_operator(question)
        )

    if not _SCALAR_LITERAL_RE.fullmatch(patch.old_text.strip()):
        return False
    if not _SCALAR_LITERAL_RE.fullmatch(patch.new_text.strip()):
        return False

    if patch.kind == "string_representation":
        return bool(
            _QUOTED_LITERAL_RE.fullmatch(patch.old_text.strip())
            and _QUOTED_LITERAL_RE.fullmatch(patch.new_text.strip())
            and _sample_supports(patch, tables)
        )

    return _question_contains_literal(question, patch.new_text) or _sample_supports(
        patch, tables
    )


def apply_evidence_repairs(
    evidence: str,
    repairs: list[EvidenceRepairPatch],
    question: str,
    tables: list[dict[str, Any]],
) -> tuple[str, list[dict[str, str | int]]]:
    """Atomically apply validated patches, preserving all other evidence."""
    lines = evidence.splitlines(keepends=True)
    accepted: list[dict[str, str | int]] = []

    for patch in repairs:
        index = patch.line_number - 1
        if index < 0 or index >= len(lines) or evidence.count(patch.old_text) != 1:
            return evidence, []
        line = lines[index]
        if not _valid_patch(patch, line, question, tables):
            return evidence, []
        lines[index] = line.replace(patch.old_text, patch.new_text, 1)
        accepted.append(patch.model_dump())

    return "".join(lines), accepted


class EvidenceRefinementAgent(BaseAgent):
    """Correct certain evidence literals while leaving everything else verbatim."""

    def __init__(self) -> None:
        super().__init__("evidence_refinement")

    def validate_input(self, state: AgentState) -> bool:
        return bool((state.get("evidence") or "").strip())

    def execute(self, state: AgentState) -> Dict[str, Any]:
        evidence = state.get("evidence") or ""
        if not evidence.strip():
            return {}

        path_state = dict(state.get("path_state") or {})
        question = get_question_for_processing(state)
        tables = list(path_state.get("relevant_tables") or [])
        numbered_evidence = "\n".join(
            f"{index}| {line}" for index, line in enumerate(evidence.splitlines(), 1)
        )
        tables_block = format_tables_for_prompt(
            tables,
            target_db=path_state.get("target_db"),
        )
        prompt = (
            f"Sanitized question:\n{question}\n\n"
            f"Evidence (line numbers are metadata only):\n{numbered_evidence}\n\n"
            f"Relevant tables, columns, and sample values:\n{tables_block}"
        )

        result = safe_invoke_structured_nr(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)],
            EvidenceRefinementResult,
        )
        if result is None:
            self.logger.warning(
                "Evidence refinement failed — keeping original evidence"
            )
            return {"evidence": evidence, "path_state": path_state}

        refined, accepted = apply_evidence_repairs(
            evidence, result.repairs, question, tables
        )
        if result.reasoning.strip():
            record_thought(path_state, _GRAPH_NODE_NAME, result.reasoning.strip())
        if accepted:
            path_state["evidence_original"] = evidence
            path_state["evidence_repairs_applied"] = accepted
            self.logger.info("Applied %d evidence repair(s)", len(accepted))
        else:
            self.logger.info("No certain evidence repairs found")

        return {"evidence": refined, "path_state": path_state}


__all__ = [
    "EvidenceRefinementAgent",
    "EvidenceRefinementResult",
    "EvidenceRepairPatch",
    "apply_evidence_repairs",
]
