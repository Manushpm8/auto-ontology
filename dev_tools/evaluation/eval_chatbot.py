# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Evaluate the text-to-SQL agent against a chatbot evaluation JSON file.

For every entry in the JSON array (each containing ``question_id``,
``question``, ``SQL`` (expected), and ``answer_raw`` (expected user-facing
result)), this script:

1. Calls ``get_agent_response`` with the question (same path as
   ``ingest_postgres.run_retrieve``).
2. Scores the agent's SQL against the expected SQL by **executing both**
   queries against the live Postgres connector and comparing the resulting
   row sets.  Failure to execute either side yields score 0.
3. Scores the agent's answer text against ``answer_raw`` via difflib
   similarity and a normalised substring check.
4. Writes one row per question to a CSV.  Any per-question exception is
   logged, recorded in the ``error`` column, and scored 0 — execution
   continues with the next question.

Usage::

    uv run python -m dev_tools.evaluation.eval_chatbot \
        [--input PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import logging
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field

from nemo_retriever.params import EmbedParams
from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import get_agent_response
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload

from gsf.connectors import get_connectors
from gsf.server.env import load_server_env
from gsf.vdb import get_data_vdb, get_semantic_vdb

load_server_env()

# The text-to-SQL agent stores executed-DB rows under this key on its result dict.
_DB_RESULT_KEY = "sql_response_from_db"

# Maximum characters stored in the CSV for DB result columns (prevents huge files).
_MAX_DB_RESULT_CHARS = 500

logger = logging.getLogger("eval_chatbot")

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
if not _NVIDIA_API_KEY:
    raise EnvironmentError(
        "NVIDIA_API_KEY is not set. "
        "Export it before running:\n\n"
        "    export NVIDIA_API_KEY='nvapi-...'\n\n"
        "Get your key at https://build.nvidia.com"
    )

# Match the chat server's wiring (gsf/server/chat/helpers.py): same embed
# endpoint/model as ingest, same retriever, same pgvector store. Anything
# else here and scoring stops being apples-to-apples with production.
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

EMBED_PARAMS = EmbedParams(
    embed_invoke_url=_EMBED_ENDPOINT,
    model_name=_EMBED_MODEL,
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)

_DEFAULT_INPUT = Path(__file__).parent / "chatbot_evaluation.json"


_DEFAULT_OUTPUT = Path(__file__).parent / "chatbot_evaluation_scores.csv"


def _build_connectors() -> list:
    """Build source-DB connectors from ``CONNECTION_STRINGS``."""
    connectors = get_connectors()
    if not connectors:
        raise EnvironmentError(
            "CONNECTION_STRINGS is not set. Add it to your .env, e.g.:\n\n"
            "    CONNECTION_STRINGS=snowflake://user:pass@account?warehouse=WH&database=DB"
        )
    return connectors


def _build_retriever() -> Retriever:
    """Build the retriever against the local pgvector store."""
    return Retriever(
        top_k=15,
        vdb_kwargs={"vdb": get_data_vdb()},
        embed_kwargs={
            "model_name": EMBED_PARAMS.model_name,
            "embed_invoke_url": EMBED_PARAMS.embed_invoke_url,
            "api_key": EMBED_PARAMS.api_key,
        },
    )


def _build_ontology_retriever() -> Retriever:
    """Build a retriever for the semantic-layer ontology collection."""
    return Retriever(
        top_k=15,
        vdb_kwargs={"vdb": get_semantic_vdb()},
        embed_kwargs={
            "model_name": EMBED_PARAMS.model_name,
            "embed_invoke_url": EMBED_PARAMS.embed_invoke_url,
            "api_key": EMBED_PARAMS.api_key,
        },
    )


# -----------------------------------------------------------------------------
# Scoring helpers
# -----------------------------------------------------------------------------


def _normalize_text(s: str) -> str:
    """Lowercase, collapse whitespace, drop trailing semicolons."""
    if s is None:
        return ""
    s = str(s).strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _sql_text_similarity(expected: str, actual: str) -> float:
    a = _normalize_text(expected)
    b = _normalize_text(actual)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _df_values_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """Compare two DataFrames by row-multiset of values, ignoring column names/order."""
    try:
        if a.shape != b.shape:
            return False
        a_rows = sorted(tuple(_canonical(v) for v in row) for row in a.values.tolist())
        b_rows = sorted(tuple(_canonical(v) for v in row) for row in b.values.tolist())
        return a_rows == b_rows
    except Exception:
        return False


def _canonical(value: Any) -> Any:
    """Make a value hashable and comparable across small numeric/string drift."""
    if value is None:
        return None
    if isinstance(value, float):
        # Round to mitigate float jitter from aggregations
        return round(value, 4)
    return str(value).strip().lower()


_EXEC_ROW_LIMIT = 500


def _execute_sql(
    connector: Any, sql: str
) -> Tuple[Optional[pd.DataFrame], str]:
    if not sql or not sql.strip():
        return None, "empty SQL"
    try:
        limited_sql = f"SELECT * FROM ({sql.rstrip(';')}) AS _eval_subq LIMIT {_EXEC_ROW_LIMIT}"
        df = connector.execute(limited_sql)
        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)
        return df.head(_MAX_RESULT_ROWS), ""
    except Exception as exc:  # pragma: no cover - tooling script
        return None, f"{type(exc).__name__}: {exc}"


