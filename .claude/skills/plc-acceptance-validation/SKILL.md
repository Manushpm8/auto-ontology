---
name: plc-acceptance-validation
description: Validate that each acceptance criterion (AC-*) in a PLC feature brief is satisfied by the delivered implementation, tests, and evidence in this repo. Use when the user asks to "validate acceptance criteria", "check acceptance coverage", "did we build the right thing", "AC validation", "acceptance gate", or asks whether the feature meets the criteria captured by `plc-requirements-authoring`. This is a validation skill — it reads the brief, the test plan, the codebase, and the evidence log, and produces an advisory report marking every AC as PASS, FAIL, UNVERIFIED, MISSING, or N/A. It does not modify the brief, invent tests, or run code.
---

# PLC Acceptance Criteria Validation

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-acceptance-validation","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-acceptance-validation"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-acceptance-validation","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-acceptance-validation"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Validate that every acceptance criterion (`AC-*`) recorded in a feature brief is satisfied by what actually exists in the repo: implementation files, automated or manual test cases, and verification evidence. Produce a structured markdown report with one section per AC, a summary table, and findings ranked by severity.

This skill is the right-side V-model counterpart to `plc-requirements-authoring`: requirements authoring defines what the feature must do; this skill checks whether the delivered work meets those definitions.

**This skill is advisory.** Findings improve quality and surface gaps early but do NOT satisfy PLC requirements, replace formal acceptance review, or replace deterministic release validation (qualified scanners, evidence generation, policy enforcement via LaunchAPI, TAVA, nSpect, ScanSpect).

---

## Where This Sits in the V-Model

```
plc-requirements-authoring   →  defines AC-* in the feature brief
plc-design-authoring          →  maps AC-* to design elements (Traceability table)
plc-test-plan-authoring       →  maps AC-* to TC-* in the test plan
implementation + verification →  code, tests, evidence in plc-evidence.jsonl
plc-acceptance-validation    ←  THIS SKILL: checks each AC-* is actually satisfied
```

The skill is read-only over the brief, the test plan, the code, and the evidence log. It does not edit the brief, the test plan, or any test. It writes a single report file.

---

## Prerequisites

Required inputs:

- A feature brief produced by `plc-requirements-authoring` (default path: `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`). The brief must contain an `## Acceptance Criteria` table with `AC-*` rows.
- Read access to the repo working tree (this skill reads code and tests; it does not run them).

Optional inputs (consumed when present, never required):

- A test plan produced by `plc-test-plan-authoring` at `agentic_plc/work_products/<feature-name>/test-plan.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-test-plan.md`. When present, the AC Coverage table and per-layer test cases are the primary source of TC-* ↔ AC-* mappings.
- An evidence log at `agentic_plc/work_products/<feature-name>/evidence.jsonl` in workspace mode, `plc-evidence.jsonl` at the repo root, or `.plc/briefs/<feature-name>-evidence.jsonl`. Used to find recorded verification results that reference AC-*, FR-*, NFR-*, or TC-* identifiers.
- A workspace manifest at `agentic_plc/workspace.json` when one SRD/SADD maps to multiple source repos. Workspace evidence rows include `workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, and repo-relative `path` when applicable.
- A canonical SRD/STP/TAVA reference if the brief points at one — used as a hint, not consumed as authoritative.

If the feature brief is missing or contains no `AC-*` rows, do not draft a report. Ask the user where the brief is, or recommend running `plc-requirements-authoring` first.

---

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

---

## Status Vocabulary

Every acceptance criterion must be assigned **exactly one** of the following statuses. Definitions are the contract — apply them consistently:

| Status | Meaning |
|---|---|
| **PASS** | A specific test, inspection, analysis, demonstration, or manual signoff has been located that exercises this AC, and its outcome is recorded as satisfied (test passes, evidence row says PASS, manual signoff present). The pointer to the evidence must be cited in the report. |
| **FAIL** | A specific test or evidence row exists for this AC, and its outcome is recorded as not satisfied (test fails, evidence row says FAIL, signoff explicitly withheld). |
| **UNVERIFIED** | A test case or design element claims to verify this AC, but no execution result, evidence row, or signoff has been located. Treat any AC with a TC-* pointer but no executed evidence as UNVERIFIED — **never** PASS. |
| **MISSING** | No test case, evidence row, or signoff was located for this AC at all. The AC is not connected to any verification target in this repo. |
| **N/A** | The AC was explicitly marked Not Applicable in the feature brief (e.g., `Verification method: N/A` with a recorded reason), or it is a documentation-only criterion that an upstream gate already covers. Cite the brief's recorded reason. |

A critical rule: **for an AC with no test pointing at it, status is UNVERIFIED at best, never PASS.** Even if the implementation "obviously works", an AC without a named verification pointer is not validated by this skill.

---

## Workflow

Track this checklist during the run:

```markdown
Acceptance Validation Progress:
- [ ] Step 1: Locate inputs (brief, optional test plan, optional evidence log)
- [ ] Step 2: Parse acceptance criteria from the brief
- [ ] Step 3: Build the AC → verification-pointer map
- [ ] Step 4: Resolve each pointer against the repo (code, tests, evidence)
- [ ] Step 5: Assign a status per AC and write a finding
- [ ] Step 6: Generate the report
```

### Step 1 — Locate Inputs

1. Resolve the feature brief path. Default: `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`. If the user provided a path, use it.
2. Look for a test plan adjacent to the brief: `test-plan.md` in workspace mode or `<feature-name>-test-plan.md` in single-repo mode. Record whether it is present.
3. Look for an evidence log in the following order, stopping at the first one found:
   - Explicit path passed by the caller (orchestrators like `plc-v-model-validate` resolve the path once for the run and pass it through; user-supplied paths from a direct invocation behave the same way).
   - `agentic_plc/work_products/<feature-name>/evidence.jsonl` when `agentic_plc/workspace.json` exists.
   - `.plc/briefs/<feature-name>-evidence.jsonl`
   - `plc-evidence.jsonl` at the repo root

   This order is intentionally identical to `plc-traceability-matrix` Step 1 and to the writer's default in `plc-evidence-ingestion` so all three skills read and write the same log when called on the same feature.
4. If the brief is missing or the `## Acceptance Criteria` section is empty, stop and ask the user. Do not invent ACs.

