---
name: plc-traceability-matrix
description: Generate the Requirement ↔ Design ↔ Code ↔ Test ↔ Evidence traceability matrix for a feature by reading the feature brief, the test plan, the codebase, and the evidence log — no interviews, no invented links. Use when the user asks to "build a traceability matrix", "show requirement-to-code-to-test coverage", "generate the trace matrix", "audit traceability", "where is FR-N implemented", or asks how each requirement traces through to evidence. The skill writes a single matrix table grouped by the 13 PLC requirement types, plus a Gap section listing requirements missing Design / Code / Test / Evidence pointers. Compute-and-write, not interview-driven.
---

# PLC Traceability Matrix

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-traceability-matrix","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-traceability-matrix"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-traceability-matrix","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-traceability-matrix"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Generate the canonical Requirement ↔ Design ↔ Code ↔ Test ↔ Evidence matrix for a feature. The matrix is derived entirely from artifacts already on disk: the feature brief (requirements + design), the test plan when present, the source tree, and `plc-evidence.jsonl`. The skill does not interview the developer and does not invent any linkage.

The output is one file at `agentic_plc/work_products/<feature-name>/traceability.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-traceability.md`, containing:

- A single matrix table with one row per requirement (`FR-*` and `NFR-*` from the brief), nine columns: Req ID, Req text, Repo, Revision, Design ref, Code (`path:line`), Test ID, Last verified, Status.
- A `## Gaps` section listing every requirement whose row resolved to `PARTIAL` or `GAP`, with the missing column(s) named.
- A `## Coverage Summary` grouping rows by the 13 PLC requirement types (Functional, System (Non-Functional), Interface, Safety, KPI, Platform, Security, Legal and Standards, Telemetry, Backward Compatibility, Virtualization, Automatability, Other Non-Functional) and counting Covered / Partial / Gap per type.
- A `## Sources Used` block listing every artifact path consulted.
- The advisory disclaimer verbatim.

**This skill is advisory.** The matrix is a working artifact that surfaces traceability gaps early; it does NOT satisfy PLC requirements, replace formal audit-trail attestation, or replace deterministic release validation (qualified scanners, evidence generation, policy enforcement via LaunchAPI, TAVA, nSpect, ScanSpect, BlackDuck, OSRB).

---

## Where This Sits in the V-Model

```
plc-requirements-authoring   →  feature brief: FR-*, NFR-*, AC-*
plc-design-authoring          →  feature brief: design delta + thin Traceability table
plc-test-plan-authoring       →  test plan: TC-* with Verifies (FR/NFR/AC IDs)
implementation + verification →  code, tests, plc-evidence.jsonl rows
plc-acceptance-validation    →  per-AC status (PASS/FAIL/UNVERIFIED/MISSING/N/A)
plc-traceability-matrix     ←   THIS SKILL: full Req → Code → Test → Evidence audit table
```

The thin `## Traceability` table inside the feature brief (one row per requirement, six columns in workspace-aware output: Requirement / Repo / Revision / Design element / Code/files / Test/evidence) is the minimum traceability surface produced by `plc-design-authoring` during authoring. This skill extends that surface into a full audit-quality matrix — using the same row identity (one row per `FR-*` / `NFR-*`) so the brief's table and this output stay reconcilable.

---

## Status Vocabulary

Every row carries exactly one status:

| Status | Meaning |
|---|---|
| **COVERED** | All five derived columns (Design ref, Code path:line, Test ID, Last verified) are populated AND the most recent verification evidence references the current branch HEAD (or a commit reachable from HEAD via `git merge-base --is-ancestor`). |
| **PARTIAL** | At least one of Design ref / Code / Test ID is populated, but at least one is empty, OR the most recent verification evidence is for a commit that is NOT an ancestor of HEAD (stale). |
| **GAP** | The Code column is empty, regardless of Test or Design presence. A requirement without an implementation pointer is a gap by definition. |

The rules apply in this order — `GAP` always wins over `PARTIAL` when Code is empty. Test presence does not promote `GAP` to `COVERED`; a test that references a requirement nobody implemented is still a gap.

---

## Prerequisites

Required:

- A feature brief at `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md` (or a user-provided path), containing at minimum the `## Proposed Functional Requirements` and `## Proposed Non-Functional Requirements` tables. If neither table exists, stop and ask the user where the canonical brief is.
- Read access to the repo working tree. The skill calls `git`, `grep`, and reads files; it does not write to anything but the matrix output.

Optional (consumed when present, never required):

