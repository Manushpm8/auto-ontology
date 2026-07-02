---
name: plc-v-model-validate
description: Orchestrate PLC-L1 V-model validation workflows for existing requirements, design, implementation changes, verification, security scan handoff, acceptance criteria coverage, and the Requirement ↔ Design ↔ Code ↔ Test ↔ Evidence traceability matrix. Use when Codex needs to validate SRD/SDD/SADD artifacts, check requirements-design-code traceability, run advisory implementation verification, surface existing plc-evidence-ingestion rows in the verification report, classify each acceptance criterion as PASS/FAIL/UNVERIFIED/MISSING, emit a traceability matrix for an existing feature, coordinate plc-requirements-validation, plc-design-validation, plc-security-scan, plc-acceptance-validation, and plc-traceability-matrix, or produce an advisory PLC validation summary for repository changes.
---

# PLC V-Model Validate

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-v-model-validate","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-v-model-validate"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-v-model-validate","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-v-model-validate"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill as the PLC-L1 V-model validation orchestration layer. It sequences the validation-oriented PLC skills and lightweight bridge checks for existing artifacts or implementation changes.

For feature authoring from a new implementation request, use `plc-v-model` instead.

## Hard Rules

- Treat all output as advisory developer feedback. Do not claim PLC compliance or release-gate satisfaction.
- Do not replace nSpect, Pulse, ScanSpect, SonarQube, Coverity, BlackDuck, Anchore, OSRB, LaunchAPI, TAVA, or authoritative PLC gates.
- Do not mutate canonical SRD, SDD, SADD, or STP artifacts unless the user explicitly requests edits and provides the target artifact.
- Do not invent test coverage or verification commands. Run only a provided or clearly repo-provided safe local command.
- Do not print secrets. Do not write secrets or raw credentials anywhere.
- Work with partial input. Record missing artifacts as `SKIP` or `WARN`; do not stop unless the repo path is missing or inaccessible.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Inputs

Required: repo root, either from the current working directory or an explicit local path.

Optional: change summary, SRD path/URL, SDD or SADD path/URL, STP path/URL, PR or branch context, test command, output directory.

Use `SDD` and `SADD` as aliases for the design artifact. Preserve the user's terminology in reports.

## Workspace Mode

If `agentic_plc/workspace.json` exists at the parent workspace root or repo root, treat feature work products as workspace-scoped:

- Feature brief: `agentic_plc/work_products/<feature-name>/brief.md`
- Test plan: `agentic_plc/work_products/<feature-name>/test-plan.md`
- Evidence log: `agentic_plc/work_products/<feature-name>/evidence.jsonl`
- Traceability matrix: `agentic_plc/work_products/<feature-name>/traceability.md`
- Traversal plan: `agentic_plc/work_products/<feature-name>/traversal-plan.json`
- Traversal summary: `agentic_plc/work_products/<feature-name>/traversal-summary.md` and `agentic_plc/work_products/<feature-name>/traversal-summary.json`

If no workspace manifest exists, keep the legacy single-repo defaults under `.plc/briefs/` and repo-root `plc-evidence.jsonl`.

Workspace mode validates every `repos[].path` as a relative, non-escaping path under the workspace root before using the manifest. `agentic_plc/workspace.json` is authoritative for workspace repo membership and paths; `.plc/profile.json` is advisory brownfield metadata only.

Validation preflight consumes the persisted `traversal-plan.json` snapshot generated during `workspace_preflight`. Major phases are `workspace_preflight`, `requirements_authoring`, `design_authoring`, `test_plan_authoring`, `implementation`, `verification`, `security_scan`, `acceptance_validation`, `traceability_matrix`, and `final_traversal_summary`. Refresh the plan only when manifest content, repo availability, repo revision, selected scope, feature references, or brownfield profile metadata changed; record the refresh reason and increment the plan version. Do not re-enter the same repo/phase/action under the same plan version.

Validation preflight must inspect all accessible repos listed in the manifest when the feature, SADD/SDD, test plan, or user request makes them relevant. Repo-bound validation commands run only on relevant or explicitly selected repos, and every skipped, missing, inaccessible, warned, or failed repo/phase outcome must be surfaced in the traversal summary artifact.

## Status Values

- `PASS`: phase ran and no material findings were reported.
- `WARN`: phase ran and reported findings or advisory issues.
- `SKIP`: phase could not run because input, tool, authentication, or infrastructure was unavailable.
- `ERROR`: phase was attempted but failed unexpectedly.
- `N/A`: phase does not apply to the change.

## Freshness and Resume