Read the brief end-to-end before proceeding. Identify the feature slug from the brief metadata (filename or the `Feature/task request` row) using the same slug rules as `plc-requirements-authoring`: lowercase ASCII letters and digits, replacing runs of non-alphanumeric characters with one hyphen, trimming leading/trailing hyphens.

### Step 2 — Parse Acceptance Criteria

Extract every row from the `## Acceptance Criteria` table. For each AC capture:

- `AC ID` (e.g., `AC-1`).
- Linked `Requirement ID` (FR-* or NFR-*).
- The criterion text (the observable pass/fail condition).
- The declared verification method (automated test, manual test, inspection, analysis, demonstration, or N/A).
- The verification idea (the "how" recorded in the brief).
- Any explicit N/A reason recorded in the brief.

If the brief uses a team template whose AC columns are named differently, match by content (criterion text, verification method); do not require exact column names.

Also extract:

- The `## Proposed Functional Requirements` and `## Proposed Non-Functional Requirements` tables, so a missing AC for a security or functional requirement can be classified at the right severity.
- The `## Traceability` table when present, since it carries AC → design → code → test/evidence pointers that this skill consumes verbatim.

### Step 3 — Build the AC → Verification-Pointer Map

For each AC, collect every pointer that may verify it. Look in the following sources, in order:

1. **Test plan AC Coverage table** (highest signal): the resolved test plan → `## Acceptance Criterion Coverage`. Each AC row lists the TC-* IDs that verify it. Follow each TC-* into the test plan's per-layer Test Cases tables to capture: `Method`, `Test artifact` (file path + function/describe name for automated, role + procedure for manual), `Environment`, and `Notes`.
2. **Test plan Layer tables**: any test case whose `Verifies` field cites the AC ID directly, even if the AC Coverage table does not list it. Both directions matter — flag a mismatch in `## Findings` (see Step 6).
3. **Feature brief Traceability table**: rows whose `Test/evidence` column names a test path, evidence reference, manual signoff, or external system (Jenkins URL, ScanSpect/nSpect ID, TestRail case).
4. **Codebase test files**: grep the repo's `tests/`, `__tests__/`, `spec/`, `e2e/`, or language-idiomatic test locations for the AC ID (`AC-1`, `AC_1`) or the linked FR/NFR ID in test names, docstrings, or comments. Match conventions discovered during `plc-test-plan-authoring` (Step 3 of that skill mapped them).
5. **Evidence log** (`plc-evidence.jsonl` or `<feature>-evidence.jsonl`): rows written by `plc-evidence-ingestion` use the schema in [plc-evidence-ingestion/SKILL.md](../plc-evidence-ingestion/SKILL.md) → *Row schema*. Match rows whose `ref` is this AC, whose `ac_id` is this AC (when `ref` names the parent Req), or whose `tc_id` is a TC-* that the test plan maps to this AC. In workspace mode, preserve the row's `workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, and `path` in the pointer citation so reviewers know which repo state the AC evidence applies to.