- Test plan at `agentic_plc/work_products/<feature-name>/test-plan.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-test-plan.md`, produced by `plc-test-plan-authoring`. Provides the `FR-*` / `NFR-*` / `AC-*` → `TC-*` mapping from the *Requirement Coverage* and *Acceptance Criterion Coverage* tables, and the per-TC `Test artifact` field that names test file paths.
- Evidence log at `agentic_plc/work_products/<feature-name>/evidence.jsonl` in workspace mode, `plc-evidence.jsonl` (repo root), or `.plc/briefs/<feature-name>-evidence.jsonl`. Provides verification timestamps and commit refs per check.
- The brief's existing `## Traceability` table (when `plc-design-authoring` has filled it). Used as a hint, not as ground truth — every cell in the existing table is re-verified against the codebase before being copied into the matrix.
- Workspace manifest at `agentic_plc/workspace.json`. Provides `workspace_id`, repo IDs, repo URLs, and relative repo paths when one SRD/SADD maps to multiple source repos. Each `repos[].path` must be relative and must resolve under the workspace root. In workspace mode, freshness for `repo_id` / `repo_url` evidence is checked against the owning child repo when that repo is accessible.
- Traversal plan at `agentic_plc/work_products/<feature-name>/traversal-plan.json` in workspace mode. This is generated during workspace preflight and consumed as the fixed repo/phase snapshot unless an explicit refresh trigger occurs.
- Traversal summary at `agentic_plc/work_products/<feature-name>/traversal-summary.md` and `.json` in workspace mode. This reviewer-facing audit artifact lists every manifest repo, every attempted inspect/execute phase, each outcome, and reasons for WARN/SKIP/FAIL/ERROR states.

---

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

---

## Workflow

Track this checklist during the run:

```markdown
Traceability Matrix Progress:
- [ ] Step 1: Locate inputs and resolve feature slug
- [ ] Step 2: Parse requirements from the brief
- [ ] Step 3: Derive Design ref per requirement
- [ ] Step 4: Derive Code (path:line) per requirement
- [ ] Step 5: Derive Test ID per requirement
- [ ] Step 6: Derive Last verified per requirement
- [ ] Step 7: Assign status and detect gaps
- [ ] Step 8: Group by requirement type, write the matrix
```

### Step 1 — Locate Inputs and Resolve Feature Slug

1. Resolve the feature brief path. Default: `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`. If the user provided a path, use it.
2. Derive `<feature-name>` from the brief filename or the brief's `Feature/task request` metadata row using the slug rules from `plc-requirements-authoring`: lowercase ASCII letters and digits, runs of non-alphanumeric characters collapsed to a single hyphen, leading/trailing hyphens trimmed.
3. Look for the optional inputs:
   - Test plan adjacent to the brief, named `test-plan.md` in workspace mode or `<feature-name>-test-plan.md` in single-repo mode.
   - Evidence log: if the caller (e.g. `plc-v-model-validate`) passed an explicit path, use it. Otherwise look for `agentic_plc/work_products/<feature-name>/evidence.jsonl` in workspace mode, then `.plc/briefs/<feature-name>-evidence.jsonl`, then `plc-evidence.jsonl` at the repo root. The first one found wins; record its path. This order is intentionally identical to `plc-acceptance-validation` Step 1 and to the writer's default in `plc-evidence-ingestion` so all three skills read and write the same log when called on the same feature.
   - Workspace manifest: look for `agentic_plc/workspace.json` at the current workspace root or repo root. When present, parse its `workspace_id`, `work_products_root`, and `repos[]` entries. Validate every `repos[].path`; stop with a manifest validation error if a path is absolute, uses `..`, uses `\` separators, or resolves outside the workspace root.
   - Traversal artifacts: in workspace mode, ensure the traversal plan and traversal summary exist beside the feature brief. The plan uses `workspace-traversal-plan/v1`; the summary uses `workspace-traversal-summary/v1`.
4. Capture the current HEAD via `git rev-parse HEAD` once at the start. Cache it as `head_sha`; the freshness check in Step 6 reuses this single value.

Record every consulted path for the *Sources Used* block. If the brief is missing or its requirement tables are empty, do not write a matrix — stop and ask.

### Step 2 — Parse Requirements

Read the brief and extract:

- Every row from `## Proposed Functional Requirements` — each yields an `FR-*` ID and the requirement text.
- Every row from `## Proposed Non-Functional Requirements` — each yields an `NFR-*` ID, requirement text, and `Category` (used to map into the 13 PLC requirement types in Step 8).
- The `## Acceptance Criteria` table, captured as a side index `{AC ID → linked Req ID}` so AC-keyed evidence rows can be folded back to their parent requirement.
- The existing `## Traceability` table when present — capture every row as `{requirement_id → {repo, revision, design_element, code_files, test_evidence}}` so Steps 3–6 can use the existing entries as starting hints (still re-verified against the codebase).