Re-running this skill must not redo requirements or design validation that is already current, and a long phase that stalls must be resumable without restarting from scratch. The mechanism is intentionally simple and phase-level.

### State file

Persist run state to `.plc/v-model-state.json` in the active child repo (create `.plc/` if it does not exist). The file is shared with `plc-v-model`; each skill owns its own top-level key and must not write into the other's block. Write atomically: write `.plc/v-model-state.json.tmp` then `rename` to `.plc/v-model-state.json`. Store work-product paths in state using the canonical paths for the active mode.

Top-level schema:

```json
{
  "schema_version": "2",
  "plc-v-model-validate": {
    "identity": {
      "srd_path": "<path or null>",
      "design_path": "<path or null>",
      "stp_path": "<path or null>",
      "comparison_base": "<base ref or unknown>"
    },
    "last_run_commit": "<sha at last write, or unknown>",
    "last_run_timestamp": "<ISO-8601 UTC at last write>",
    "phases": {
      "requirements_validation": "<phase record, see below>",
      "design_validation": "<phase record>",
      "code_implementation_check": "<phase record>",
      "verification": "<phase record>",
      "security_scan": "<phase record>",
      "acceptance_validation": "<phase record>",
      "traceability_matrix": "<phase record>"
    }
  },
  "plc-v-model": "<owned by plc-v-model; this skill must not write here>"
}
```

Phase record shape:

```json
{
  "status": "complete | in_progress | not_started | stale | skipped",
  "freshness": "current | current_with_caveat | stale | in_progress | not_started",
  "run_decision": "reuse | rerun | skip | null",
  "decision_reason": "<short reason or null>",
  "commit": "<sha at last completion or in_progress write, or null>",
  "timestamp": "<ISO-8601 UTC or null>",
  "last_substep": "<bounded substep name or null>",
  "inputs_covered": "<phase-specific object of consumed inputs — see Phase input schema>",
  "produced_artifacts": "<phase-specific object of generated paths with digests — see Phase input schema>",
  "dirty_files_at_completion": ["<normalized path from the git diff + ls-files union — see Freshness rule>", "..."],
  "notes": "<optional one-line note>"
}
```

`status` is the lifecycle field maintained by the phase step itself. `freshness` and `run_decision` are written by the freshness preflight (Step 1) — they record what the most recent preflight classified and what the developer chose for this invocation.

`inputs_covered` and `produced_artifacts` are **disjoint**. `inputs_covered` lists what the phase consumed (SRD, SDD/SADD, STP, comparison base, changed-file set, test command, scanner versions). `produced_artifacts` lists what the phase generated (the validation report, the verification log, the evidence file). A generated artifact must never appear in `inputs_covered`; that would let a phase's own untracked output mark itself stale.

### Bounded substep names

Phase resume is bounded — each phase has a fixed, short list of substeps. The state file records the last substep that completed, and on resume the phase picks up at the next substep. Do not invent substeps outside this list.

| Phase | Substeps (in order) |
|---|---|
| `requirements_validation` | `srd_loaded`, `subskill_ran`, `report_written` |
| `design_validation` | `design_loaded`, `subskill_ran`, `report_written` |
| `code_implementation_check` | `changed_files_collected`, `traceability_checked`, `report_written` |
| `verification` | `command_run`, `results_recorded` |
| `security_scan` | `scanners_run`, `evidence_written` |
| `acceptance_validation` | `brief_loaded`, `subskill_ran`, `report_written` |
| `traceability_matrix` | `brief_loaded`, `subskill_ran`, `report_written` |

### Phase input schema

`inputs_covered` and `produced_artifacts` are both phase-specific objects. Use exactly the shapes below — do not record unrelated fields. If a required input is unknown, set it to `null`; that forces the next preflight to classify the phase as **stale** (not `current_with_caveat`).

| Phase | `inputs_covered` (consumed) | `produced_artifacts` (generated) |
|---|---|---|
| `requirements_validation` | `{srd_path, design_path, comparison_base}` | `{report_path, report_digest}` |
| `design_validation` | `{design_path, srd_path, comparison_base, changed_files: [path], changed_files_content_digest}` | `{report_path, report_digest}` |
| `code_implementation_check` | `{srd_path, design_path, comparison_base, changed_files: [path]}` | `{report_path, report_digest}` |
| `verification` | `{test_command, changed_files: [path]}` | `{log_path, log_digest}` |
| `security_scan` | `{scan_scope: [path], scan_scope_content_digest, scanner_versions: {<scanner>: <version>}}` | `{evidence_path, evidence_digest}` |
| `acceptance_validation` | `{feature_brief_path, test_plan_path, evidence_log_path, comparison_base, changed_files: [path], changed_files_content_digest}` | `{report_path, report_digest}` |
| `traceability_matrix` | `{feature_brief_path, test_plan_path, evidence_log_path, comparison_base, changed_files: [path], changed_files_content_digest}` | `{report_path, report_digest}` |