def _score_sql(connector: Any, expected: str, actual: str) -> Dict[str, Any]:
    text_sim = _sql_text_similarity(expected, actual)
    expected_df, expected_err = _execute_sql(connector, expected)
    actual_df, actual_err = _execute_sql(connector, actual)
    exec_match = 0
    if expected_df is not None and actual_df is not None:
        exec_match = 1 if _df_values_equal(expected_df, actual_df) else 0
    return {
        "sql_text_similarity": round(text_sim, 4),
        "sql_exec_match": exec_match,
        "expected_sql_error": expected_err,
        "returned_sql_error": actual_err,
        "expected_sql_result": _stringify_db_result(expected_df)
        if expected_df is not None
        else "",
    }


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_numbers(text: str) -> List[float]:
    if not text:
        return []
    out = []
    for tok in _NUM_RE.findall(str(text)):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _parse_markdown_table(md: str) -> Optional[pd.DataFrame]:
    """Parse a simple markdown table into a DataFrame, or None on failure."""
    if not md:
        return None
    lines = [ln.strip() for ln in md.strip().splitlines() if ln.strip()]
    # Need at least header + separator + one data row
    if len(lines) < 3:
        return None
    data_lines = [ln for ln in lines if not re.match(r"^\|[\s:_-]+\|$", ln)]
    if len(data_lines) < 2:
        return None
    header = [c.strip() for c in data_lines[0].strip("|").split("|")]
    rows = []
    for line in data_lines[1:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(header):
            rows.append(cells)
    if not rows:
        return None
    return pd.DataFrame(rows, columns=header)


def _db_result_to_df(value: str) -> Optional[pd.DataFrame]:
    """Try to parse the stringified DB result into a DataFrame."""
    if not value:
        return None
    text = str(value).strip()
    # Unwrap outer list wrapper like ['[{"count":712}]']
    if text.startswith("[") and text.endswith("]"):
        try:
            outer = json.loads(text)
            if (
                isinstance(outer, list)
                and len(outer) == 1
                and isinstance(outer[0], str)
            ):
                text = outer[0]
        except (json.JSONDecodeError, TypeError):
            pass
    # Try JSON array of objects
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return pd.DataFrame(parsed)
        if isinstance(parsed, dict):
            return pd.DataFrame([parsed])
    except (json.JSONDecodeError, TypeError):
        pass
    # Try CSV
    try:
        from io import StringIO

        df = pd.read_csv(StringIO(text))
        if not df.empty:
            return df
    except Exception:
        pass
    return None


def _score_answer(expected_raw: str, returned_db_str: str) -> Dict[str, Any]:
    """Score the agent's answer against the expected ``answer_raw`` markdown table.

    Strategy (in priority order):
    1. Parse both sides into DataFrames and compare row-multisets (structural match).
    2. Compare the multiset of numeric values (survives formatting differences).
    3. Fall back to fuzzy text similarity.
    """
    expected_df = _parse_markdown_table(expected_raw)
    if expected_df is None:
        expected_df = _db_result_to_df(expected_raw)
    actual_df = _db_result_to_df(returned_db_str)

    structural_match = 0
    if expected_df is not None and actual_df is not None:
        structural_match = 1 if _df_values_equal(expected_df, actual_df) else 0

    haystack = str(returned_db_str or "")

    expected_nums = sorted(round(n, 4) for n in _extract_numbers(expected_raw))
    actual_nums = sorted(round(n, 4) for n in _extract_numbers(haystack))
    if not expected_nums and not actual_nums:
        nums_match = 1
    else:
        nums_match = 1 if expected_nums and expected_nums == actual_nums else 0

    sim = (
        difflib.SequenceMatcher(
            None, _normalize_text(expected_raw), _normalize_text(haystack)
        ).ratio()
        if expected_raw and haystack
        else 0.0
    )
    if structural_match:
        sim = 1.0
    elif nums_match:
        sim = max(sim, 1.0)

    return {
        "answer_text_similarity": round(sim, 4),
        "answer_numbers_match": nums_match,
    }


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------


def _load_questions(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(
            f"Evaluation file not found: {path}\n"
            f"Pass --input <path> or create the default file at "
            f"{_DEFAULT_INPUT}."
        )
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON array of questions, got {type(data).__name__}"
        )
    return data


_MAX_RESULT_ROWS = 50


def _stringify_db_result(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, pd.DataFrame):
        total = len(value)
        if total > _MAX_RESULT_ROWS:
            value = value.head(_MAX_RESULT_ROWS)
        text = value.to_csv(index=False)
        if total > _MAX_RESULT_ROWS:
            text += f"... truncated ({total} rows total)\n"
        return text
    if isinstance(value, list):
        total = len(value)
        items = value[:_MAX_RESULT_ROWS]
        try:
            text = json.dumps(items, default=str)
        except (TypeError, ValueError):
            text = str(items)
        if total > _MAX_RESULT_ROWS:
            text += f"\n... truncated ({total} rows total)"
        return text
    return str(value)


class SqlScore(BaseModel):
    logic_match: float = Field(ge=0.0, le=1.0)
    logic_issues: str
    semantic_match: float = Field(ge=0.0, le=1.0)
    final_weighted_score: float = Field(ge=0.0, le=1.0)
    sql_compared_to_ground_truth_score: float = Field(ge=0.0, le=1.0)
    is_valid_sql: bool
    is_sql_returns_data: bool


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

_LLM_SCORE_MODEL = os.environ.get("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
_LLM_SCORE_BASE_URL = os.environ.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
_LLM_SCORE_API_KEY = os.environ.get("NVIDIA_API_KEY", "")


def _llm_score_sql(
    question: str,
    sql_code: str,
    ground_truth_sql: str,
    sql_result_preview: str,
) -> SqlScore | None:
    """Call the LLM to score SQL logic, semantic match, and ground truth similarity."""
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
        logger.warning("LLM scoring failed: %s", exc)
        return None


CSV_FIELDS = [
    "row_index",
    "question_id",
    "difficulty",
    "question",
    "expected_sql",
    "returned_sql",
    "sql_text_similarity",
    "sql_exec_match",
    "expected_sql_error",
    "returned_sql_error",
    "expected_sql_result",
    "expected_answer_raw",
    "returned_answer",
    "answer_text_similarity",
    "answer_numbers_match",
    "llm_logic_match",
    "llm_semantic_match",
    "llm_final_weighted_score",
    "llm_sql_vs_ground_truth",
    "llm_is_valid_sql",
    "llm_is_sql_returns_data",
    "llm_logic_issues",
    "runtime_seconds",
    "error",
]


def _print_agent_result(
    qid: Any,
    question: str,
    agent_result: Dict[str, Any] | None,
    expected_sql: str = "",
    expected_sql_result: str = "",
    returned_sql_result: str = "",
) -> None:
    """Pretty-print the agent result to stdout for quick visual inspection."""
    sep = "=" * 80
    print(f"\n{sep}")
    print(f"  Question {qid}: {question}")
    print(sep)
    if expected_sql:
        print("\n  [expected_sql]")
        for line in expected_sql.splitlines():
            print(f"    {line}")
    if expected_sql_result:
        print("\n  [expected_sql_result]")
        for line in expected_sql_result.splitlines():
            print(f"    {line}")
    if not agent_result:
        print("  (no result)")
        print(sep)
        return
    for key in ("sql_code", "response"):
        val = agent_result.get(key)
        if val is None:
            continue
        print(f"\n  [{key}]")
        text = str(val)
        if key == "sql_response_from_db" and len(text) > 100:
            text = text[:100] + f"  ... ({len(text)} chars total)"
        for line in text.splitlines():
            print(f"    {line}")
    if returned_sql_result:
        print("\n  [returned_sql_result]")
        for line in returned_sql_result.splitlines():
            print(f"    {line}")
    remaining = {
        k: v
        for k, v in agent_result.items()
        if k not in ("sql_code", "response", "sql_response_from_db")
    }
    if remaining:
        print("\n  [other keys]")
        for k, v in remaining.items():
            print(f"    {k}: {v}")
    print(sep)


def evaluate(
    input_path: Path,
    output_path: Path,
    start_index: int = 0,
    end_index: int | None = None,
    question_ids: list[int] | None = None,
) -> None:
    all_questions = _load_questions(input_path)
    if question_ids is not None:
        id_set = set(question_ids)
        questions = [q for q in all_questions if q.get("question_id") in id_set]
        logger.info("Running %d selected question(s) (ids: %s) from %s", len(questions), sorted(id_set), input_path)
    else:
        questions = all_questions[start_index:end_index]
        logger.info(
            "Running questions %d–%d (%d of %d total) from %s",
            start_index,
            start_index + len(questions) - 1,
            len(questions),
            len(all_questions),
            input_path,
        )

    connectors = _build_connectors()
    retriever = _build_retriever()
    ontology_retriever = _build_ontology_retriever()

    resuming = start_index > 0 and output_path.exists()
    mode = "a" if resuming else "w"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open(mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not resuming:
            writer.writeheader()

        for idx, item in enumerate(questions, start=start_index):
            qid = item.get("question_id", idx)
            question = item.get("question", "")
            expected_sql = item.get("SQL", "")
            expected_answer = item.get("answer_raw", "")
            difficulty = item.get("difficulty", "")
            logger.info("[%d/%d] q%s: %s", idx + 1, len(questions), qid, question)

            row: Dict[str, Any] = {
                "row_index": idx,
                "question_id": qid,
                "difficulty": difficulty,
                "question": question,
                "expected_sql": expected_sql,
                "returned_sql": "",
                "sql_text_similarity": 0.0,
                "sql_exec_match": 0,
                "expected_sql_error": "",
                "returned_sql_error": "",
                "expected_answer_raw": expected_answer,
                "returned_answer": "",
                "answer_text_similarity": 0.0,
                "answer_numbers_match": 0,
                "llm_logic_match": "",
                "llm_semantic_match": "",
                "llm_final_weighted_score": "",
                "llm_sql_vs_ground_truth": "",
                "llm_is_valid_sql": "",
                "llm_is_sql_returns_data": "",
                "llm_logic_issues": "",
                "runtime_seconds": "",
                "error": "",
            }

            t0 = time.perf_counter()
            try:
                payload: AgentPayload = {
                    "question": question,
                    "retriever": retriever,
                    "ontology_retriever": ontology_retriever,
                    "connectors": connectors,
                    "path_state": {},
                    "custom_prompts": "",
                    "acronyms": [],
                }
                agent_result = get_agent_response(payload)

                returned_sql = (agent_result or {}).get("sql_code", "") or ""
                returned_db = (agent_result or {}).get(_DB_RESULT_KEY)
                returned_db_str = _stringify_db_result(returned_db)

                score = _score_sql(connectors[0], expected_sql, returned_sql)
                expected_result_str = score.get("expected_sql_result", "")

                _print_agent_result(
                    qid,
                    question,
                    agent_result,
                    expected_sql,
                    expected_sql_result=expected_result_str,
                    returned_sql_result=returned_db_str,
                )

                row["returned_sql"] = returned_sql
                row["returned_answer"] = returned_db_str[:_MAX_DB_RESULT_CHARS]

                row.update(score)
                row.update(_score_answer(expected_answer, returned_db_str))

                llm_score = _llm_score_sql(
                    question=question,
                    sql_code=returned_sql,
                    ground_truth_sql=expected_sql,
                    sql_result_preview=returned_db_str,
                )
                if llm_score:
                    row["llm_logic_match"] = llm_score.logic_match
                    row["llm_semantic_match"] = llm_score.semantic_match
                    row["llm_final_weighted_score"] = llm_score.final_weighted_score
                    row["llm_sql_vs_ground_truth"] = llm_score.sql_compared_to_ground_truth_score
                    row["llm_is_valid_sql"] = llm_score.is_valid_sql
                    row["llm_is_sql_returns_data"] = llm_score.is_sql_returns_data
                    row["llm_logic_issues"] = llm_score.logic_issues
            except Exception as exc:
                logger.exception("Question %s failed", qid)
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["error"] += (
                    " | " + traceback.format_exc().replace("\n", " | ")[:1000]
                )
            finally:
                row["runtime_seconds"] = round(time.perf_counter() - t0, 2)
                writer.writerow(row)
                f.flush()

    logger.info("Wrote scores to %s", output_path)
    _print_summary(output_path)


def _print_summary(output_path: Path) -> None:
    """Read the scores CSV and print average metrics to stdout."""
    try:
        import pandas as pd

        df = pd.read_csv(output_path)
        n = len(df)

        def _avg(col: str) -> float:
            return pd.to_numeric(df[col], errors="coerce").mean() if col in df.columns else float("nan")

        avg_exec = _avg("sql_exec_match")
        avg_duration = _avg("runtime_seconds")
        avg_logic = _avg("llm_logic_match")
        avg_semantic = _avg("llm_semantic_match")
        avg_weighted = _avg("llm_final_weighted_score")
        avg_vs_gt = _avg("llm_sql_vs_ground_truth")

        sep = "=" * 50
        print(f"\n{sep}")
        print(f"  EVALUATION SUMMARY  ({n} questions)")
        print(sep)
        print(f"  SQL exec match (exact)     : {avg_exec:.4f}")
        print(f"  LLM logic match            : {avg_logic:.4f}")
        print(f"  LLM semantic match         : {avg_semantic:.4f}")
        print(f"  LLM final weighted score   : {avg_weighted:.4f}")
        print(f"  LLM SQL vs ground truth    : {avg_vs_gt:.4f}")
        print(f"  Average duration (s)       : {avg_duration:.3f}")
        print(sep)
    except Exception as exc:
        logger.warning("Could not compute summary: %s", exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help=f"Input JSON path (default: {_DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--consistency",
        action="store_true",
        default=False,
        help="Run consistency evaluation (repeat N times and report SQL/answer stability).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=10,
        help="Number of runs for consistency evaluation (default: 10).",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        default=False,
        help="Run a single hardcoded query (edit SINGLE_QUERY in the script).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="1-based index of the first question to run (inclusive).",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="1-based index of the last question to run (inclusive).",
    )
    parser.add_argument(
        "--questions",
        type=str,
        default=None,
        help="Comma-separated list of question_ids to run (e.g. 1,40,41). Overrides --start/--end.",
    )
    return parser.parse_args()


def _write_consistency_csv(
    csv_path: Path,
    questions: list,
    results: Dict[int, list],
    completed_runs: int,
    start_index: int,
) -> None:
    """Write/overwrite the consistency CSV with all data collected so far."""
    fieldnames = ["question_id", "question"]
    for r in range(1, completed_runs + 1):
        fieldnames.extend([f"sql_run_{r}", f"answer_run_{r}"])
    fieldnames.extend(["sql_consistency", "answer_consistency"])

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for q_idx, item in enumerate(questions):
            qid = item.get("question_id", start_index + q_idx)
            question = item.get("question", "")
            run_results = results[q_idx]

            row: Dict[str, Any] = {"question_id": qid, "question": question}

            first_sql: Dict[str, int] = {}
            first_answer: Dict[str, int] = {}

            for r_idx, r in enumerate(run_results):
                run_num = r_idx + 1
                sql_val = r["sql"]
                ans_val = r["answer"]

                if sql_val in first_sql:
                    row[f"sql_run_{run_num}"] = f"same as run {first_sql[sql_val]}"
                else:
                    first_sql[sql_val] = run_num
                    row[f"sql_run_{run_num}"] = sql_val

                if ans_val in first_answer:
                    row[f"answer_run_{run_num}"] = (
                        f"same as run {first_answer[ans_val]}"
                    )
                else:
                    first_answer[ans_val] = run_num
                    row[f"answer_run_{run_num}"] = ans_val

            sql_counts = {}
            answer_counts = {}
            for r in run_results:
                sql_counts[r["sql"]] = sql_counts.get(r["sql"], 0) + 1
                answer_counts[r["answer"]] = answer_counts.get(r["answer"], 0) + 1
            row["sql_consistency"] = (
                f"{max(sql_counts.values())}/{completed_runs}" if sql_counts else ""
            )
            row["answer_consistency"] = (
                f"{max(answer_counts.values())}/{completed_runs}"
                if answer_counts
                else ""
            )

            writer.writerow(row)


def evaluate_consistency(
    input_path: Path,
    output_path: Path,
    start_index: int = 0,
    end_index: int | None = None,
    runs: int = 10,
) -> None:
    """Run each question multiple times and report SQL/answer consistency."""
    all_questions = _load_questions(input_path)
    questions = all_questions[start_index:end_index]
    logger.info(
        "Consistency eval: %d questions, %d runs each, output=%s",
        len(questions),
        runs,
        output_path,
    )

    connectors = _build_connectors()
    retriever = _build_retriever()
    ontology_retriever = _build_ontology_retriever()

    results: Dict[int, list] = {i: [] for i in range(len(questions))}

    for run_num in range(1, runs + 1):
        print(f"\n{'=' * 60}")
        print(f"  RUN {run_num}/{runs}")
        print(f"{'=' * 60}")

        for q_idx, item in enumerate(questions):
            qid = item.get("question_id", start_index + q_idx)
            question = item.get("question", "")
            logger.info("[Run %d] q%s: %s", run_num, qid, question)

            try:
                payload: AgentPayload = {
                    "question": question,
                    "retriever": retriever,
                    "ontology_retriever": ontology_retriever,
                    "connectors": connectors,
                    "path_state": {},
                    "custom_prompts": "",
                    "acronyms": [],
                }
                agent_result = get_agent_response(payload)
                returned_sql = _normalize_text(
                    (agent_result or {}).get("sql_code", "") or ""
                )
                returned_db = (agent_result or {}).get(_DB_RESULT_KEY)
                returned_db_str = _stringify_db_result(returned_db)[:_MAX_DB_RESULT_CHARS]
            except Exception as exc:
                logger.exception("Run %d, question %s failed", run_num, qid)
                returned_sql = f"ERROR: {exc}"
                returned_db_str = ""

            results[q_idx].append({"sql": returned_sql, "answer": returned_db_str})

        print(f"\n--- After run {run_num} ---")
        for q_idx, item in enumerate(questions):
            qid = item.get("question_id", start_index + q_idx)
            run_results = results[q_idx]
            sqls = [r["sql"] for r in run_results]
            answers = [r["answer"] for r in run_results]
            print(
                f"  q{qid}: {len(sqls)} runs -> "
                f"{len(set(sqls))} unique SQLs, "
                f"{len(set(answers))} unique answers"
            )

        _write_consistency_csv(output_path, questions, results, run_num, start_index)
        logger.info("Updated consistency CSV: %s (after run %d)", output_path, run_num)

    print(f"\n{'=' * 60}")
    print(f"  CONSISTENCY SUMMARY ({runs} runs)")
    print(f"{'=' * 60}")

    for q_idx, item in enumerate(questions):
        qid = item.get("question_id", start_index + q_idx)
        question = item.get("question", "")
        run_results = results[q_idx]

        sql_counts: Dict[str, int] = {}
        answer_counts: Dict[str, int] = {}
        sql_to_answer: Dict[str, str] = {}

        for r in run_results:
            sql_counts[r["sql"]] = sql_counts.get(r["sql"], 0) + 1
            answer_counts[r["answer"]] = answer_counts.get(r["answer"], 0) + 1
            sql_to_answer[r["sql"]] = r["answer"]

        print(f"\n  q{qid}: {question}")
        print(f"  {'─' * 50}")
        for sql, count in sorted(sql_counts.items(), key=lambda x: -x[1]):
            print(f"    SQL ({count}/{runs}): {sql[:500]}")
            print(f"    Answer: {sql_to_answer[sql][:500]}")
            print()

        most_common_sql = max(sql_counts.values())
        most_common_answer = max(answer_counts.values())
        print(f"    -> SQL consistency:    {most_common_sql}/{runs}")
        print(f"    -> Answer consistency: {most_common_answer}/{runs}")

    logger.info("Final consistency CSV: %s", output_path)


def run_single_query(question: str) -> None:
    """Run a single question through the agent and print the result."""
    connectors = _build_connectors()
    retriever = _build_retriever()
    ontology_retriever = _build_ontology_retriever()

    payload: AgentPayload = {
        "question": question,
        "retriever": retriever,
        "ontology_retriever": ontology_retriever,
        "connectors": connectors,
        "path_state": {},
        "custom_prompts": "",
        "acronyms": [],
    }
    t0 = time.perf_counter()
    agent_result = get_agent_response(payload)
    elapsed = round(time.perf_counter() - t0, 2)

    _print_agent_result("single", question, agent_result)
    print(f"\n  Runtime: {elapsed}s")


SINGLE_QUERY = "list all actors"

START_INDEX = 0
END_INDEX = None  # None = run to the end
CONSISTENCY_RUNS = 10
RUN_CONSISTENCY = False

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    _start = (args.start - 1) if args.start is not None else START_INDEX
    _end = args.end if args.end is not None else END_INDEX
    _qids = [int(x.strip()) for x in args.questions.split(",")] if args.questions else None
    if args.single:
        run_single_query(SINGLE_QUERY)
    elif RUN_CONSISTENCY or args.consistency:
        num_runs = args.runs if args.consistency else CONSISTENCY_RUNS
        consistency_output = Path(__file__).parent / "chatbot_consistency_scores.csv"
        evaluate_consistency(
            input_path=args.input,
            output_path=consistency_output,
            start_index=_start,
            end_index=_end,
            runs=num_runs,
        )
    else:
        evaluate(
            input_path=args.input,
            output_path=args.output,
            start_index=_start,
            end_index=_end,
            question_ids=_qids,
        )