If the brief uses a team template with renamed columns, match by intent (ID column, text column, type/category column). Record any column mapping in the *Sources Used* block.

### Step 3 — Derive Design Ref

For each requirement:

1. If the brief's `## Traceability` row for this requirement has a `Design element` value, use it directly. Then verify the named element appears in the brief's `## Proposed Design Delta` (Affected components / Affected files / Design summary fields). If it does, the cell is filled.
2. If the brief's traceability row is empty, scan the design delta sections for the requirement ID itself, the requirement text's salient nouns (e.g., "rate limiter", "retry budget"), or the linked acceptance criterion's verification idea. The first design section that names one of these is the design ref.
3. If nothing resolves, leave the Design ref column empty. Do not invent a design element.

### Step 4 — Derive Code (path:line)

For each requirement:

1. Compute a set of search anchors for this requirement: the requirement ID itself (e.g., `FR-3`), the canonical noun phrases in the requirement text (longest first), and any file paths already named in the brief's traceability row's `Code/files` column.
2. Use repo-grep for each anchor against the working tree, excluding test directories (`tests/`, `__tests__/`, `spec/`, `e2e/`, language-idiomatic test locations) and excluding the brief itself.
3. For the strongest match (longest exact noun-phrase or ID match), record the first hit as `relative/path/to/file.ext:line`. If multiple files match, prefer the file whose `git log -1 --format=%H -- <path>` is reachable from HEAD (skip files that exist only on a stale branch).
4. If the brief's traceability row already names files but the noun-phrase search returns nothing in those files, record the file's most recent commit-line for the requirement ID via `git blame -L` if the ID appears, or the first non-blank line of the named function/symbol if the brief's traceability row named one.
5. If no anchor matches anywhere in the codebase, leave the Code column empty.

Constraints:

- Cite **one line**, not a range. The reviewer follows the line into the source; widening to a range loses signal.
- Cite **the file as it exists at HEAD**, not at a stale ref. If the file was renamed, follow the rename (`git log --follow`) and cite the current path.
- **Never invent a line number.** If grep returns a file but you can't pin a meaningful line, record only the path (e.g., `src/auth.py`) and downgrade the row to `PARTIAL` in Step 7.

### Step 5 — Derive Test ID

For each requirement, look in this order:

1. **Test plan** (when present): the *Requirement Coverage* table maps each `FR-*` / `NFR-*` to a list of `TC-*` IDs that verify it. Record the comma-separated TC IDs. Then resolve each TC into its `Test artifact` cell from the per-layer Test Cases tables (e.g., `tests/auth/test_login.py::test_lockout`) so a reviewer can click straight through.
2. **Test plan AC mapping**: the *Acceptance Criterion Coverage* table maps each `AC-*` to TC IDs. Join through the brief's AC → Req index (built in Step 2) to fold AC-keyed TCs back to the parent Req when the *Requirement Coverage* row is empty.
3. **Codebase scan** (when no test plan exists): grep the repo's test directories for the requirement ID (`FR-3`, `FR_3`), the requirement's noun phrases, or the AC IDs that link to it. Cite the first matching test file as `tests/.../test_x.py::test_name`. If the test framework doesn't use a `::` separator, use the framework-idiomatic test reference (`tests/x_spec.rb test_name`, `pkg/x_test.go::TestFoo`).
4. If neither source surfaces a test, leave the Test ID column empty.

Do not invent TC IDs. The TC namespace is owned by `plc-test-plan-authoring`.

### Step 6 — Derive Last Verified

For each requirement, look in this order:

1. **Evidence log**: read every JSONL row. Each row is shaped per the schema in [plc-evidence-ingestion/SKILL.md](../plc-evidence-ingestion/SKILL.md) → *Row schema* — common fields are `pillar`, `check`, `tool`, `ref`, `branch`, `commit`, `timestamp`, and one of `status` / `decision`. Workspace rows also carry `workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, `path` when file-scoped, and optionally `finding_id`. (`plc-security-scan` writes the same envelope with `pillar: CODING`.) Group rows by their effective key in this order: (a) `ref` directly when it matches `FR-*` / `NFR-*`, (b) `ac_id` (or `ref` matching `AC-*`) resolved to the parent Req via the AC→Req index, (c) `tc_id` resolved via the test plan's `Verifies` field. For each Req, pick the **most recent** row by `timestamp` whose outcome is satisfied — `status` ∈ {`pass`, `green`, `clean`} or `decision: accepted`. Record `<commit_short_sha>  <YYYY-MM-DD>` in the Last verified column, and carry the row's `repo_id` / `repo_url` and `repo_revision` into the Repo and Revision columns when present.
2. **No matching evidence row**: leave the column empty.

Freshness check: when a row is found, run `git merge-base --is-ancestor <evidence_commit> HEAD`. If non-zero (the evidence commit is not in HEAD's history), append a trailing `(stale)` marker to the cell — and Step 7 will demote the row from `COVERED` to `PARTIAL`.

Reuse the parsing pattern from `plc-security-scan/scripts/plc_security_scan.py` → `read_evidence(path)`: JSONL parse with one record per non-empty line, silently skip malformed lines. Do not duplicate the file into a parallel format; treat the evidence log as the single source of timestamps and commit refs.

### Step 7 — Assign Status

For each row, apply the rules in order (first match wins):

1. Code column is empty → `GAP`.
2. Any of Design ref / Code / Test ID is empty → `PARTIAL`.
3. Last verified column has a `(stale)` marker → `PARTIAL`.
4. Last verified column is empty (no evidence row pointed at this Req) → `PARTIAL`.
5. All five derived columns populated and Last verified references HEAD or an ancestor → `COVERED`.

Then collect the `PARTIAL` and `GAP` rows for the *Gaps* section. For each entry record: requirement ID, status, the named missing/stale column(s), and a one-line suggested next step (run the test, add an evidence row, file an implementation task, follow-up the design author).

### Step 8 — Group by Requirement Type, Write the Matrix

Group the rows for the *Coverage Summary* section by the 13 PLC requirement types from `plc-requirements-validation/assets/srd-sections.md` *Area 10*:

| # | Type |
|---|---|
| 1 | Functional |
| 2 | System (Non-Functional) |
| 3 | Interface |
| 4 | Safety |
| 5 | KPI |
| 6 | Platform |
| 7 | Security |
| 8 | Legal and Standards |
| 9 | Telemetry |
| 10 | Backward Compatibility |
| 11 | Virtualization |
| 12 | Automatability |
| 13 | Other Non-Functional |

Use the brief's NFR `Category` column to assign each `NFR-*` to its type. Every `FR-*` goes under `Functional`. A requirement that doesn't fit any named type goes under `Other Non-Functional`. Do not invent additional categories.

Write the output to the canonical traceability path for the active mode using [matrix-template.md](assets/matrix-template.md). The matrix table itself is **one flat table** (one row per requirement, sorted by ID `FR-1, FR-2, …, NFR-1, NFR-2, …`); the type grouping appears as the *Coverage Summary* table below the matrix, not as repeating sub-tables — reviewers consuming the matrix as CSV need one continuous table.

After writing, present a short stdout summary: total rows, counts per status, top 3 gaps. Offer to add a `Traceability matrix path` pointer row to the brief's metadata table — do not edit the brief without the user's consent.

---

## Important Constraints

- **Compute, don't interview.** This skill never asks the developer questions unless an input is missing or genuinely ambiguous (e.g., two briefs in `.plc/briefs/` and the slug is unclear). Every row is derived from artifacts.
- **Never invent linkages.** If the brief doesn't say `FR-3` is implemented in `src/auth.py`, the skill must find evidence — either an existing traceability row, a grep hit for `FR-3` or the requirement's noun phrases, or a TC pointer that the test plan attached. No best-guess attribution.
- **Code references are `path:line`.** A path-only cell is allowed but demotes the row to `PARTIAL`. A range is not allowed.
- **Workspace attribution is explicit.** In multi-repo workspace mode, every row with a repo pointer must include Repo (`repo_id` or `repo_url`), Revision (`repo_revision` or commit SHA), Code (`path` or `path:line` when applicable), and the Req/Finding identity (`ref` or `finding_id` in evidence rows).
- **A requirement with no code is a `GAP`, not a `COVERED` even if tests exist.** Tests against a missing implementation are themselves a finding, not a satisfaction signal.
- **Evidence inputs are read-only.** This skill does not modify evidence rows that it reads for traceability. It may append its own `aplc record` completion event when available; the matrix logic must not consume that completion event as verification evidence.
- **Respect the advisory wording contract.** See `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*. Reserved phrases ("release-equivalent", "would block release", "PLC-approved", "audit-clean", "OSRB-clear") must NOT appear in the generated matrix unless `~/.agentic-plc/scanner-approvals.jsonl` records approval for that exact wording. Default to advisory framing.
- **Do not modify the brief, the test plan, existing evidence rows, or any source file.** The only write this skill produces by default is the matrix file plus the `aplc record` completion event when available. If the user opts in, also add a single `Traceability matrix path` metadata row to the brief — nothing else.
- **Format agnostic on inputs.** Accept any readable brief format. Record any team-template column mapping in *Sources Used*.