`changed_files_content_digest` and `scan_scope_content_digest` are sha256 hashes over the **bytes of each file in the path list**, not just the file list itself — see *Content digests for path lists* below. They are paired with the corresponding path list so that the freshness rule can detect both (a) a change in which files are in scope, via the path-list comparison, and (b) a content edit at the same path, via the digest comparison.

`report_digest`, `log_digest`, `evidence_digest` are sha256 hashes of the produced file's bytes at completion time. Record them so a later edit (or absence) of the report or log is detected on the next preflight.

### Content digests for path lists

When a phase depends on the **contents** of a list of files (not just the path names), record both the path list and a content digest. The path list lets `commit_touched_inputs` and `dirty_touched_inputs` in the freshness rule compare real paths against the diff or worktree dirty set; the content digest lets `current_inputs_match` catch same-path content edits that the path-list checks alone would miss when (for example) a file was edited and a previous run's commit and the current commit are the same.

`changed_files_content_digest` and `scan_scope_content_digest` are computed as:

1. Sort the path list lexicographically.
2. For each path, if the file is missing or deleted at digest-computation time, mark the phase stale at preflight. Do not silently hash an empty list.
3. Build a stable serialization for each entry: `<path>\n<sha256 of file bytes in hex>\n`.
4. Concatenate the serializations.
5. Sha256 the concatenated bytes.

The content digest therefore changes whenever any in-scope file's bytes change, even if the path list itself is unchanged.

### Freshness rule

Classify each phase by running the checks below **in order** and returning the first classification that fires. The rule deliberately separates consumed inputs (`inputs_covered`) from generated outputs (`produced_artifacts`): worktree and commit-drift overlap checks only consider consumed input paths, never produced artifact paths.

Define for the current phase:

- `consumed_paths`: every file path appearing in `inputs_covered` (`srd_path`, `design_path`, `stp_path`, `changed_files`, etc.). Path-typed fields contribute the path; digest-typed and string-typed fields do not.
- `current_inputs_match`: for every field in `inputs_covered`, the current input value equals the stored value. Recompute digest fields (`changed_files_content_digest`, `scan_scope_content_digest`) from current content per *Content digests for path lists* and compare. Compare string fields (`comparison_base`, `test_command`) exactly. Compare path-typed fields (`srd_path`, `design_path`, `stp_path`) exactly. Compare path-list fields (`changed_files`, `scan_scope`) as sets. Compare `scanner_versions` per-key.
- `artifacts_valid`: every path in `produced_artifacts` exists on disk AND its current sha256 equals the stored digest (`report_digest`, `log_digest`, `evidence_digest`). For `requirements_validation` and `design_validation`, additionally require the report to contain at least one findings group section. For `security_scan`, additionally require the evidence file to parse as valid JSON or JSONL. For `acceptance_validation`, additionally require the report to contain the `## Summary` table and at least one `### AC-` section. For `traceability_matrix`, additionally require the report to contain the `## Matrix` heading and at least one `| FR-` or `| NFR-` table row.
- `dirty`: the union of three git commands, normalized to plain paths. Do not parse `git status --porcelain` output directly — its records carry status flags and rename arrows and are not safe to intersect with path sets. Instead use:
  - `git diff --name-only` (unstaged tracked changes)
  - `git diff --cached --name-only` (staged changes)
  - `git ls-files --others --exclude-standard` (untracked files honoring `.gitignore`)

  Take the union, deduplicate, and treat the result as a set of paths.
- `commit_touched_inputs`: when `state.phases[<phase>].commit != HEAD`, the set returned by `git diff --name-only state.phases[<phase>].commit..HEAD` intersected with `consumed_paths`.
- `dirty_touched_inputs`: `dirty` intersected with `consumed_paths`.

Decision table (first matching row wins):

| # | Condition | Classification |
|---|---|---|
| 1 | Identity mismatch (handled in Step 1 before this table; archives owned block) | n/a |
| 2 | `state.phases[<phase>].status` is not `complete` | return the stored status (`in_progress`, `not_started`, `skipped`) |
| 3 | `inputs_covered` has any `null` required field | `stale` |
| 4 | `artifacts_valid` is false | `stale` |
| 5 | `current_inputs_match` is false | `stale` |
| 6 | `state.phases[<phase>].commit != HEAD` AND `commit_touched_inputs` is non-empty | `stale` |
| 7 | `dirty_touched_inputs` is non-empty | `stale` |
| 8 | `state.phases[<phase>].commit == HEAD` AND `dirty` is empty | `current` |
| 9 | otherwise (commit drift outside consumed paths and/or dirty files outside consumed paths) | `current_with_caveat` |

