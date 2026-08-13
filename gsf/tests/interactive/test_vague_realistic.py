"""
Realistic user-simulator test for the output-type clarification question.

Uses the official BIRD-Interact usersim-guard STEP1 + STEP2 pipeline:
  - STEP1: classifier decides labeled / unlabeled / unanswerable
  - STEP2: generates the actual response based on that action

One question variant only:
  vague = "What output shape or format are you expecting for this result?"

Early-abort: if the first 3 instances consecutively return unanswerable, stop.

14 instances:
  Group A (7) — truly ambiguous: zero linguistic scalar/table signal
  Group B (7) — table-leaning language but scalar GT
"""

import json, re, sys
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
USERSIM_DIR = Path("/Users/ariellegeva/Desktop/BIRD-Interact/usersim-guard")
GSF_DIR     = Path("/Users/ariellegeva/Desktop/GSF_and_Ontology/GSF")
FULL_DATA   = Path("/Users/ariellegeva/Desktop/BIRD-Interact/bird-interact-full/bird_interact_data_with_gt.jsonl")
DB_ROOT     = Path("/Users/ariellegeva/Desktop/BIRD-Interact/bird-interact-full")

sys.path.insert(0, str(USERSIM_DIR))
sys.path.insert(0, str(GSF_DIR))

from user_simulator.prompt_generator import (
    generate_step1_prompt,
    generate_step2_prompt,
    extract_action_from_response,
    load_db_schema,
)
from gsf.utils.llm_invoke import get_llm_client

# ── Data ──────────────────────────────────────────────────────────────────────
instances = {json.loads(l)["instance_id"]: json.loads(l) for l in open(FULL_DATA)}

VAGUE_Q = "What output shape or format are you expecting for this result?"

CASES = [
    # Group A — truly ambiguous (no linguistic scalar/table signal)
    ("truly_ambiguous", "solar_panel_2"),
    ("truly_ambiguous", "museum_artifact_19"),
    ("truly_ambiguous", "museum_artifact_20"),
    ("truly_ambiguous", "cold_chain_pharma_compliance_2"),
    ("truly_ambiguous", "reverse_logistics_14"),
    ("truly_ambiguous", "exchange_traded_funds_16"),
    ("truly_ambiguous", "sports_events_15"),
    # Group B — table-leaning language, scalar GT
    ("table_leaning",   "hulushows_14"),
    ("table_leaning",   "crypto_exchange_11"),
    ("table_leaning",   "sports_events_11"),
    ("table_leaning",   "exchange_traded_funds_19"),
    ("table_leaning",   "planets_data_5"),
    ("table_leaning",   "fake_account_20"),
    ("table_leaning",   "households_19"),
]

# ── Classifiers ───────────────────────────────────────────────────────────────
UNANSWERABLE_RE = re.compile(r"unanswerable\(\)", re.IGNORECASE)
UNLABELED_RE    = re.compile(r"unlabeled\(", re.IGNORECASE)
LABELED_RE      = re.compile(r"labeled\(", re.IGNORECASE)

SCALAR_RE = re.compile(
    r"\b(single|one \w*\s?(number|value|row|figure|result)|scalar|just (the )?one|"
    r"summary (value|number)|overall (number|total|average|value)|"
    r"one (overall|summary|total|average|combined)|just a number|a single|"
    r"aggregate|aggregated|one number|a number|one figure|one score|one value)\b",
    re.IGNORECASE,
)
TABLE_RE = re.compile(
    r"\b(breakdown|broken.?down|row.?by.?row|per (item|row|entry)|"
    r"list (of|all)|each (item|row|entry)|multiple rows|table|tabular|"
    r"for each|all of them|all entries|all records|several rows)\b",
    re.IGNORECASE,
)
EXTRA_INFO_RE = re.compile(
    r"\b(because|specifically|meaning|i\.?e\.?|e\.?g\.?|such as|for example|"
    r"in particular|actually|clarif|more precisely|what i mean)\b",
    re.IGNORECASE,
)

def classify_action(step1_raw: str) -> str:
    """Extract action type from STEP1 response."""
    # Look inside <s>...</s> tags first
    m = re.search(r"<s>(.*?)</s>", step1_raw, re.DOTALL | re.IGNORECASE)
    text = m.group(1).strip() if m else step1_raw
    if UNANSWERABLE_RE.search(text): return "unanswerable"
    if UNLABELED_RE.search(text):    return "unlabeled"
    if LABELED_RE.search(text):      return "labeled"
    # Fallback: check full raw text
    if UNANSWERABLE_RE.search(step1_raw): return "unanswerable"
    if UNLABELED_RE.search(step1_raw):    return "unlabeled"
    if LABELED_RE.search(step1_raw):      return "labeled"
    return "unknown"