---

## Advisory Disclaimer

Every generated matrix must end with this disclaimer verbatim:

```markdown
## Disclaimer

This matrix is **advisory only**. It surfaces traceability gaps between
requirements, design, code, tests, and recorded evidence so reviewers can
spot missing or stale links early. It does NOT satisfy PLC requirements,
replace formal audit-trail attestation, or replace deterministic release
validation (qualified scanners, evidence generation, policy enforcement
via LaunchAPI, TAVA, nSpect, ScanSpect, SonarQube, Coverity, BlackDuck,
OSRB). A `COVERED` row is "current for workflow reuse" — not release-gate
clearance. Your work must still go through the governed PLC release
process.
```

Do not soften, abridge, or relabel this disclaimer. Stronger wording requires explicit, recorded owner approval per `plc-security-scan/SKILL.md` → *Owner approval record*.

---

## Bundled CLI — Compact and Full Modes

This skill ships a stdlib-only CLI at [scripts/plc_traceability_matrix.py](scripts/plc_traceability_matrix.py). It works for any PLC feature or bug brief with FR/NFR tables and an optional `## Traceability` table.

The shared small-output mode is called **compact**. Use `--mode compact` when an orchestrator, CI job, or external pipeline needs a quick flat Req -> Design -> Code -> Test -> Evidence table.

Use `--mode full` when automation should write the full advisory report shape: the matrix table, Gaps section, Coverage Summary, Sources Used block, and required disclaimer. The CLI relies on the brief's existing `## Traceability` table for Design / Code / Test pointers (it does not grep the codebase) and joins the evidence log for the *Last verified* column.

Invoke:

```bash
python3 source/plc-traceability-matrix/scripts/plc_traceability_matrix.py \
    --feature-brief .plc/briefs/<feature>-brief.md \
    --evidence-log  .plc/briefs/<feature>-evidence.jsonl \
    --scope-fr FR-024 \
    --mode compact \
    --output stdout \
    --format markdown
```

Compact output (markdown): a single flat table with columns Req ID, Req text, Design ref, Code, Test ID, Last verified, Status. Status assignment matches *Status Vocabulary* above exactly (COVERED / PARTIAL / GAP). Stale evidence (commit not an ancestor of HEAD) is annotated `(stale)` and demotes COVERED to PARTIAL. Rows with `producer: external` carry `(external: <producer_id>)` next to the Last verified value so reviewers can see the provenance inline.

Compact output (json): same data as a structured object with `scope_fr`, `feature_brief`, `evidence_log`, and `rows` (each row a dict with the matrix columns plus requirement category for full-report grouping).

Full output adds `gaps`, `coverage_summary`, `sources_used`, and the advisory disclaimer in Markdown mode.

The CLI uses **stdlib only** — no third-party deps — same constraint as `plc_evidence_ingest.py`. It is installed alongside this skill via `install.sh` (the `SKILLS` array copies the whole directory recursively).

---

## Additional Resources

- Output template: [matrix-template.md](assets/matrix-template.md)
- Bundled CLI: [scripts/plc_traceability_matrix.py](scripts/plc_traceability_matrix.py)
- Upstream contracts:
  - `plc-requirements-authoring/assets/feature_brief.md` — FR-*, NFR-*, AC-* tables
  - `plc-design-authoring/assets/design-sections.md` — thin Traceability table this matrix extends
  - `plc-test-plan-authoring/assets/test-plan-template.md` — Requirement / AC Coverage tables, TC-* IDs, Test artifact field
  - `plc-requirements-validation/assets/srd-sections.md` — 13 PLC requirement types (Area 10)
  - `plc-security-scan/scripts/plc_security_scan.py` → `read_evidence(path)` — JSONL parsing pattern
- Advisory wording contract: `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` when there are no GAP rows, `WARN` when PARTIAL or GAP rows remain, `SKIP` with `skip_reason` when required inputs are unavailable, and `ERROR` with `error_reason` for unexpected failures. Include the matrix path and row-status counts in `summary`.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"validation","check":"plc-traceability-matrix","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-traceability-matrix","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