The top-level `last_run_commit` field on the state file is metadata for the freshness summary (latest activity timestamp). It is not consulted by the decision table — only `state.phases[<phase>].commit` is.

`current_with_caveat` is presented to the developer with the list of out-of-scope changes and a reuse/rerun/skip prompt. Default to **reuse**; the developer can change it. `current` does not require a prompt — the freshness summary still reports the classification and the developer can override during the decision step.

### Identity binding

The `identity` block binds the owned top-level block to a specific validation input set. The preflight (Step 1) must verify identity before classifying any phase.

- `srd_path`, `design_path`, `stp_path` must equal the artifacts the developer requested for this run (or `null` on both sides when not provided).
- `comparison_base` must equal the comparison base resolved in Step 0.

The changed-file set is **not** part of identity. The phases that actually depend on changed files record them per-phase in `inputs_covered` — `design_validation` (which compares design against code) records both `changed_files` and `changed_files_content_digest`; `code_implementation_check` (which traces changed files to requirements/design) records `changed_files`. The freshness rule then decides per-phase whether a code edit invalidates that specific phase. `requirements_validation` is a document consistency check over the SRD and SDD/SADD and does not consume changed files, so it is unaffected by code-only edits. Treating the changed-file set as identity would archive the whole owned block whenever any code file changed, which would force requirements/design validation to rerun even when their source docs were untouched.

If any of these differ, the **owned block** (`state["plc-v-model-validate"]`) is not applicable to this run. Do not rename or rewrite the whole file — that would discard `state["plc-v-model"]`. Instead:

1. Append the current `state["plc-v-model-validate"]` value to `state["plc-v-model-validate"].archived_runs`, an array of prior snapshots. Each snapshot keeps its `identity`, `phases`, `last_run_commit`, and `last_run_timestamp`, plus an `archived_at` ISO-8601 UTC timestamp.
2. Replace `state["plc-v-model-validate"]` with a fresh block: new `identity` set to the current run, `phases` initialized to every phase `not_started`, and `archived_runs` carried over from the prior block (with the snapshot appended).
3. Leave `state["plc-v-model"]` untouched.
4. Persist atomically.
5. Tell the developer the prior `plc-v-model-validate` block belonged to a different input set and was archived in-place.

`archived_runs` may grow over many input-set switches. The skill does not auto-prune; the developer can trim it manually if disk concern arises.

### State reconstruction from existing artifacts

If `.plc/v-model-state.json` is missing but a prior `plc-v-model-validate-summary.md` exists, reconstruct minimal state by inspecting the summary's Phase Status table phase-by-phase, not as a whole. For each row:

- Mark the phase `complete` only if both the row's `Status` is `PASS` or `WARN` AND the `Report` path referenced by the row still exists on disk and is non-empty.
- Use the summary's `Commit` metadata as the phase's `commit`, set `produced_artifacts.report_path` (or `log_path` / `evidence_path` per phase) to the row's referenced path, and compute the current sha256 into the matching `*_digest` field. Leave `inputs_covered` digest fields and other unrecoverable values unknown (null) so the next preflight classifies the phase as stale unless the developer confirms.
- Phases whose row says `SKIP`, `ERROR`, or `N/A` are reconstructed as `not_started`.

Tell the developer the state file was reconstructed from the summary and which phases were reconstructed.

### Per-phase state writes

Every phase step in this skill must:

1. **Before invoking the subskill or running the command**: atomically write `status = "in_progress"`, `commit = <HEAD>`, `timestamp = <now>`, `last_substep = null`, and populate `inputs_covered` with the inputs the phase is about to consume.
2. **After each named substep completes** (from *Bounded substep names*): atomically update `last_substep` to that substep's name and bump `timestamp`.
3. **On successful phase completion**: set `status = "complete"`, `last_substep` to the final substep, `produced_artifacts` to the generated path(s) and their sha256 digest(s) (compute from the bytes on disk), and `dirty_files_at_completion` to the union of `git diff --name-only`, `git diff --cached --name-only`, and `git ls-files --others --exclude-standard` (normalized paths only, not raw porcelain lines).
4. **On expected skip** (subskill unavailable, required input absent, developer chose `skip` in preflight): set `status = "skipped"` with a non-null `decision_reason`.
5. **On unexpected interrupt or error**: leave `status = "in_progress"` with the last completed substep so the next run resumes at the next substep.

