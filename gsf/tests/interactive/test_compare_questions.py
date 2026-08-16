"""
Side-by-side comparison of vague vs open output-type question.

Part 1 — 14 scalar instances (same as previous test), open question added.
  We already have vague results; this adds open so both can be compared.

Part 2 — 8 table instances (not skipped by v3 triggers), both vague and open.

Questions:
  vague = "What output shape or format are you expecting for this result?"
  open  = "What kind of output are you looking for?"

Uses official usersim-guard STEP1 + STEP2 pipeline with real ambiguity data.

Run with:
    uv run pytest gsf/tests/interactive/test_compare_questions.py -s -v
"""

import json, os, re, sys
from pathlib import Path

# GSF_DIR is computed relative to this file so it works from any checkout of
# this repo. USERSIM_DIR / FULL_DATA / DB_ROOT point into the separate
# BIRD-Interact benchmark checkout (not part of this repo); override via env
# vars if that checkout lives elsewhere, otherwise the values below (this
# machine's layout) are used as the default.
GSF_DIR     = Path(__file__).resolve().parents[3]
USERSIM_DIR = Path(os.environ.get(
    "BIRD_INTERACT_USERSIM_DIR", "/Users/ariellegeva/Desktop/BIRD-Interact/usersim-guard"
))
FULL_DATA   = Path(os.environ.get(
    "BIRD_INTERACT_FULL_DATA",
    "/Users/ariellegeva/Desktop/BIRD-Interact/bird-interact-full/bird_interact_data_with_gt.jsonl",
))
DB_ROOT     = Path(os.environ.get(
    "BIRD_INTERACT_FULL_DB_ROOT", "/Users/ariellegeva/Desktop/BIRD-Interact/bird-interact-full"
))

sys.path.insert(0, str(USERSIM_DIR))
sys.path.insert(0, str(GSF_DIR))

from user_simulator.prompt_generator import (
    generate_step1_prompt,
    generate_step2_prompt,
    extract_action_from_response,
    load_db_schema,
)
from gsf.utils.llm_invoke import get_llm_client

instances = {json.loads(l)["instance_id"]: json.loads(l) for l in open(FULL_DATA)}

QUESTIONS = {
    "vague": "What output shape or format are you expecting for this result?",
    "open":  "What kind of output are you looking for?",
}

# ── Part 1: same 14 scalar instances as previous test ────────────────────────
SCALAR_CASES = [
    ("truly_ambiguous", "solar_panel_2"),
    ("truly_ambiguous", "museum_artifact_19"),
    ("truly_ambiguous", "museum_artifact_20"),
    ("truly_ambiguous", "cold_chain_pharma_compliance_2"),
    ("truly_ambiguous", "reverse_logistics_14"),
    ("truly_ambiguous", "exchange_traded_funds_16"),
    ("truly_ambiguous", "sports_events_15"),
    ("table_leaning",   "hulushows_14"),
    ("table_leaning",   "crypto_exchange_11"),
    ("table_leaning",   "sports_events_11"),
    ("table_leaning",   "exchange_traded_funds_19"),
    ("table_leaning",   "planets_data_5"),
    ("table_leaning",   "fake_account_20"),
    ("table_leaning",   "households_19"),
]

# ── Part 2: 8 table instances not skipped by v3 ───────────────────────────────
TABLE_CASES = [
    ("table", "solar_panel_10"),
    ("table", "hulushows_10"),
    ("table", "cybermarket_pattern_4"),
    ("table", "hulushows_19"),
    ("table", "cybermarket_pattern_6"),
    ("table", "hulushows_18"),
    ("table", "cybermarket_pattern_3"),
    ("table", "hulushows_13"),
]

# ── Classifiers ───────────────────────────────────────────────────────────────
def classify_action(raw: str) -> str:
    m = re.search(r"<s>(.*?)</s>", raw, re.DOTALL | re.IGNORECASE)
    text = m.group(1).strip() if m else raw
    for label, pat in [("unanswerable", r"unanswerable\(\)"),
                        ("unlabeled",    r"unlabeled\("),
                        ("labeled",      r"labeled\(")]:
        if re.search(pat, text, re.IGNORECASE) or re.search(pat, raw, re.IGNORECASE):
            return label
    return "unknown"

SCALAR_RE = re.compile(
    r"\b(single|one \w*\s?(number|value|row|figure|result)|scalar|just (the )?one|"
    r"summary (value|number)|overall (number|total|average|value)|"
    r"one (overall|summary|total|average|combined)|just a number|a single|"
    r"aggregate|aggregated|one number|a number|one figure|one score|one value|"
    r"one result|one total|a total|a count|one count|just the (number|count|total|"
    r"name|id|value|score|figure|percentage|percent|ratio))\b",
    re.IGNORECASE,
)
TABLE_RE = re.compile(
    r"\b(breakdown|broken.?down|row.?by.?row|per (item|row|entry)|"
    r"list (of|all)|each (item|row|entry)|multiple rows|tabular|"
    r"for each|all of them|all entries|all records|several rows|"
    r"multiple (entries|results|rows|records)|a table|table of)\b",
    re.IGNORECASE,
)