def classify_response(text: str) -> dict:
    scalar  = bool(SCALAR_RE.search(text))
    table   = bool(TABLE_RE.search(text))
    refused = bool(re.search(r"(out of scope|cannot answer|can.t answer|i don.t know|"
                              r"not sure|sorry.*not|unable to answer)", text, re.IGNORECASE))
    extra   = bool(EXTRA_INFO_RE.search(text)) or len(text.split()) > 30
    return dict(scalar=scalar, table=table, refused=refused,
                answered=(scalar or table) and not refused,
                extra_info=extra, words=len(text.split()))

def fmt(action: str, cls: dict) -> str:
    parts = [f"action={action}"]
    parts.append("✓ answered" if cls["answered"] else "✗ not answered")
    if cls["scalar"]:  parts.append("→ scalar")
    if cls["table"]:   parts.append("→ table")
    if cls["refused"]: parts.append("(refused/OOS)")
    if cls["extra_info"]: parts.append(f"+extra({cls['words']}w)")
    return "  ".join(parts)

def test_vague_output_question():
    llm = get_llm_client()
    results = []
    consecutive_oos = 0

    print(f"\nQuestion: \"{VAGUE_Q}\"\n")
    print("=" * 78)

    for group, iid in CASES:
        d       = instances[iid]
        db_name = d["selected_database"]
        schema  = load_db_schema(db_name, DB_ROOT)

        print(f"\n[{group.upper()}]  {iid}")
        print(f"Q: {d['amb_user_query'][:120]}")
        print(f"GT output_type: {d.get('output_type', '?')}")

        # ── STEP 1: classify action ───────────────────────────────────────────
        step1_prompt = generate_step1_prompt(d, VAGUE_Q)
        try:
            step1_raw = llm.invoke(step1_prompt).content
        except Exception as e:
            print(f"  [STEP1 ERROR] {e} — skipping")
            results.append({"group": group, "id": iid, "action": "error",
                             "response": None, "cls": None})
            continue
        action = classify_action(step1_raw)

        print(f"\n  STEP1 action → {action}")
        m = re.search(r"<s>(.*?)</s>", step1_raw, re.DOTALL)
        if m:
            print(f"  STEP1 raw   → {m.group(1).strip()[:120]}")

        # ── Early abort: first 3 consecutive unanswerable ─────────────────────
        if action == "unanswerable":
            consecutive_oos += 1
            print(f"  [OOS #{consecutive_oos}]")
            results.append({"group": group, "id": iid, "action": action,
                             "response": None, "cls": None})
            if consecutive_oos >= 3 and len(results) <= 3:
                print(f"\n⚠️  First 3 instances all unanswerable — aborting early.")
                break
            continue
        else:
            consecutive_oos = 0

        # ── STEP 2: generate response ─────────────────────────────────────────
        action_str   = extract_action_from_response(step1_raw)
        step2_prompt = generate_step2_prompt(d, VAGUE_Q, action_str, schema)
        try:
            step2_raw = llm.invoke(step2_prompt).content
        except Exception as e:
            print(f"  [STEP2 ERROR] {e} — skipping")
            results.append({"group": group, "id": iid, "action": action,
                             "response": None, "cls": None})
            continue

        m2 = re.search(r"<s>(.*?)</s>", step2_raw, re.DOTALL)
        response_text = m2.group(1).strip() if m2 else step2_raw.strip()

        cls = classify_response(response_text)
        print(f"\n  STEP2 response → {response_text[:300]}")
        print(f"  Result         → {fmt(action, cls)}")

        results.append({"group": group, "id": iid, "action": action,
                         "response": response_text, "cls": cls})

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n\n{'=' * 78}")
    print("SUMMARY")
    print(f"{'Group+ID':40s}  {'Action':12s}  {'Answered':8s}  {'Scalar':6s}  {'Table':5s}  {'Extra':5s}")
    print("-" * 78)
    for r in results:
        c = r["cls"] or {}
        print(
            f"[{r['group'][:2].upper()}] {r['id']:37s}  "
            f"{r['action']:12s}  "
            f"{'✓' if c.get('answered') else '✗':8s}  "
            f"{'S' if c.get('scalar') else '-':6s}  "
            f"{'T' if c.get('table') else '-':5s}  "
            f"{'E' if c.get('extra_info') else '-':5s}"
        )

    answered  = [r for r in results if r["cls"] and r["cls"].get("answered")]
    unans     = [r for r in results if r["action"] == "unanswerable"]
    unlabeled = [r for r in results if r["action"] == "unlabeled"]
    labeled   = [r for r in results if r["action"] == "labeled"]
    extra     = [r for r in results if r["cls"] and r["cls"].get("extra_info")]

    print(f"\nRan {len(results)}/14 instances")
    print(f"  unlabeled  (will answer): {len(unlabeled)}")
    print(f"  labeled    (will answer): {len(labeled)}")
    print(f"  unanswerable (OOS):       {len(unans)}")
    print(f"  answered with content:    {len(answered)}")
    print(f"  revealed extra info:      {len(extra)}")