These writes are produced by phase steps and consumed by the next preflight. Skipping any write above means the next run cannot make a correct freshness or resume decision.

### Freshness is not formal PLC approval

A "current" phase status here means **the workflow can reuse the prior report without redoing the validation**. It is not formal PLC approval, release-gate clearance, nSpect/Pulse/SonarQube/Coverity/BlackDuck/OSRB/LaunchAPI/TAVA satisfaction, or team signoff. Phrase it consistently as "current for workflow reuse" or "fresh for this run" in summaries and reports.

## Workflow

### Step 0 - Establish Scope

1. Resolve repo root and output directory. Default outputs to the repo root or current working directory if the user selected another working directory.
2. Collect branch and commit when the repo is in git:
   - `git -C <repo> rev-parse --show-toplevel`
   - `git -C <repo> branch --show-current`
   - `git -C <repo> rev-parse HEAD`
3. Resolve the committed-change comparison base without fetching remote refs:
   - If the user provided a PR/MR target branch or explicit comparison base, use it.
   - Otherwise try local refs in this order: `main`, `origin/main`.
   - If no base ref exists locally, record `comparison_base: unknown` and reason `base_ref_not_available`.
4. If a comparison base exists, identify committed branch changes:
   - `git -C <repo> merge-base HEAD <base>`
   - `git -C <repo> diff --name-only <merge-base>...HEAD`
   Record `comparison_base`, `merge_base`, and committed changed files.
5. Always identify staged and unstaged files separately:
   - `git -C <repo> status --short`
   - `git -C <repo> diff --name-only`
   - `git -C <repo> diff --cached --name-only`
   The effective changed-file set is the union of committed branch changes, staged files, unstaged files, and any user-provided file list.
6. If the repo is not in git, inspect user-provided file lists or state `changed_files: unknown`.
7. Determine source docs and test command.

### Step 1 - Freshness Preflight

Before invoking any validation subskill or running tests, decide which phases must run and which can be reused. Do this every run.

1. **Locate state**. Look for `.plc/v-model-state.json` and any prior `plc-v-model-validate-summary.md`.
   - If neither exists, this is a fresh run. Initialize state with `plc-v-model-validate.identity` set to the current input set (SRD path, design path, STP path, comparison base — **not** the changed-file digest, which is a per-phase input) and every phase `not_started`. Skip to step 5.
   - If only the summary exists, follow *State reconstruction from existing artifacts* in *Freshness and Resume* (reconstruct per phase, not as a whole) and tell the developer.
   - If only the state file exists, proceed.
2. **Verify identity of the owned block**. Compare `state["plc-v-model-validate"].identity` to the current run's input set: `srd_path`, `design_path`, `stp_path`, and `comparison_base`.
   - If all match, continue.
   - If any differ, archive the owned block per *Identity binding*: append the current `state["plc-v-model-validate"]` snapshot to `state["plc-v-model-validate"].archived_runs`, replace `state["plc-v-model-validate"]` with a fresh block (new identity, every phase `not_started`, `archived_runs` carried forward), leave `state["plc-v-model"]` untouched, persist, and tell the developer the prior plc-v-model-validate block was archived in-place. Skip to step 5.
3. **Classify each phase using the decision table in *Freshness rule*.** Run the checks in order (status, inputs populated, artifacts valid + digests match, current-inputs-match, commit-drift overlap, dirty-worktree overlap, then current vs current_with_caveat). Write the resulting classification to `state.phases[<phase>].freshness`.
4. **Present the freshness summary to the developer**:

   ```text
   Freshness summary (workflow reuse, not PLC approval):
   - requirements_validation: <freshness> — last commit <sha>, report <path> (digest <match | mismatch>), dirty consumed inputs: <files or none>, out-of-scope dirty files: <files or none>
   - design_validation: ...
   - code_implementation_check: ...
   - verification: ...
   - security_scan: ...
   - acceptance_validation: ...
   - traceability_matrix: ...
   Identity: srd=<path>, design=<path>, stp=<path>, base=<ref>
   Stale because: <per-phase reason: "input X changed", "report missing", "commit-diff touches Y", "dirty file Z in consumed inputs", or "n/a">
   ```

5. **Collect decisions**. For each phase with `freshness in {current, current_with_caveat}`, ask reuse / rerun / skip with **reuse** as default. For each phase with `freshness in {stale, in_progress, not_started}`, default to **rerun**. Write each decision to `state.phases[<phase>].run_decision` and `decision_reason`.
6. **Persist** the updated state atomically (tmp + rename).
7. State explicitly in any user-facing message that "current" means current for workflow reuse, not formal PLC approval, release-gate clearance, or scanner satisfaction.

