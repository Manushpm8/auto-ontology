"""
Live test: simulating user responses to an output-type clarification question.

The test asks a hardcoded "would you like a scalar or table?" question to a
user-simulator LLM for selected BIRD-Interact FULL instances.  For each case
we record and assert on three behaviours:
  - answered   : user gave a clear scalar / table preference
  - extra_info : user voluntarily revealed additional context
  - out_of_scope: user refused / said it does not apply

Instances are drawn from every signal-strength bucket identified in the
scalar-pattern analysis of the BIRD-Interact FULL dataset (600 instances,
24 % scalar):
  - clear          : explicit "how many / how much / what is the average" phrasing
  - implicit       : aggregate word present but no textbook trigger
  - table_leaning  : phrasing reads like a table request but GT is scalar
  - truly_ambiguous: no linguistic hint at all

Run with:
    uv run pytest gsf/tests/interactive/test_output_type_question.py -s -v
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from gsf.utils.llm_invoke import get_llm_client, get_non_reasoning_llm_client

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# These point into the separate BIRD-Interact FULL benchmark checkout (not
# part of this repo, and not produced by any seed script) — there is no
# machine-independent default, so both env vars are required.


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. This test needs a local BIRD-Interact FULL "
            f"checkout; set {name} to the appropriate path before running, e.g.:\n"
            f"  export {name}=/path/to/BIRD-Interact/bird-interact-full"
        )
    return value


_FULL_DATASET = Path(_require_env("BIRD_INTERACT_FULL_DATA"))
_FULL_DB_ROOT = Path(_require_env("BIRD_INTERACT_FULL_DB_ROOT"))


def _load_instances() -> dict[str, dict]:
    instances: dict[str, dict] = {}
    with _FULL_DATASET.open() as f:
        for line in f:
            d = json.loads(line)
            instances[d["instance_id"]] = d
    return instances


def _load_schema(db_name: str) -> str:
    schema_file = _FULL_DB_ROOT / db_name / f"{db_name}_schema.txt"
    if schema_file.exists():
        return schema_file.read_text()
    return "(schema not found)"


_INSTANCES = _load_instances()

# ---------------------------------------------------------------------------
# Hardcoded output-type clarification questions
# Two variants — specific vs. vague — run for every case so we can compare.
# ---------------------------------------------------------------------------

# Explicit: names scalar/table options directly
OUTPUT_Q_SPECIFIC = (
    "Would you like the result as a single summary value (one number / one row), "
    "or broken down row-by-row for each item in the result?"
)

# Vague: asks about shape/format without naming the options
OUTPUT_Q_VAGUE = (
    "What output shape or format are you expecting for this result?"
)

OUTPUT_QUESTIONS = {
    "specific": OUTPUT_Q_SPECIFIC,
    "vague": OUTPUT_Q_VAGUE,
}

# ---------------------------------------------------------------------------
# User-simulator prompt (inline, inspired by usersim-guard USER_SIMULATOR_BASE)
# ---------------------------------------------------------------------------

_USER_SIM_PROMPT = """\
You are a data analyst at a company who asked the question below.
An AI assistant is about to help you write a SQL query, but first it has a \
clarification question for you.

DB name: {db_name}

--- DB Schema (for your reference only — do not reveal table/column names) ---
{db_schema}

--- Your original question ---
{user_query}

--- Ground-truth SQL (for your reference only — do not reveal it to the AI) ---
{correct_sql}

--- AI clarification question ---
{clarification_question}

Instructions:
1. Answer the AI's question naturally, as a non-technical user would.
2. You may refer to the ground-truth SQL to understand what you actually want,
   but do NOT mention the SQL or any table/column names in your answer.
3. If the question does not apply to your request (e.g. it asks something
   irrelevant), say so politely and briefly.
4. You may add any extra context that feels natural, but keep your answer concise.