def classify_response(text: str) -> dict:
    scalar  = bool(SCALAR_RE.search(text))
    table   = bool(TABLE_RE.search(text))
    refused = bool(re.search(
        r"(out of scope|cannot answer|can.t answer|i don.t know|"
        r"not sure|sorry.*not|unable to answer)", text, re.IGNORECASE))
    words   = len(text.split())
    # extra info: mentions something beyond shape (units, rounding, filters, nulls)
    extra   = bool(re.search(
        r"\b(round|decimal|percent|unit|second|dollar|currency|null|zero|"
        r"filter|specific(ally)?|meaning|i\.?e\.?|e\.?g\.|such as|because|"
        r"in particular|clarif|what i mean|namely|that is)\b",
        text, re.IGNORECASE)) or words > 25
    return dict(scalar=scalar, table=table, refused=refused,
                answered=(scalar or table) and not refused,
                extra_info=extra, words=words)

def run_instance(d: dict, question: str, llm) -> dict:
    """Run STEP1 + STEP2 for one instance and question. Returns result dict."""
    try:
        step1_raw = llm.invoke(generate_step1_prompt(d, question)).content
    except Exception as e:
        return {"action": "error", "response": f"STEP1 error: {e}", "cls": {}}

    action = classify_action(step1_raw)

    if action == "unanswerable":
        return {"action": action, "response": "(out of scope)", "cls":
                dict(scalar=False, table=False, refused=True, answered=False,
                     extra_info=False, words=0)}

    try:
        action_str = extract_action_from_response(step1_raw)
        db_schema  = load_db_schema(d["selected_database"], DB_ROOT)
        step2_raw  = llm.invoke(
            generate_step2_prompt(d, question, action_str, db_schema)
        ).content
    except Exception as e:
        return {"action": action, "response": f"STEP2 error: {e}", "cls": {}}

    m = re.search(r"<s>(.*?)</s>", step2_raw, re.DOTALL)
    response_text = m.group(1).strip() if m else step2_raw.strip()
    return {"action": action, "response": response_text,
            "cls": classify_response(response_text)}


def _icon(cls: dict) -> str:
    parts = ["✓" if cls.get("answered") else "✗"]
    if cls.get("scalar"):    parts.append("scalar")
    if cls.get("table"):     parts.append("table")
    if cls.get("refused"):   parts.append("OOS")
    if cls.get("extra_info"): parts.append(f"+extra({cls.get('words')}w)")
    return " ".join(parts)


def test_compare_output_questions():
    llm = get_llm_client()

    # ── PART 1: scalar instances, open question only ───────────────────────────
    print(f"\n{'='*80}")
    print("PART 1 — Scalar instances: open question")
    print(f"{'='*80}")
    print(f"{'':3s} {'Instance':35s}  {'Group':15s}  "
          f"{'Action':11s}  {'Result':30s}  Response")
    print("-" * 120)

    p1_results = []
    for group, iid in SCALAR_CASES:
        d = instances[iid]
        r = run_instance(d, QUESTIONS["open"], llm)
        p1_results.append({"group": group, "id": iid, "gt": d.get("output_type"), **r})
        print(f"[{group[:2].upper()}] {iid:35s}  {group:15s}  "
              f"{r['action']:11s}  {_icon(r['cls']):30s}  "
              f"{r['response'][:70]}")

    # ── PART 2: table instances, both questions ────────────────────────────────
    print(f"\n{'='*80}")
    print("PART 2 — Table instances: vague vs open side-by-side")
    print(f"{'='*80}")

    p2_results = []
    for group, iid in TABLE_CASES:
        d   = instances[iid]
        rv  = run_instance(d, QUESTIONS["vague"], llm)
        ro  = run_instance(d, QUESTIONS["open"],  llm)
        p2_results.append({"id": iid, "gt": d.get("output_type"),
                            "vague": rv, "open": ro})

        print(f"\n[{iid}]  GT={d.get('output_type')}")
        print(f"  Q: {d['amb_user_query'][:100]}")
        print(f"  VAGUE ({rv['action']:11s}) {_icon(rv['cls'])}")
        print(f"    → {rv['response'][:120]}")
        print(f"  OPEN  ({ro['action']:11s}) {_icon(ro['cls'])}")
        print(f"    → {ro['response'][:120]}")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print("SUMMARY — Part 1 (open, scalar instances)")
    print(f"{'='*80}")
    for r in p1_results:
        c = r["cls"] or {}
        print(f"  [{r['group'][:2].upper()}] {r['id']:35s}  "
              f"action={r['action']:11s}  {_icon(c)}")

    p1_answered  = sum(1 for r in p1_results if r["cls"].get("answered"))
    p1_oos       = sum(1 for r in p1_results if r["action"] == "unanswerable")
    p1_extra     = sum(1 for r in p1_results if r["cls"].get("extra_info"))
    print(f"\n  answered={p1_answered}/14  OOS={p1_oos}  extra_info={p1_extra}")

    print(f"\n{'='*80}")
    print("SUMMARY — Part 2 (table instances, vague vs open)")
    print(f"{'='*80}")
    print(f"  {'Instance':35s}  {'VAGUE':35s}  {'OPEN':35s}")
    print("-" * 110)
    for r in p2_results:
        v = _icon(r["vague"]["cls"]) + f" [{r['vague']['action'][:3]}]"
        o = _icon(r["open"]["cls"])  + f" [{r['open']['action'][:3]}]"
        print(f"  {r['id']:35s}  {v:35s}  {o}")
