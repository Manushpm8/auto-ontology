# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-score an existing eval CSV with LLM-based logic/semantic scoring.

Reads a CSV produced by eval_chatbot.py (must contain ``question``,
``expected_sql``, ``returned_sql``, and optionally ``returned_answer``),
calls the LLM scorer for each row, and writes a new CSV with the additional
LLM score columns appended.

Usage::

    uv run python -m dev_tools.evaluation.score_existing \
        --input  dev_tools/evaluation/wwi_evaluation_scores.csv \
        --output dev_tools/evaluation/wwi_evaluation_scores_llm.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from gsf.server.env import load_server_env

load_server_env()

csv.field_size_limit(sys.maxsize)

logger = logging.getLogger("score_existing")

# ---------------------------------------------------------------------------
# LLM scorer (copied from eval_chatbot to keep this script standalone)
# ---------------------------------------------------------------------------

_LLM_SCORE_MODEL = os.environ.get("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
_LLM_SCORE_BASE_URL = os.environ.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
_LLM_SCORE_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

_SQL_SCORING_PROMPT = """\
You are an expert SQL evaluator. Your task is to score SQL code based on three separate criteria:

1. **Logic Match**: How well does the SQL logic answer the given question?
2. **Semantic Match**: How well do the SQL response types match the expected types for the question?
3. **Ground Truth Similarity**: How similar is the SQL to the provided ground truth SQL?

**Logic Match Scoring Guidelines:**
- Scores should be between 0.0 and 1.0
- Evaluate ONLY based on the question — do NOT compare to the ground truth SQL.
- Ask: does this SQL correctly answer the question on its own merits?
  - Does the SQL use appropriate tables, columns, and filters?
  - Does the query logic match the question's requirements?
  - Are the joins, aggregations, and conditions correct?
  - Does it capture the business logic behind the question?
- Provide short text in logic_issues explaining what reduced the score (e.g., "Missing WHERE clause for date filter", "Wrong aggregation function", "Incorrect table join")

**Semantic Match Scoring Guidelines:**
- Scores should be between 0.0 and 1.0
- Evaluate if the SQL response types match what the question expects:
  - **Format Appropriateness**: Does the response format match what the question is asking for?
    * Questions asking for "the earliest date" or "the maximum value" should return a single value, not a table of multiple values
    * Questions asking for "top 5" should return exactly 5 rows (or fewer if data doesn't exist)
    * Questions asking for specific single values should not return multiple rows
  - **Data Completeness**: Does the response include all relevant information requested?
    * Questions asking for "sales with and without discount" should include BOTH categories in results
    * Questions asking for comparisons should include all relevant comparison groups
    * Questions asking for detailed breakdowns should include all requested dimensions
  - **Important**: If the SQL expected types include the user's question expected types, it is acceptable

**Final Weighted Score:**
- Calculate as: (logic_match * 0.5) + (semantic_match * 0.5)

**Ground Truth Similarity Guidelines:**
- Compare the SQL structure, logic, tables used, and expected results
- Be lenient with minor syntax differences or equivalent approaches
- Focus on semantic similarity rather than exact text matching

**Question:** {question}

**SQL Code to Evaluate:**
{sql_code}

**Ground Truth SQL:**
{ground_truth_sql}

**SQL Result Preview (if available):**
{sql_result_preview}

Please provide all scores, logic issues text, and boolean flags based on your comprehensive evaluation.
"""

_LLM_SCORE_FIELDS = [
    "llm_logic_match",
    "llm_semantic_match",
    "llm_final_weighted_score",
    "llm_sql_vs_ground_truth",
    "llm_is_valid_sql",
    "llm_is_sql_returns_data",
    "llm_logic_issues",
]


class SqlScore(BaseModel):
    logic_match: float = Field(ge=0.0, le=1.0)
    logic_issues: str
    semantic_match: float = Field(ge=0.0, le=1.0)
    final_weighted_score: float = Field(ge=0.0, le=1.0)
    sql_compared_to_ground_truth_score: float = Field(ge=0.0, le=1.0)
    is_valid_sql: bool
    is_sql_returns_data: bool


def _llm_score_sql(
    question: str,
    sql_code: str,
    ground_truth_sql: str,
    sql_result_preview: str,
) -> SqlScore | None:
    if not sql_code:
        return None
    try:
        prompt = _SQL_SCORING_PROMPT.format(
            question=question,
            sql_code=sql_code,
            ground_truth_sql=ground_truth_sql or "(none provided)",
            sql_result_preview=sql_result_preview[:500] if sql_result_preview else "(none)",
        )
        if _LLM_SCORE_MODEL.startswith("openai/"):
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                model=_LLM_SCORE_MODEL,
                api_key=_LLM_SCORE_API_KEY,
                base_url=_LLM_SCORE_BASE_URL,
                max_tokens=8192,
            )
        else:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA

            llm = ChatNVIDIA(
                model=_LLM_SCORE_MODEL,
                api_key=_LLM_SCORE_API_KEY,
                base_url=_LLM_SCORE_BASE_URL,
                max_tokens=1024,
            )
        result = llm.with_structured_output(SqlScore).invoke(prompt)
        if isinstance(result, SqlScore):
            return result
        return SqlScore.model_validate(result)
    except Exception as exc:
        logger.warning("LLM scoring failed for question %r: %s", question[:60], exc)
        return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_DEFAULT_INPUT = Path(__file__).parent / "wwi_evaluation_scores.csv"