A phase whose `run_decision == "reuse"` must be **skipped** entirely in its corresponding step below; do not re-invoke the subskill. The subsequent Validation Summary must reference the prior `produced_artifacts.report_path` (or `log_path` / `evidence_path`) and mark the phase `Freshness = Reused @<sha>` in the report table.

A phase whose `run_decision == "skip"` must be **skipped** entirely with `status = "skipped"` recorded by the corresponding step.

If a phase's prior `status == "in_progress"` with a recorded `last_substep`, resume that phase at the next substep in the bounded list (see *Bounded substep names*). Do not restart the phase from its first substep.

### Step 2 - Requirements Validation

**Unless Step 1 marked `requirements_validation` as reused.** On reuse, read the prior report path from state and skip to Step 3.

If an SRD is available:

1. Load and run `plc-requirements-validation`. Prefer sibling path `../plc-requirements-validation/SKILL.md` when available; otherwise invoke the skill by name.
2. Provide the SRD, repo, and SDD/SADD if available so SRD-to-SDD/SADD consistency can run.
3. Capture status, report path, and finding counts. Use `PASS` when the sub-skill reports no material findings, `WARN` when it reports findings, `ERROR` only for unexpected failures, and `SKIP` for unavailable tool/auth/input.

If no SRD is available, mark requirements `SKIP`, reason `srd_not_provided`.

Maintain state per *Per-phase state writes* in *Freshness and Resume*: set `requirements_validation.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`srd_loaded`, `subskill_ran`, `report_written`), and finalize `status = "complete"` (or `skipped` on expected skip) with `inputs_covered = {srd_path, design_path, comparison_base}` and `produced_artifacts = {report_path, report_digest}`.

### Step 3 - Design Validation

**Unless Step 1 marked `design_validation` as reused.** On reuse, read the prior report path from state and skip to Step 4.

If an SDD/SADD is available:

1. Load and run `plc-design-validation`. Prefer sibling path `../plc-design-validation/SKILL.md` when available; otherwise invoke the skill by name.
2. Provide the design artifact, repo, SRD path when available, and changed files. Ask the sub-skill to compare design against code in both directions.
3. Capture status, report path, finding counts, code/design drift notes, and missing-input/tool failures.

If no SDD/SADD is available, mark design `SKIP`, reason `design_artifact_not_provided`.

Maintain state per *Per-phase state writes*: set `design_validation.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`design_loaded`, `subskill_ran`, `report_written`), and finalize `status = "complete"` (or `skipped`) with `inputs_covered = {design_path, srd_path, comparison_base, changed_files, changed_files_content_digest}` (digest computed per *Content digests for path lists*) and `produced_artifacts = {report_path, report_digest}`.

### Step 4 - Code Implementation Check

**Unless Step 1 marked `code_implementation_check` as reused.** On reuse, read the prior `plc-code-implementation-check.md` (or summary section) and skip to Step 5.

Perform a lightweight agent review; do not create a compliance claim.

1. Identify changed files from the effective changed-file set and summarize implementation intent from committed branch diffs, staged/unstaged diffs, and user context.
2. If SRD/SDD/SADD artifacts were provided, check whether changed behavior is traceable to cited requirements/design items.
3. Flag obvious untraced behavior, test gaps, or design drift as advisory notes.
4. Write `plc-code-implementation-check.md` unless the summary can capture all details without loss.
5. Use `PASS` for no material advisory issues, `WARN` for advisory issues, `SKIP` if no changed files or reviewable context exists, and `N/A` if there is no implementation change.

Maintain state per *Per-phase state writes*: set `code_implementation_check.status = "in_progress"` before starting, update `last_substep` after each completed substep (`changed_files_collected`, `traceability_checked`, `report_written`), and finalize `status = "complete"` (or `skipped`) with `inputs_covered = {srd_path, design_path, comparison_base, changed_files}` and `produced_artifacts = {report_path, report_digest}` (or `produced_artifacts = {}` when no report file was written).

### Step 5 - Verification Check

**Unless Step 1 marked `verification` as reused.** On reuse, read the prior `plc-v-model-validate-verification.log` path from state and skip to Step 6.