Your answer:"""


def _simulate_user(instance_id: str, llm, clarification_question: str) -> str:
    """Run the user-simulator for one BIRD-Interact instance and return the response."""
    d = _INSTANCES[instance_id]
    db_name = d["selected_database"]
    schema = _load_schema(db_name)
    sol_sql = (d.get("sol_sql") or [""])[0]

    prompt = _USER_SIM_PROMPT.format(
        db_name=db_name,
        db_schema=schema[:3000],  # truncate very long schemas
        user_query=d["amb_user_query"],
        correct_sql=sol_sql,
        clarification_question=clarification_question,
    )
    return llm.invoke(prompt).content.strip()


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

_SCALAR_SIGNALS = re.compile(
    r"\b(single|one \w* ?(number|value|row|figure|result)|scalar|just (the )?one|"
    r"summary (value|number)|overall (number|total|average|value)|"
    r"single (number|value|total|average|figure|result|score)|"
    r"one (overall|summary|total|average|combined))\b",
    re.IGNORECASE,
)
_TABLE_SIGNALS = re.compile(
    r"\b(breakdown|broken.?down|row.?by.?row|per (item|row|entry|plant|artifact|show)|"
    r"list (of|all)|each (item|row|entry|plant|artifact)|multiple rows|table)\b",
    re.IGNORECASE,
)
_REFUSAL_SIGNALS = re.compile(
    r"\b(not (applicable|relevant|sure how)|does(n'?t| not) (apply|matter)|"
    r"(either|both) (would|is|are) (fine|ok|good)|out of scope|"
    r"can'?t answer|cannot answer|not sure|doesn'?t matter)\b",
    re.IGNORECASE,
)

def _classify(response: str) -> dict:
    scalar   = bool(_SCALAR_SIGNALS.search(response))
    table    = bool(_TABLE_SIGNALS.search(response))
    refusal  = bool(_REFUSAL_SIGNALS.search(response))
    # "extra info" = response > ~30 words and contains more than just the cardinality answer
    word_count = len(response.split())
    extra = word_count > 35 and not refusal
    answered = (scalar or table) and not refusal
    return {
        "scalar": scalar,
        "table": table,
        "refusal": refusal,
        "answered": answered,
        "extra_info": extra,
        "word_count": word_count,
    }


# ---------------------------------------------------------------------------
# Test cases — one per signal-strength bucket, plus all examples from analysis
# ---------------------------------------------------------------------------

CASES = [
    # ── CLEAR signal bucket ────────────────────────────────────────────────
    {
        "id": "solar_panel_5",
        "bucket": "clear",
        "description": "'How many' — unambiguous count scalar",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "solar_panel_7",
        "bucket": "clear",
        "description": "'How much does quality drop' — clear scalar delta",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "solar_panel_9",
        "bucket": "clear",
        "description": "'How much money do we lose' — clear scalar sum",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "solar_panel_11",
        "bucket": "clear",
        "description": "'How long does it typically take' — clear scalar average",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "solar_panel_12",
        "bucket": "clear",
        "description": "'How many times' — explicit count",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    # ── IMPLICIT signal bucket ─────────────────────────────────────────────
    {
        "id": "solar_panel_8",
        "bucket": "implicit",
        "description": "'Average cost to fix' — aggregate word present but no trigger phrase",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "polar_equipment_17",
        "bucket": "implicit",
        "description": "'Overall reliability… average score' — bare aggregate words",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "sports_events_12",
        "bucket": "implicit",
        "description": "'Average performance measure… round the result' — implicit scalar",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    {
        "id": "cybermarket_pattern_10",
        "bucket": "implicit",
        "description": "'Average keyword-hit density' — implicit single value",
        "expected_output_type": "scalar",
        "expect_answered": True,
    },
    # ── TABLE-LEANING language, scalar GT ─────────────────────────────────
    {
        "id": "hulushows_14",
        "bucket": "table_leaning",
        "description": "'Which entry is most promo-saturated? Return the maximum ratio.' — 'which' sounds tabular",
        "expected_output_type": "scalar",
        # User may answer scalar (guided by GT SQL) or could be confused
        "expect_answered": None,  # not strictly asserted — observe only
    },
    {
        "id": "hulushows_5",
        "bucket": "table_leaning",
        "description": "'I want the count of shows…' — phrasing implies single count",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    # ── TRULY AMBIGUOUS — no linguistic hint ──────────────────────────────
    {
        "id": "solar_panel_2",
        "bucket": "truly_ambiguous",
        "description": "'What's the potential financial hit' — zero scalar signal",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    {
        "id": "solar_panel_14",
        "bucket": "truly_ambiguous",
        "description": "'Worst case of dirt/grime buildup' — superlative, could be table or scalar",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    {
        "id": "solar_panel_15",
        "bucket": "truly_ambiguous",
        "description": "'Total projected revenue loss for all plants' — implicit but no trigger word",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    {
        "id": "hulushows_3",
        "bucket": "truly_ambiguous",
        "description": "'Top rating among entries with barely any visuals' — could be scalar or table",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    {
        "id": "museum_artifact_19",
        "bucket": "truly_ambiguous",
        "description": "'Show me showcase suitability for artifacts from an important dynasty' — entirely ambiguous",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    {
        "id": "labor_certification_applications_2",
        "bucket": "truly_ambiguous",
        "description": "'Success rate for H-1B visas' — could easily be a per-category table",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
    {
        "id": "solar_panel_17",
        "bucket": "truly_ambiguous",
        "description": "'Average price tag on a single repair' — implicit but no formal trigger",
        "expected_output_type": "scalar",
        "expect_answered": None,
    },
]


# ---------------------------------------------------------------------------
# The test
# Runs BOTH question variants per case and prints a side-by-side comparison.
# ---------------------------------------------------------------------------

def _fmt_result(result: dict) -> str:
    parts = []
    parts.append("✓ answered" if result["answered"] else "✗ not answered")
    if result["scalar"]:
        parts.append("→ scalar")
    if result["table"]:
        parts.append("→ table")
    if result["refusal"]:
        parts.append("(refused/out-of-scope)")
    if result["extra_info"]:
        parts.append(f"+ extra info ({result['word_count']}w)")
    return "  ".join(parts)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_output_type_user_response(case):
    """
    Ask BOTH output-type question variants to a user simulator and compare.

    For each case we run:
      - specific: "single summary value or row-by-row breakdown?"
      - vague   : "what output shape or format are you expecting?"

    Hard assertions only fire when expect_answered is True (unambiguous cases).
    Ambiguous cases are observe-only — results are printed for analysis.
    """
    llm = get_llm_client()
    instance_id = case["id"]
    d = _INSTANCES[instance_id]

    responses = {
        label: _simulate_user(instance_id, llm, q)
        for label, q in OUTPUT_QUESTIONS.items()
    }
    results = {label: _classify(r) for label, r in responses.items()}

    # ── Print for inspection ───────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"[{case['bucket'].upper()}] {instance_id}")
    print(f"Description : {case['description']}")
    print(f"User query  : {d['amb_user_query'][:120]}")
    print(f"GT output   : {d.get('output_type', 'N/A')}")

    for label, q in OUTPUT_QUESTIONS.items():
        resp = responses[label]
        res  = results[label]
        print(f"\n── [{label.upper()}] Q: \"{q}\"")
        print(f"   Response  : {resp[:300]}")
        print(f"   Result    : {_fmt_result(res)}")

    # ── Hard assertions for unambiguously clear cases (specific question only) ──
    specific = results["specific"]
    if case.get("expect_answered") is True:
        assert specific["answered"], (
            f"[{instance_id}] Specific question: user should have answered "
            f"(bucket='{case['bucket']}') but response was:\n{responses['specific']}"
        )
        if case["expected_output_type"] == "scalar":
            assert specific["scalar"] and not specific["table"], (
                f"[{instance_id}] Specific question: expected scalar answer but got "
                f"scalar={specific['scalar']}, table={specific['table']}\n"
                f"Response: {responses['specific']}"
            )