_DEFAULT_OUTPUT = Path(__file__).parent / "wwi_evaluation_scores_llm.csv"


def run(input_path: Path, output_path: Path) -> None:
    with input_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        original_fields = list(reader.fieldnames or [])

    # Build output fieldnames: existing fields + new LLM fields (skip if already present)
    new_fields = [f for f in _LLM_SCORE_FIELDS if f not in original_fields]
    out_fields = original_fields + new_fields

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(rows)
    scored = 0

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()

        for i, row in enumerate(rows):
            question = row.get("question", "")
            returned_sql = row.get("returned_sql", "")
            expected_sql = row.get("expected_sql", "")
            returned_answer = row.get("returned_answer", "")

            logger.info("[%d/%d] Scoring q%s: %s", i + 1, total, row.get("question_id", i + 1), question[:80])

            score = _llm_score_sql(
                question=question,
                sql_code=returned_sql,
                ground_truth_sql=expected_sql,
                sql_result_preview=returned_answer,
            )

            out_row = dict(row)
            if score:
                out_row["llm_logic_match"] = score.logic_match
                out_row["llm_semantic_match"] = score.semantic_match
                out_row["llm_final_weighted_score"] = score.final_weighted_score
                out_row["llm_sql_vs_ground_truth"] = score.sql_compared_to_ground_truth_score
                out_row["llm_is_valid_sql"] = score.is_valid_sql
                out_row["llm_is_sql_returns_data"] = score.is_sql_returns_data
                out_row["llm_logic_issues"] = score.logic_issues
                scored += 1
            else:
                for field in _LLM_SCORE_FIELDS:
                    out_row.setdefault(field, "")

            writer.writerow(out_row)
            f.flush()

    logger.info("Scored %d/%d rows. Output: %s", scored, total, output_path)

    # Print summary averages
    try:
        import pandas as pd

        df = pd.read_csv(output_path)

        def _avg(col: str) -> float:
            return pd.to_numeric(df[col], errors="coerce").mean() if col in df.columns else float("nan")

        sep = "=" * 50
        print(f"\n{sep}")
        print(f"  LLM SCORING SUMMARY  ({len(df)} questions)")
        print(sep)
        print(f"  Logic match            : {_avg('llm_logic_match'):.4f}")
        print(f"  Semantic match         : {_avg('llm_semantic_match'):.4f}")
        print(f"  Final weighted score   : {_avg('llm_final_weighted_score'):.4f}")
        print(f"  SQL vs ground truth    : {_avg('llm_sql_vs_ground_truth'):.4f}")
        print(sep)
    except Exception as exc:
        logger.warning("Could not compute summary: %s", exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help=f"Input scores CSV (default: {_DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output CSV with LLM scores (default: {_DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    run(input_path=args.input, output_path=args.output)