1. Run the test command only when it is provided by the user or clearly provided by the repo and is safe/local.
2. Treat commands requiring GPU, Colossus, network, credentials, privileged access, or unavailable infrastructure as `SKIP` with a precise reason unless the user provided a working environment.
3. Save output to `plc-v-model-validate-verification.log`. Record command, duration, exit code, and log path.
4. Mark `PASS` for exit code 0, `WARN` for test failures, `ERROR` for unexpected execution problems, and `SKIP` when no command can run.
5. If no command is available, use reason `test_command_not_provided`. Recommend the smallest likely pre-check-in command only when repo conventions make it obvious, but do not run it as invented coverage.

Maintain state per *Per-phase state writes*: set `verification.status = "in_progress"` before running the command, update `last_substep` after `command_run` and `results_recorded`, and finalize `status = "complete"` (or `skipped`) with `inputs_covered = {test_command, changed_files}` and `produced_artifacts = {log_path, log_digest}`.

When a canonical per-feature evidence log or repo-root `plc-evidence.jsonl` exists, scan it for `pillar:"VERIFICATION"` rows written by `plc-evidence-ingestion` whose `commit` field equals the current HEAD or an ancestor reachable via `git merge-base --is-ancestor`. Surface a one-line summary of those rows in the verification check report (counts per `(check, status|decision)` pair, plus the most recent row's `(ref, status, url)`). Do not re-run the underlying verification or re-fetch the external artifact; this step is read-only. Rows whose `commit` is not reachable from HEAD are mentioned but flagged stale. If no evidence-ingestion rows are found, state that explicitly so the reviewer knows the gap is in the evidence log, not in this skill's pass.

In workspace mode (`agentic_plc/workspace.json` exists), also check `agentic_plc/work_products/<feature-name>/evidence.jsonl` before the legacy `.plc/briefs` and repo-root paths. Preserve workspace attribution fields (`workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, and repo-relative `path`) in the verification summary when present.

### Step 6 - Security Scan

**Unless Step 1 marked `security_scan` as reused.** On reuse, read the prior evidence path from state and skip to Step 7.

1. Load and run `plc-security-scan` before commit or PR handoff. Prefer sibling path `../plc-security-scan/SKILL.md` when available; otherwise invoke the skill by name.
2. Let `plc-security-scan` own detailed evidence entries for secret scanning, SAST, CVE, and license checks. In workspace mode, preserve its workspace/repo attribution fields in summaries.
3. Capture the latest evidence summary, report path, skipped tools, and error reasons.
4. Keep sandbox or Omnistation network failures distinct from tool-not-installed failures.
5. Do not silently skip checks. Do not print secrets.

Maintain state per *Per-phase state writes*: set `security_scan.status = "in_progress"` before invoking the subskill, update `last_substep` after `scanners_run` and `evidence_written`, and finalize `status = "complete"` (or `skipped`) with `inputs_covered = {scan_scope, scan_scope_content_digest, scanner_versions}` (digest computed per *Content digests for path lists*) and `produced_artifacts = {evidence_path, evidence_digest}`.

### Step 7 - Acceptance Validation

**Unless Step 1 marked `acceptance_validation` as reused.** On reuse, read the prior report path from state and skip to Step 8.

This phase classifies every acceptance criterion (`AC-*`) in the feature brief as PASS / FAIL / UNVERIFIED / MISSING / N/A with cited verification pointers (test files, evidence-log rows, manual signoffs). It runs after the verification and security scan phases so it can consume any execution evidence those phases wrote.

1. Locate the inputs the sub-skill needs:
   - `feature_brief_path`: prefer `agentic_plc/work_products/<feature-name>/brief.md` when `agentic_plc/workspace.json` exists, otherwise prefer `.plc/briefs/<feature-name>-brief.md` derived from the SRD's slug or from the user's input. If the developer provided an SRD that contains an `## Acceptance Criteria` table directly, pass the SRD path here. If no AC table can be located in either, mark this phase `SKIP` with reason `acceptance_criteria_not_provided`.
   - `test_plan_path` (optional): prefer `agentic_plc/work_products/<feature-name>/test-plan.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-test-plan.md` produced by `plc-test-plan-authoring`, or the canonical STP at `stp_path` if it includes an AC Coverage table. If neither resolves, pass `null` and the sub-skill will draw mappings from the brief's Traceability table only.
   - `evidence_log_path` (optional): resolve once for the run and pass the same value to both `plc-acceptance-validation` (this step) and `plc-traceability-matrix` (Step 8). Resolution order, first match wins: (a) explicit caller-supplied path, (b) `agentic_plc/work_products/<feature-name>/evidence.jsonl` in workspace mode, (c) `.plc/briefs/<feature-name>-evidence.jsonl`, (d) `plc-evidence.jsonl` at the repo root. Pass `null` if none exists; every AC with a TC-* pointer will then default to UNVERIFIED, which is the correct behaviour. Do **not** independently re-derive the path from `security_scan`'s `evidence_path` — that field is `security_scan`'s output record for its own CODING-pillar rows, not a hint for which log this phase reads. If those two paths disagree on a given run, surface the divergence in the Validation Summary as a follow-up, but keep the validators on the canonical per-feature path so they do not silently consume a different log.
2. Load and run `plc-acceptance-validation`. Prefer sibling path `../plc-acceptance-validation/SKILL.md` when available; otherwise invoke the skill by name.
3. Pass the resolved paths plus the effective changed-file set (so the sub-skill can resolve test-file pointers against the current tree).
4. Capture status, report path, and counts per status (PASS / FAIL / UNVERIFIED / MISSING / N/A) by severity. Use `PASS` when no `FAIL` or Critical-severity `MISSING` ACs were reported, `WARN` when any non-N/A AC is not PASS, `SKIP` when the brief or AC table is unavailable, and `ERROR` only for unexpected failures.

Maintain state per *Per-phase state writes*: set `acceptance_validation.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`brief_loaded`, `subskill_ran`, `report_written`), and finalize `status = "complete"` (or `skipped`) with `inputs_covered = {feature_brief_path, test_plan_path, evidence_log_path, comparison_base, changed_files, changed_files_content_digest}` (digest computed per *Content digests for path lists*) and `produced_artifacts = {report_path, report_digest}`.

### Step 8 - Traceability Matrix

**Unless Step 1 marked `traceability_matrix` as reused.** On reuse, read the prior report path from state and skip to Step 9.

This phase emits the full Requirement ↔ Design ↔ Code ↔ Test ↔ Evidence audit matrix. It runs after `acceptance_validation` so the *Coverage Summary* in the Validation Summary can reference both per-AC status and per-Requirement coverage side by side.

1. Reuse the inputs `acceptance_validation` resolved in Step 7: `feature_brief_path`, `test_plan_path`, `evidence_log_path`. The matrix consumes the same artifacts; deriving them twice would only invite drift.
2. Load and run `plc-traceability-matrix`. Prefer sibling path `../plc-traceability-matrix/SKILL.md` when available; otherwise invoke the skill by name.
3. In workspace mode, pass through the parsed `agentic_plc/workspace.json` context or the workspace root so the matrix can keep Repo and Revision columns aligned with `repo_id` / `repo_url` and `repo_revision`.
4. Pass through the resolved paths plus the effective changed-file set so the sub-skill can resolve Code (`path:line`) pointers against the current tree.
5. Capture status, report path, and counts per Req-row status (`COVERED` / `PARTIAL` / `GAP`). Use `PASS` when zero `GAP` rows, `WARN` when any `PARTIAL` or `GAP` rows exist, `SKIP` when the brief or its requirement tables are unavailable, and `ERROR` only for unexpected failures.

Maintain state per *Per-phase state writes*: set `traceability_matrix.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`brief_loaded`, `subskill_ran`, `report_written`), and finalize `status = "complete"` (or `skipped`) with `inputs_covered = {feature_brief_path, test_plan_path, evidence_log_path, comparison_base, changed_files, changed_files_content_digest}` (digest computed per *Content digests for path lists*) and `produced_artifacts = {report_path, report_digest}`.

### Step 9 - Validation Summary

Generate `plc-v-model-validate-summary.md` using `report-template.md`. Include:

- source docs used (SRD, design, STP, feature brief, evidence log)
- comparison base and merge base
- phases run
- phases skipped and why
- finding counts by phase, including per-status AC counts from `acceptance_validation` and per-Req `COVERED / PARTIAL / GAP` counts from `traceability_matrix`
- **phase freshness** — for each phase, whether it ran this invocation or was reused from `.plc/v-model-state.json`, and at which commit. Phrase reused entries as "current for workflow reuse, not formal PLC approval."
- links or paths to sub-reports
- top risks (include any `traceability_matrix` `GAP` rows in this list)
- next actions
- advisory-only disclaimer

## Templates and References

- Use `report-template.md` for the final V-model validation summary.
- Use `references/subskill-contracts.md` when normalizing sub-skill inputs/outputs.

## Final Response to User

Return the summary path and the highest-risk next actions. State that the result is advisory and does not replace authoritative PLC gates.

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` when the validation summary reports no gaps, `WARN` when findings or gaps remain, `SKIP` with `skip_reason` when required inputs are unavailable, and `ERROR` with `error_reason` for unexpected failures. Keep `summary` to aggregate finding counts only.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"validation","check":"plc-v-model-validate","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-v-model-validate","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