For each pointer, record:

- Pointer kind (test plan TC row, test file path + function, evidence-log line number, manual signoff reference, external system URL).
- Source location citation (e.g., `tests/auth/test_login.py::test_locks_after_five_failures`, `plc-evidence.jsonl:42`, `<feature>-test-plan.md AC Coverage row AC-3`).
- Workspace attribution when present (`workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, and repo-relative path).
- Recorded outcome if any (test result, the evidence-log row's `status` or `decision` field, signoff status). Recorded outcome may be absent — that is the difference between PASS and UNVERIFIED in Step 5.

**Do not invent pointers.** If you cannot find a pointer in any of the five sources above, leave the AC with an empty pointer set; Step 5 will classify it MISSING.

### Step 4 — Resolve Each Pointer Against the Repo

For each pointer collected in Step 3, validate that it actually resolves:

- **Test file pointers**: verify the file exists at the recorded path. If the file exists, verify a function/describe block with the recorded name is present (case-sensitive match against the test file's contents). If the file or function is missing, downgrade the pointer to "claimed but unresolved" and mention this in the AC's notes.
- **Evidence-log pointers**: verify the evidence log row exists at the cited line and that its `ref` (or `ac_id` when `ref` names the parent Req, or `tc_id` mapped to this AC via the test plan) references this AC. Record the row's outcome verbatim — `status` for run-style checks (`pass` / `green` / `clean` / `fail` / `findings` / `critical` / ...), `decision` for manual-signoff rows (`accepted` / `rejected` / `pending`). If the row has neither field set, treat it as a claim without an outcome — that is UNVERIFIED territory in Step 5.
- **External system pointers** (Jenkins URLs, ScanSpect/nSpect IDs, TestRail cases): cite the reference verbatim from the brief or test plan. Do not attempt to fetch the external system. Mark the pointer as "external — outcome not retrieved by this skill" so Step 5 can decide whether the AC is UNVERIFIED or PASS based on whether the brief recorded a signoff.
- **Manual signoff pointers**: the brief or evidence log must record both a reviewer identity (role or person) and an outcome ("PASS" / "FAIL" / "signed off on <date>"). A free-form note that says "QA reviewed" without an outcome is a claim, not a signoff — record this and let Step 5 classify it UNVERIFIED.

Do not run tests. Do not write to any file the user has not asked you to write to (the only writes this skill produces are the report and the `Test plan path` / `Acceptance validation path` metadata pointer in the brief — and the brief metadata write only happens if the user opts in during Step 6).

### Step 5 — Assign a Status and Write a Finding

For each AC, apply the status rubric using only what Step 4 resolved:

- **PASS** — at least one pointer resolves to a test, evidence row, or signoff that explicitly records a satisfied outcome (test passes, evidence row `status` ∈ {`pass`, `green`, `clean`} or `decision: accepted`, signoff explicitly recorded). Cite the pointer that earned the PASS. If multiple pointers exist with mixed outcomes, classify as FAIL and explain in the notes; never quietly use the best pointer.
- **FAIL** — at least one pointer resolves to an explicitly unsatisfied outcome (test fails, evidence row `status` ∈ {`fail`, `red`, `findings`, `critical`, `error`, `blocked`} or `decision: rejected`, signoff explicitly withheld). Cite the failing pointer.
- **UNVERIFIED** — pointers exist but none have a recorded outcome (test files exist but nothing in evidence; TC-* listed but no executed evidence row; external system reference without a recorded signoff). Cite the pointers and state what is missing to upgrade to PASS.
- **MISSING** — no pointer resolves at all. The AC is disconnected from any verification target in this repo.
- **N/A** — the brief explicitly marked the AC as N/A in `plc-requirements-authoring` output. Cite the brief's recorded reason.

Then assign severity per the rubric in *Severity Rubric* below. Severity drives the order of findings in the final report.

### Step 6 — Generate the Report

Write the report to `agentic_plc/work_products/<feature-name>/acceptance-validation.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-acceptance-validation.md`, using the structure in [report-template.md](assets/report-template.md). Required content:

- A summary table counting PASS / FAIL / UNVERIFIED / MISSING / N/A by severity.
- One section per AC with: AC ID, criterion text, linked requirement ID, status, severity, evidence pointers (each with its source citation), and notes.
- A `## Findings` block sorted by severity (Critical → Warning → Info) listing every non-PASS, non-N/A AC with the action needed to resolve it.
- A `## Scope and Caveats` section listing: which test plan was consulted (path or "none"), which evidence log was consulted (path or "none"), any pointers that resolved as "external — outcome not retrieved", and any team-template mapping notes.
- The advisory disclaimer (see *Advisory Disclaimer* below) verbatim at the bottom.

After writing, present a short summary to the user (totals, top Critical findings, the report path). Offer to update the feature brief metadata table with an `Acceptance validation path` pointer to the report. Do not edit the brief without consent.

---

## Severity Rubric

Apply consistently. Severity reflects the impact of the AC, not the strength of the verification gap.

| Severity | Use for |
|---|---|
| **Critical** | A Functional or Security AC with status FAIL or MISSING. A FAIL or MISSING AC on a requirement that touches data integrity, authentication, authorization, secret handling, privilege boundaries, input validation, or release-gate sign-off lines. |
| **Warning** | An NFR AC (performance, reliability, usability, compatibility) with status FAIL, UNVERIFIED, or MISSING. A Functional AC with status UNVERIFIED. An AC with a TC-* pointer that does not resolve (test file or function moved/renamed). |
| **Info** | A nice-to-have AC with status UNVERIFIED or MISSING (e.g., a developer-experience criterion, an observability nicety). A PASS AC where the evidence pointer is "external — outcome not retrieved" and the brief already records a signoff. |

If you are unsure whether an AC is Functional or Security, look at the linked requirement type in the brief. Security ACs almost always link to a requirement whose `Category` is `security` or that touches auth/crypto/secrets/data access.

---

## Important Constraints

- **Cite everything.** Every PASS, FAIL, UNVERIFIED, and N/A entry must point to a specific file path, test name, evidence-log line, or signoff reference. Vague phrases like "tests look good" or "implementation appears complete" are not acceptable findings — replace them with the concrete pointer or downgrade to UNVERIFIED.
- **For an AC with no test pointing at it, status is UNVERIFIED, never PASS.** This is the contract. Even if a reviewer "knows" the AC is satisfied, this skill only validates what is cited in the repo.
- **Do not modify the brief, the test plan, or any test file.** This skill is read-only over those artifacts. The only write this skill produces by default is the acceptance-validation report at the canonical output path for the active mode plus the `aplc record` completion event when available. If the user opts in, also add a single `Acceptance validation path` row to the brief metadata table — nothing else.
- **Do not run tests or external systems.** Outcome retrieval is out of scope. If a pointer references an external system without a recorded signoff in the repo, the AC is UNVERIFIED until evidence ingestion lands.
- **Respect the advisory wording contract.** See `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*. Reserved phrases ("release-equivalent", "would block release", "PLC-approved", "OSRB-clear") must not appear in the generated report unless `~/.agentic-plc/scanner-approvals.jsonl` has an approval matching that wording. Default to advisory framing.
- **Do not invent pointers, tests, or ACs.** If the brief or test plan does not record a pointer and the codebase scan finds nothing, the correct status is MISSING. Do not synthesize a "probably tested in `tests/foo.py`" pointer.
- **Format agnostic on inputs.** Accept any readable brief format. When the brief uses a team template whose section names differ from the bundled `feature_brief.md`, match by content and intent — do not refuse to validate just because the headings differ. Record any mapping you had to do in the report's *Scope and Caveats* section.

---

## Advisory Disclaimer

Every generated report must end with this disclaimer verbatim:

```markdown
## Disclaimer

This report is **advisory only**. It validates that each acceptance criterion has
a cited verification pointer in this repo and records the pointer's resolved
outcome. It does NOT satisfy PLC requirements, replace formal acceptance review,
or replace deterministic release validation (qualified scanners, evidence
generation, policy enforcement via LaunchAPI, TAVA, nSpect, ScanSpect, SonarQube,
Coverity, BlackDuck, OSRB). A PASS here is "current for workflow reuse" — not
release-gate clearance. Your work must still go through the governed PLC release
process.
```

Do not soften, abridge, or relabel this disclaimer. Stronger wording requires explicit, recorded owner approval per `plc-security-scan/SKILL.md` → *Owner approval record*.

---

## Bundled CLI — Compact and Full Modes

This skill ships a stdlib-only CLI at [scripts/plc_acceptance_validate.py](scripts/plc_acceptance_validate.py). It works for any PLC feature or bug brief that contains an `## Acceptance Criteria` table.

The shared small-output mode is called **compact**. Use `--mode compact` when an orchestrator, CI job, or external pipeline needs a quick machine-readable verdict.

Use `--mode full` when automation should write the full advisory report shape: status counts, one section per AC, findings, scope/caveats, and the required disclaimer. The full CLI mode is still artifact-driven and stdlib-only; it reads the brief and evidence log. The agent-driven workflow above remains available when a human-readable investigation needs code/test pointer discovery beyond those inputs.

Invoke:

```bash
python3 source/plc-acceptance-validation/scripts/plc_acceptance_validate.py \
    --feature-brief .plc/briefs/<feature>-brief.md \
    --evidence-log  .plc/briefs/<feature>-evidence.jsonl \
    --scope-fr FR-024 \
    --mode compact \
    --output stdout \
    --format json
```

Output shape (JSON):

```json
{
  "mode": "compact",
  "verdict": "PASS | FAIL | UNVERIFIED | MIXED | EMPTY",
  "scope_fr": "FR-024 or null",
  "feature_brief": "<relative-path>",
  "evidence_log": "<relative-path>",
  "ac_results": [
    {"ac_id": "AC-1", "req_id": "FR-024", "status": "PASS", "evidence": "external-test/gtest/2026-05-22", "notes": ""}
  ]
}
```

Status vocabulary in `ac_results[].status` matches *Status Vocabulary* above exactly. Overall `verdict` aggregates:

- **FAIL** if any AC is FAIL.
- **PASS** if every AC is PASS or N/A.
- **UNVERIFIED** if any AC is UNVERIFIED or MISSING and none are FAIL.
- **EMPTY** if no ACs are in scope (e.g., `--scope-fr` matched no rows).

Evidence rows are matched per AC by either `row.ref == ac_id` or `row.ac_id == ac_id`. Rows with `producer: external` are surfaced in the evidence summary with their `producer_id` annotated.

**Per-AC classification semantics — test-shaped rows first, then scan-shaped (compact mode only).**

Each AC's matching evidence rows are split into two pools:

- **Test-shaped** checks: `external-test`, `jenkins-build`, `perf-report`, `accuracy-report`, `manual-signoff`. These are the *primary* evidence for AC acceptance — they match the AC's typical `Verification method` (automated test / manual test / signoff).
- **Scan-shaped** checks: `scanspect-run`, `nspect-result`, `other`. These corroborate but are *secondary*; e.g., a security scan returning `clean` is useful context but it's not what proves an AC's behavior.

Classification rule:

1. Among test-shaped rows (sorted most-recent-first), take the most recent definitive outcome:
   - pass / accepted → **PASS**, evidence cites that test row.
   - fail / rejected → **FAIL**, evidence cites that test row.
2. If no test-shaped row is definitive, fall back to scan-shaped rows under the same rule.
3. Otherwise → **UNVERIFIED**.

This rule has two practical consequences worth knowing:

- **A test FAIL beats a later scan PASS.** If the bug-fix pytest run failed and Semgrep later ran clean, the AC classifies as FAIL with the pytest row cited — not PASS with Semgrep cited. Test outcomes are authoritative for acceptance.
- **A test PASS keeps its citation even when scans run later.** The bug-fix orchestrator typically writes pytest pass rows in Phase 4, then security-scan rows in Phase 5. Without this rule the verdict would still be PASS but would cite the most recent scan; with this rule the verdict cites the pytest run that actually proves the AC.

This differs from `--mode full`, which uses conservative "any FAIL ever recorded means FAIL" semantics for audit. Compact mode's "test-shaped first, most-recent-wins" rule is intentional: orchestrators ask "did the AC pass?" — a bug-fix flow naturally writes a failing pre-fix row, then a passing post-fix row, then security-scan rows, and this rule gives the right verdict (PASS) with the right citation (the post-fix pytest row). Full mode keeps the conservative rule because it surfaces every historical failure for human review.

The CLI uses **stdlib only** — no third-party deps — same constraint as `plc_evidence_ingest.py`. It is installed alongside this skill via `install.sh` (the `SKILLS` array copies the whole directory recursively).

---

## Additional Resources

- Report output template: [report-template.md](assets/report-template.md)
- Bundled CLI: [scripts/plc_acceptance_validate.py](scripts/plc_acceptance_validate.py)
- Upstream contract: `plc-requirements-authoring/assets/feature_brief.md` (AC table schema)
- Upstream contract: `plc-test-plan-authoring/assets/test-plan-template.md` (AC Coverage table, per-layer Test Cases tables, TC-* IDs)
- Advisory wording contract: `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` when all applicable ACs are verified, `WARN` when any AC is FAIL, UNVERIFIED, or MISSING, `SKIP` with `skip_reason` when required inputs are unavailable, and `ERROR` with `error_reason` for unexpected failures. Include the report path and AC status counts in `summary`.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"validation","check":"plc-acceptance-validation","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-acceptance-validation","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
