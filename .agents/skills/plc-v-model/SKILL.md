---
name: plc-v-model
description: Orchestrate PLC-L1 V-model feature authoring and implementation. Use when you need to take a feature request through requirements authoring, design authoring, test plan authoring, implementation, verification, evidence ingestion, security scanning, and traceability-matrix emission. Coordinates plc-requirements-authoring, plc-design-authoring, plc-test-plan-authoring, plc-security-scan, plc-evidence-ingestion, and plc-traceability-matrix.
---

# PLC V-Model

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-v-model","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-v-model"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-v-model","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-v-model"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill for feature authoring mode: turn a feature request into requirements, design, implementation, tests, and security scan evidence. This skill coordinates authoring skills, then implements against the authored draft context, while allowing later findings to revise earlier artifacts when the feature understanding changes.

For validation-only workflows over existing SRD/SDD/SADD artifacts or existing implementation changes, use `plc-v-model-validate`.

## Hard Rules

- Treat all output as advisory developer feedback. Do not claim PLC compliance or release-gate satisfaction.
- Do not replace nSpect, Pulse, ScanSpect, SonarQube, Coverity, BlackDuck, Anchore, OSRB, LaunchAPI, TAVA, or authoritative PLC gates.
- Do not invent requirements, design decisions, acceptance criteria, or test coverage. Mark assumptions and unresolved questions explicitly.
- Do not force a one-way waterfall. If design, test planning, implementation, verification, or security review exposes an upstream gap, pause the current phase, revise the owning upstream artifact with the relevant authoring skill, and re-enter the earliest downstream phase affected by that revision.
- Do not mutate canonical SRD, SDD, SADD, or STP artifacts unless the user explicitly asks and the relevant authoring skill asks before writing.
- Do not print secrets. Do not write secrets or raw credentials anywhere.
- Do not run destructive commands. Preserve unrelated user changes in the worktree.
- In Perforce workspaces, do not mutate files until they are opened in the selected APLC changelist with `aplc p4 open`; if opening fails, stop before editing.
- In Perforce workspaces, add generated review artifacts with `aplc p4 open --action add` immediately after creating them.
- Run `plc-security-scan` during implementation work and before final handoff. If the skill or a required scanner is unavailable, report the skip reason explicitly.
- Treat developer approval gates as workflow consent to proceed, not formal PLC approval, release-gate approval, or replacement for team signoff.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Required Skills

Resolve these skills before starting. The default first-pass traversal is the order below, but later phases may revisit earlier skills when new information invalidates or refines upstream artifacts:

1. `plc-requirements-authoring`
2. `plc-design-authoring`
3. `plc-test-plan-authoring`
4. `plc-security-scan`
5. `plc-evidence-ingestion`
6. `plc-traceability-matrix`

Prefer repo-local sibling paths under `.agents/skills` or the current skill bundle when available. If not available, use the skill by name. If `plc-requirements-authoring`, `plc-design-authoring`, or `plc-test-plan-authoring` is unavailable, pause and tell the user which dependency is missing. If `plc-security-scan` is unavailable, continue only when appropriate and record `security_scan: SKIP` with reason `subskill_not_available`. If `plc-evidence-ingestion` is unavailable, skip Step 11's evidence-persist sub-step and record the gap in the *Final Advisory Summary* (the verification log itself is still written). If `plc-traceability-matrix` is unavailable, skip Step 13's matrix sub-step and record the gap in the *Final Advisory Summary* (the brief's slim Traceability table still gets updated by hand in the rest of Step 13).

## Flexible Traversal

The V-model is a dependency graph, not a rigid sequence. The normal first pass is requirements -> design -> test plan -> implementation -> verification -> security scan -> traceability, but any phase can discover that an upstream artifact is wrong, incomplete, or too vague to continue responsibly.

Use upstream refinement when:

- Design uncovers a missing or untestable requirement, or one that must be split, merged, or expanded.
- Test planning uncovers an unverifiable acceptance criterion, missing design behavior, or a mismatch between ACs and design components.
- Implementation uncovers an infeasible design component, an interface or data-model mismatch, an uncaptured security/privacy concern, or a requirement that cannot be implemented as written.
- Verification uncovers that tests exercise behavior not captured by requirements/design, or that requirements/design/test expectations disagree.
- Security scanning uncovers a requirement or design constraint needed for secure defaults, secrets handling, auth, crypto, input validation, privilege boundaries, data access, network behavior, or logging.

When upstream refinement is needed:

1. Pause the active phase and state the mismatch, affected IDs, and proposed owner (`requirements_authoring`, `design_authoring`, or `test_plan_authoring`).
2. Invoke the owning authoring skill instead of hand-editing around the problem:
   - Requirement text, NFRs, and ACs are owned by `plc-requirements-authoring`.
   - Design components, interfaces, security/PLC implications, and test implications are owned by `plc-design-authoring`.
   - TC IDs, verification layers, coverage tables, and test-plan gaps are owned by `plc-test-plan-authoring`.
3. Preserve stable IDs when semantics remain the same. When semantics change materially, add revision notes or superseding IDs in the brief instead of silently reusing old traceability.
4. Update `.plc/v-model-state.json`: mark the revised phase `complete`, mark every downstream phase whose inputs include the changed slice as `stale`, preserve its existing metadata and artifact paths, and record the reason in `decision_reason` or `notes`.
5. Re-run or re-review the earliest stale downstream phase. Do not jump back to implementation if requirements or design changed in a way that affects test planning.
6. Repeat only the approval gates whose owned slice changed.

Do not treat these loops as workflow failure. Call them out as refinements made during traversal, with changed IDs and the downstream phases re-run or intentionally skipped.

## Workspace Mode

If `agentic_plc/workspace.json` exists at the parent workspace root or repo root, treat the run as a multi-repo workspace run.

- Work products live under `agentic_plc/work_products/<feature-name>/`.
- The feature brief path is `agentic_plc/work_products/<feature-name>/brief.md`.
- The test plan path is `agentic_plc/work_products/<feature-name>/test-plan.md`.
- The evidence log path is `agentic_plc/work_products/<feature-name>/evidence.jsonl`.
- The traceability matrix path is `agentic_plc/work_products/<feature-name>/traceability.md`.
- The traversal plan path is `agentic_plc/work_products/<feature-name>/traversal-plan.json`.
- The traversal summary paths are `agentic_plc/work_products/<feature-name>/traversal-summary.md` and `agentic_plc/work_products/<feature-name>/traversal-summary.json`.
- Every evidence, finding, or traceability record that points into a source repo must include `workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, repo-relative `path` when applicable, and `ref` or `finding_id`.

If no workspace manifest exists, preserve the existing single-repo defaults under `.plc/briefs/` and `plc-evidence.jsonl`.

Workspace mode validates every `repos[].path` as a relative, non-escaping path under the workspace root before using the manifest. `agentic_plc/workspace.json` is authoritative for workspace repo membership and paths; `.plc/profile.json` is advisory brownfield metadata only and may provide artifact, repo-layout, nSpect, and orientation hints, but must not add, remove, or redirect manifest repos.

Generate or reuse the persisted traversal plan during `workspace_preflight`. Major phases are `workspace_preflight`, `requirements_authoring`, `design_authoring`, `test_plan_authoring`, `implementation`, `verification`, `security_scan`, `acceptance_validation`, `traceability_matrix`, and `final_traversal_summary`. Later phases consume the latest `traversal-plan.json` snapshot and refresh it only when the manifest, repo availability, repo revision, selected scope, feature references, or brownfield profile metadata changed. A refresh must increment the plan version and record the reason. Do not re-enter the same repo/phase/action under the same plan version.

Phase preflight must inspect all accessible repos listed in the manifest when the feature, SADD/SDD, test plan, or user request makes them relevant. Repo-bound commands such as edits, tests, and local advisory scans run only on relevant or explicitly selected repos. Every skipped, missing, inaccessible, warned, or failed repo/phase outcome must be surfaced in the traversal summary artifact.

Throughout this skill, "canonical feature brief path", "canonical test plan path", "canonical per-feature evidence log path", and "canonical traceability matrix path" mean the workspace paths above in workspace mode and the existing `.plc/briefs/` paths in single-repo mode.

## Freshness and Resume

Re-running this skill must not redo requirements or design work that is already current, and a long phase that stalls must be resumable without restarting from scratch. The mechanism is intentionally simple and phase-level.

### State file

Persist run state to `.plc/v-model-state.json` in the active child repo (create `.plc/` if it does not exist). The file is shared with `plc-v-model-validate`; each skill owns its own top-level key and must not write into the other's block. Write atomically: write `.plc/v-model-state.json.tmp` then `rename` to `.plc/v-model-state.json`. Store work-product paths in state using the canonical paths for the active mode.

Top-level schema:

```json
{
  "schema_version": "2",
  "plc-v-model": {
    "identity": {
      "feature_name": "<slug>",
      "feature_brief": "<canonical feature brief path>"
    },
    "last_run_commit": "<sha at last write, or unknown>",
    "last_run_timestamp": "<ISO-8601 UTC at last write>",
    "pending_requirements": [
      {
        "placeholder_id": "PENDING-JAMA-<slug>",
        "name": "<Jama item name>",
        "description": "<requirement description>",
        "context": "<source/context>",
        "verification_method": "<automated test/manual test/inspection/analysis/demonstration>",
        "requirement_type": "<Functional/System/etc.>",
        "target_release": "<release or unknown>",
        "feature_brief": ".plc/briefs/<feature-name>-brief.md",
        "status": "pending | resolved",
        "jama_id": "<assigned Jama ID, or null>"
      }
    ],
    "scm": {
      "type": "git | p4 | none",
      "p4_client": "<client or null>",
      "p4_server": "<server or null>",
      "p4_root": "<client root or null>",
      "p4_changelist_mode": "rolling | milestone | null",
      "active_changelist": "<CL number or null>",
      "milestone_changelists": {"<milestone>": "<CL number>"},
      "shelved_milestones": [{"milestone": "<name>", "changelist": "<CL number>", "files": ["path"]}]
    },
    "phases": {
      "requirements_authoring": "<phase record, see below>",
      "design_authoring": "<phase record>",
      "test_plan_authoring": "<phase record>",
      "implementation": "<phase record>",
      "verification": "<phase record>",
      "security_scan": "<phase record>"
    }
  },
  "plc-v-model-validate": "<owned by plc-v-model-validate; this skill must not write here>"
}
```

`pending_requirements` is owned by `plc-v-model` and records new requirements whose authoritative IDs must be assigned by Jama before downstream traceability can proceed. Keep the array present; use an empty array when no pending Jama IDs exist. A pending entry is unresolved when `status == "pending"`, `jama_id` is null/empty, or the feature brief still contains its `placeholder_id`.

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

`status` is the lifecycle field maintained by the phase step itself. `freshness` and `run_decision` are written by the freshness preflight (Step 2) — they record what the most recent preflight classified and what the developer chose for this invocation.

`inputs_covered` and `produced_artifacts` are **disjoint**. `inputs_covered` lists what the phase consumed (source documents, the prior feature brief, the test command, scanner versions). `produced_artifacts` lists what the phase generated (the new brief, the verification log, the evidence file). A generated artifact must never appear in `inputs_covered`; that would let a phase's own untracked output mark itself stale.

### Bounded substep names

Phase resume is bounded — each phase has a fixed, short list of substeps. The state file records the last substep that completed, and on resume the phase picks up at the next substep. Do not invent substeps outside this list.

| Phase | Substeps (in order) |
|---|---|
| `requirements_authoring` | `preflight_complete`, `requirements_drafted`, `subagent_review_complete`, `brief_written`, `jama_ids_resolved` |
| `design_authoring` | `preflight_complete`, `design_drafted`, `brief_updated`, `validation_offered` |
| `test_plan_authoring` | `preflight_complete`, `test_cases_drafted`, `subagent_review_complete`, `plan_written`, `brief_updated` |
| `implementation` | `plan_drafted`, `code_changes_applied`, `tests_added` |
| `verification` | `commands_run`, `results_recorded` |
| `security_scan` | `scanners_run`, `evidence_written` |

### Phase input schema

`inputs_covered` and `produced_artifacts` are both phase-specific objects. Use exactly the shapes below — do not record unrelated fields. If a required input is unknown, set it to `null`; that forces the next preflight to classify the phase as **stale** (not `current_with_caveat`).

| Phase | `inputs_covered` (consumed) | `produced_artifacts` (generated) |
|---|---|---|
| `requirements_authoring` | `{feature_request_digest, template_path, source_docs: [path]}` | `{brief_path, requirements_slice_digest}` |
| `design_authoring` | `{feature_request_digest, requirements_slice_digest, template_path, source_docs: [path]}` | `{brief_path, design_slice_digest}` |
| `test_plan_authoring` | `{feature_request_digest, requirements_slice_digest, design_slice_digest, template_path, source_docs: [path]}` | `{test_plan_path, test_plan_digest, brief_path}` |
| `implementation` | `{plan_digest, changed_files: [path]}` | `{}` |
| `verification` | `{test_command, changed_files: [path]}` | `{log_path, log_digest}` |
| `security_scan` | `{scan_scope: [path], scan_scope_content_digest, scanner_versions: {<scanner>: <version>}}` | `{evidence_path, evidence_digest}` |

`feature_request_digest` and `plan_digest` are sha256 hashes of the relevant text content. `scan_scope_content_digest` is a sha256 over the **bytes of each file in `scan_scope`**, not just the file list — see *Content digests for path lists* below. Record digests so that an edit to the underlying text invalidates freshness even when file paths are unchanged.

`requirements_slice_digest`, `design_slice_digest`, `log_digest`, `evidence_digest`, `test_plan_digest` are sha256 hashes of the produced output's bytes at completion time. For slice digests, hash only the brief sections the phase owns (see *Brief content slices* below), not the whole file. For `log_digest`, `evidence_digest`, and `test_plan_digest`, hash the file's full bytes (the test plan is a standalone file, not a brief slice).

`implementation` has no `produced_artifacts` — its outputs are the file edits themselves, which are tracked through `changed_files` and the worktree state, not through a generated artifact path.

### Content digests for path lists

When a phase depends on the **contents** of a list of files (not just the path names), record both the path list and a content digest. The path list lets `commit_touched_inputs` and `dirty_touched_inputs` in the freshness rule compare real paths against the diff or worktree dirty set; the content digest lets `current_inputs_match` catch same-path content edits that the path-list checks alone would miss when (for example) a file was edited and a previous run's commit and the current commit are the same.

`scan_scope_content_digest` is computed as:

1. Sort `scan_scope` lexicographically.
2. For each path, if the file is missing or deleted, mark the phase stale at preflight time. Do not silently hash an empty list.
3. Build a stable serialization for each entry: `<path>\n<sha256 of file bytes in hex>\n`.
4. Concatenate the serializations.
5. Sha256 the concatenated bytes.

`scan_scope_content_digest` therefore changes whenever any in-scope file's bytes change, even if the path list itself is unchanged.

### Brief content slices

The feature brief at the canonical feature brief path is shared mutable state: multiple phases write to it and Step 11 updates traceability later. Whole-file digests would make every phase invalidate every other phase. Each phase owns a specific slice; its `*_slice_digest` is computed over exactly that slice.

| Phase | Owned slice (sections within the brief) |
|---|---|
| `requirements_authoring` | From `## Proposed Functional Requirements` through `## Acceptance Criteria` (inclusive), plus the `## Open Questions` and `## Advisory Notes` sections. Excludes design sections and traceability table updates. |
| `design_authoring` | From `## Proposed Design Delta` through `## Security And PLC Implications` (inclusive). Excludes `## Traceability` (owned by implementation/Step 11) and the requirements slice above. |

Computing a slice digest:

1. Read the brief.
2. Extract the slice's section range: locate the first owned heading, take all lines up to (but not including) the next heading outside the owned range, in the order they appear in the brief.
3. Normalize trailing whitespace per line (strip trailing spaces; keep blank lines).
4. Concatenate the normalized lines with `\n`.
5. Compute sha256 over the resulting bytes.

`## Traceability` is intentionally outside both slices because Step 11 updates it after implementation. Edits to traceability rows therefore do not invalidate requirements or design freshness.

### Freshness rule

Classify each phase by running the checks below **in order** and returning the first classification that fires. The rule deliberately separates consumed inputs (`inputs_covered`) from generated outputs (`produced_artifacts`): worktree and commit-drift overlap checks only consider consumed input paths, never produced artifact paths.

Define for the current phase:

- `consumed_paths`: every file path appearing in `inputs_covered` (`source_docs`, `changed_files`, etc.). Path-typed fields contribute the path; digest-typed and string-typed fields do not.
- `current_inputs_match`: for every field in `inputs_covered`, the current input value equals the stored value. Recompute digest fields (`feature_request_digest`, `requirements_slice_digest`, `design_slice_digest`, `plan_digest`, `scan_scope_content_digest`) from current content and compare; for brief-slice digests (`requirements_slice_digest`, `design_slice_digest`) recompute from the owned slice per *Brief content slices*; for content digests over path lists, follow *Content digests for path lists*. Compare string fields (`template_path`, `test_command`) exactly. Compare path-list fields (`source_docs`, `changed_files`, `scan_scope`) as sets. Compare `scanner_versions` per-key.
- `artifacts_valid`: every path in `produced_artifacts` exists on disk AND its current digest equals the stored digest. For brief-slice digests (`requirements_slice_digest`, `design_slice_digest`), recompute from the owned slice (see *Brief content slices*); for `log_digest`, `evidence_digest`, and `test_plan_digest`, hash the full file bytes (the test plan is a standalone file, not a brief slice). For `implementation`, `produced_artifacts == {}` so this is trivially true.
- `dirty`: the SCM work-unit paths, normalized to plain paths. For Git, use the union of three commands. Do not parse `git status --porcelain` output directly — its records carry status flags and rename arrows and are not safe to intersect with path sets. Instead use:
  - `git diff --name-only` (unstaged tracked changes)
  - `git diff --cached --name-only` (staged changes)
  - `git ls-files --others --exclude-standard` (untracked files honoring `.gitignore`)

  For Perforce, use non-delete paths from `p4 -ztag opened` in the current client. Take the union, deduplicate, and treat the result as a set of paths.
- `commit_touched_inputs`: for Git, when `state.phases[<phase>].commit != HEAD`, the set returned by `git diff --name-only state.phases[<phase>].commit..HEAD` intersected with `consumed_paths`. For Perforce v1, commit drift is represented by file content digests and opened-file overlap, not depot revision diffs.
- `dirty_touched_inputs`: `dirty` intersected with `consumed_paths`.

Decision table (first matching row wins):

| # | Condition | Classification |
|---|---|---|
| 1 | Identity mismatch (handled in Step 2 before this table; archives owned block) | n/a |
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

The `identity` block binds the owned top-level block to a specific run input set. The preflight (Step 2) must verify identity before classifying any phase.

- `plc-v-model.identity.feature_name` must equal the current feature's slug.
- `plc-v-model.identity.feature_brief` must equal the canonical feature brief path for this run.

If either differs, the **owned block** (`state["plc-v-model"]`) is not applicable to this run. Do not rename or rewrite the whole file — that would discard `state["plc-v-model-validate"]`. Instead:

1. Append the current `state["plc-v-model"]` value to `state["plc-v-model"].archived_runs`, an array of prior snapshots. Each snapshot keeps its `identity`, `phases`, `pending_requirements`, `last_run_commit`, and `last_run_timestamp`, plus an `archived_at` ISO-8601 UTC timestamp.
2. Replace `state["plc-v-model"]` with a fresh block: new `identity` set to the current run, `pending_requirements = []`, `phases` initialized to every phase `not_started`, and `archived_runs` carried over from the prior block (with the snapshot appended).
3. Leave `state["plc-v-model-validate"]` untouched.
4. Persist atomically.
5. Tell the developer the prior `plc-v-model` block belonged to a different feature and was archived in-place.

`archived_runs` may grow over many feature switches. The skill does not auto-prune; the developer can trim it manually if disk concern arises.

### State reconstruction from existing artifacts

If `.plc/v-model-state.json` is missing but the canonical feature brief path exists, reconstruct minimal state by inspecting the brief's content phase-by-phase, not as a whole:

- `requirements_authoring` is reconstructable as `complete` only if the brief contains functional/non-functional requirement tables AND an acceptance criteria table and has no unresolved `PENDING-JAMA-*` placeholders. Use the brief's `Commit` metadata row as `commit`, set `produced_artifacts.brief_path` to the brief path and `produced_artifacts.requirements_slice_digest` to the sha256 of the current requirements slice (see *Brief content slices*), and copy the brief's recorded source documents into `inputs_covered.source_docs`. Leave `feature_request_digest` and `template_path` unknown (null) so the next preflight classifies the phase as stale unless the developer confirms. If the brief contains unresolved `PENDING-JAMA-*` placeholders, reconstruct `requirements_authoring` as `in_progress`, populate `pending_requirements` from the brief's `Pending Jama ID Resolution` table when available, and set `last_substep = "brief_written"`.
- `design_authoring` is reconstructable as `complete` only if the brief contains the design delta sections from `## Proposed Design Delta` through `## Security And PLC Implications`. Set `produced_artifacts.brief_path` to the brief path and `produced_artifacts.design_slice_digest` to the sha256 of the current design slice. Leave `feature_request_digest`, `requirements_slice_digest`, and `template_path` in `inputs_covered` unknown (null).
- `test_plan_authoring` is reconstructable as `complete` only if the canonical test plan path exists. Set `produced_artifacts.test_plan_path` to that path, `produced_artifacts.test_plan_digest` to the sha256 of the test plan file's full bytes, and `produced_artifacts.brief_path` to the brief path. Leave `feature_request_digest`, `requirements_slice_digest`, `design_slice_digest`, and `template_path` in `inputs_covered` unknown (null) so the next preflight classifies the phase as stale unless the developer confirms.
- `implementation`, `verification`, `security_scan` are not reconstructable from the brief alone; mark them `not_started`.

Tell the developer the state file was reconstructed from the brief and which phases were reconstructed.

### Per-phase state writes

Every phase step in this skill must:

1. **Before invoking the subskill or starting work**: atomically write `status = "in_progress"`, `commit = <HEAD>`, `timestamp = <now>`, `last_substep = null`, and populate `inputs_covered` with the inputs the phase is about to consume (digests, paths, command strings). Do this even when the phase later turns out to be a fast no-op.
2. **After each named substep completes** (from *Bounded substep names*): atomically update `last_substep` to that substep's name and bump `timestamp`.
3. **On successful phase completion**: set `status = "complete"`, `last_substep` to the final substep, `produced_artifacts` to the generated paths and their digests (slice digests for brief sections per *Brief content slices*; full-file sha256 for log/evidence; `implementation` writes `{}`), and `dirty_files_at_completion` to the normalized work-unit paths (Git diff/cache/untracked union, or Perforce opened non-delete files).
4. **On expected skip** (subskill unavailable, required input absent, developer chose `skip` in preflight): set `status = "skipped"` with a non-null `decision_reason`.
5. **On unexpected interrupt or error**: leave `status = "in_progress"` with the last completed substep so the next run resumes at the next substep.

These writes are produced by phase steps and consumed by the next preflight. Skipping any write above means the next run cannot make a correct freshness or resume decision.

### Freshness is not formal PLC approval

A "current" phase status here means **the workflow can reuse the prior artifact without redoing the work**. It is not formal PLC approval, release-gate clearance, nSpect/Pulse/SonarQube/Coverity/BlackDuck/OSRB/LaunchAPI/TAVA satisfaction, or team signoff. Phrase it consistently as "current for workflow reuse" or "fresh for this run" in summaries and reports.

### Perforce authoring workflow

When Step 1 detects `scm.type == "p4"`, use the repo-local `aplc p4` helpers for all Perforce mutation and shelving.

1. Ask the developer once per feature run whether shelves should use one changelist per milestone or one rolling changelist. If there is no explicit preference, use milestone changelists. Record the answer in `state["plc-v-model"].scm.p4_changelist_mode`.
2. Establish the active changelist before writing any feature artifact:

```bash
aplc p4 ensure-changelist --repo-root . --feature-name <feature-slug> --milestone <milestone>
```

Omitting `--mode` uses the default `milestone` strategy. Pass `--mode rolling` only when the developer explicitly chooses a rolling changelist.

3. Before editing an existing tracked file, run:

```bash
aplc p4 open --repo-root . --changelist <CL> --action edit --path <path>
```

If this command reports that the file is already opened in another changelist, ask the developer whether to move it, reuse that changelist, or stop. Only pass `--on-opened-other move` or `--on-opened-other reuse` after that explicit decision.

4. Immediately after creating a review-relevant generated file, run:

```bash
aplc p4 open --repo-root . --changelist <CL> --action add --path <path>
```

Generated review-relevant files include feature briefs, test plans, per-feature evidence logs, verification logs intended for review, traceability matrices, and implementation/test files newly created by the feature.

5. Before deleting a tracked file, run:

```bash
aplc p4 open --repo-root . --changelist <CL> --action delete --path <path>
```

6. At each approval gate and final handoff, update the shelf:

```bash
aplc p4 shelve --repo-root . --changelist <CL> --milestone <requirements|design|test-plan|final>
```

Shelving is advisory review handoff only. Never submit a Perforce changelist from this workflow.

## Superpowers Integration

If Superpowers skills are present in the session, use them as workflow discipline for the Agentic PLC flow; do not treat them as competing instructions.

- Requirements and design phases should let `plc-requirements-authoring` and `plc-design-authoring` apply Superpowers `brainstorming` and `writing-plans` while preserving PLC-specific content rules.
- During implementation, use Superpowers `executing-plans` to drive the implementation plan created from the feature brief.
- Use Superpowers `test-driven-development` for implementing requirement/design changes with tests that trace to acceptance criteria and design components.
- Use Superpowers `subagent-driven-development` and `dispatching-parallel-agents` when implementation, test, documentation, or review subtasks can safely run in parallel without conflicting writes and active delegation policy permits it.
- Use Superpowers `using-git-worktrees` when isolation is useful for risky work, parallel branches, or keeping the main worktree clean.
- Use Superpowers `systematic-debugging` when tests fail, behavior is unclear, or implementation does not match the authored requirements/design.
- Use Superpowers `requesting-code-review` after implementation and before final handoff when available.
- Use Superpowers `verification-before-completion` before final summary; do not complete while relevant tests, security scans, or review checks are still pending without an explicit skip reason.
- Use Superpowers `finishing-a-development-branch` for final branch hygiene, summary, and handoff when the user is ready to close the development loop.
- Keep the PLC-specific instructions in this skill as the content and compliance-adjacent standard: requirements/design authoring, traceability, `plc-security-scan`, advisory disclaimers, and preservation of user changes still apply.
- Follow active tool and session policy. If Superpowers are unavailable or disallowed, continue with this skill normally and do not block on them.

## Inputs

Required:

- Repo root, from the current working directory or explicit path
- Feature request or implementation task

Optional:

- Existing feature brief
- SRD/SDD locations that the authoring skills may use
- Test command
- Output path for the feature brief; default and preferred location is the canonical feature brief path for the active mode
- Constraints, target release, platform, stakeholders, acceptance expectations

## Workflow

Track this checklist during the run:

```markdown
V-Model Authoring Progress:
- [ ] Step 1: Establish scope and repo context
- [ ] Step 2: Freshness preflight
- [ ] Step 3: Run requirements authoring and refinement
- [ ] Step 4: Ask developer approval to proceed to design
- [ ] Step 5: Run design authoring
- [ ] Step 6: Ask developer approval to proceed to test plan authoring
- [ ] Step 7: Run test plan authoring
- [ ] Step 8: Ask developer approval to plan and implement
- [ ] Step 9: Plan implementation from the feature brief
- [ ] Step 10: Implement the feature and tests
- [ ] Step 11: Run verification
- [ ] Step 12: Run PLC security scan
- [ ] Step 13: Update feature brief traceability and notes
- [ ] Step 14: Final advisory summary
```

### Step 1 - Establish Scope and Repo Context

1. Resolve repo root and output directory.
2. Collect SCM context:
   - `git -C <repo> rev-parse --show-toplevel`
   - `git -C <repo> branch --show-current`
   - `git -C <repo> rev-parse HEAD`
   - In a Perforce workspace, use `p4 -ztag info` and record `p4/<clientName>` as the branch/commit identity.
3. Identify current staged and unstaged files so unrelated changes are preserved:
   - `git -C <repo> status --short`
   - `git -C <repo> diff --name-only`
   - `git -C <repo> diff --cached --name-only`
   - In a Perforce workspace, use `p4 -ztag opened` and ignore delete-like actions for scan scope.
4. Read the feature request and ask concise clarifying questions only when missing information blocks requirements or design authoring.
5. If the user is actually asking to validate existing documents or implementation, switch to `plc-v-model-validate`.

### Step 2 - Freshness Preflight

Before invoking any authoring or implementation subskill, decide which phases must run and which can be reused. Do this every run.

1. **Locate state**. Look for `.plc/v-model-state.json` and the canonical feature brief path.
   - If neither exists, this is a fresh run. Initialize state with `plc-v-model.identity` set to the current run, `pending_requirements = []`, and every phase `not_started`. Skip to step 6.
   - If only the brief exists, follow *State reconstruction from existing artifacts* in *Freshness and Resume* (reconstruct per phase, not as a whole) and tell the developer.
   - If only the state file exists, proceed.
2. **Verify identity of the owned block**. Compare `state["plc-v-model"].identity.feature_name` and `state["plc-v-model"].identity.feature_brief` to the current feature slug and canonical brief path.
   - If they match, continue.
   - If they do not match, archive the owned block per *Identity binding*: append the current `state["plc-v-model"]` snapshot to `state["plc-v-model"].archived_runs`, replace `state["plc-v-model"]` with a fresh block (new identity, `pending_requirements = []`, every phase `not_started`, `archived_runs` carried forward), leave `state["plc-v-model-validate"]` untouched, persist, and tell the developer the prior plc-v-model block was archived in-place. Skip to step 6.
3. **Check pending Jama IDs.** Before classifying downstream phases, read `state["plc-v-model"].pending_requirements` and the feature brief, if present. If any pending entry is unresolved or the brief contains `PENDING-JAMA-*`, set `requirements_authoring.status = "in_progress"`, set `requirements_authoring.last_substep = "brief_written"` when the brief exists, mark downstream phases (`design_authoring`, `test_plan_authoring`, `implementation`, `verification`, `security_scan`) as not runnable for this invocation, persist the state atomically, and present the pending Jama creation fields to the developer. Do not collect reuse/rerun decisions for downstream phases until the user returns assigned Jama IDs and all placeholders are replaced.
4. **Classify each phase using the decision table in *Freshness rule*.** Run the checks in order (status, inputs populated, artifacts valid + digests match, current-inputs-match, commit-drift overlap, dirty-worktree overlap, then current vs current_with_caveat). Write the resulting classification to `state.phases[<phase>].freshness`.
5. **Present the freshness summary to the developer**:

   ```text
   Freshness summary (workflow reuse, not PLC approval):
   - requirements_authoring: <freshness> — last commit <sha>, brief <path> (digest <match | mismatch>), dirty consumed inputs: <files or none>, out-of-scope dirty files: <files or none>
   - design_authoring: ...
   - test_plan_authoring: ...
   - implementation: ...
   - verification: ...
   - security_scan: ...
   Identity: feature=<slug>, brief=<path>
   Stale because: <per-phase reason: "input X changed", "artifact missing", "commit-diff touches Y", "dirty file Z in consumed inputs", or "n/a">
   ```

6. **Collect decisions**. For each phase with `freshness in {current, current_with_caveat}`, ask reuse / rerun / skip with **reuse** as default. For each phase with `freshness in {stale, in_progress, not_started}`, default to **rerun**. Write each decision to `state.phases[<phase>].run_decision` and `decision_reason`.
7. **Persist** the updated state atomically (tmp + rename).
8. State explicitly in any user-facing message that "current" means current for workflow reuse, not formal PLC approval, release-gate clearance, or scanner satisfaction.

A phase whose `run_decision == "reuse"` must be **skipped** entirely in its corresponding step below; do not re-invoke the subskill. The subsequent step should reference the prior path from `produced_artifacts` instead of regenerating it.

A phase whose `run_decision == "skip"` must be **skipped** entirely with `status = "skipped"` recorded by the corresponding step.

If a phase's prior `status == "in_progress"` with a recorded `last_substep`, resume that phase at the next substep in the bounded list (see *Bounded substep names*). Do not restart the phase from its first substep.

### Step 3 - Run Requirements Authoring and Refinement

Call `plc-requirements-authoring` first, **unless Step 2 marked `requirements_authoring` as reused**. On reuse, read the prior feature brief and skip to Step 4.

Pass through:

- Feature request
- Repo context
- Existing SRD/Jama/local document information if provided
- Output path preference for the feature brief, defaulting to the canonical feature brief path

Expected output:

- A feature brief markdown file at the canonical feature brief path containing proposed functional requirements, non-functional requirements, and acceptance criteria.
- Each requirement has at least one acceptance criterion.
- Any new Jama-backed requirement uses `PENDING-JAMA-<slug>` and appears in the brief's `Pending Jama ID Resolution` table with paste-ready Jama fields.
- The developer has enough context to review the drafted requirements and request refinement before design begins.

If the user already supplied a requirements feature brief, read it and confirm it contains requirements and acceptance criteria. If it is incomplete, run or resume `plc-requirements-authoring`.

Maintain state per *Per-phase state writes* in *Freshness and Resume*: set `requirements_authoring.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`preflight_complete`, `requirements_drafted`, `subagent_review_complete`, `brief_written`, `jama_ids_resolved`), and finalize `status = "complete"` only after `jama_ids_resolved` with `inputs_covered = {feature_request_digest, template_path, source_docs}` and `produced_artifacts = {brief_path, requirements_slice_digest}` (digest computed over the requirements slice per *Brief content slices*) on success.

If requirements authoring produces any `PENDING-JAMA-*` placeholders, update `state["plc-v-model"].pending_requirements` with one entry per pending requirement, keep `requirements_authoring.status = "in_progress"`, set `last_substep = "brief_written"`, persist the state atomically, and pause. Present the blocking message and fields from `plc-requirements-authoring`; do not move to Step 4 until the user returns the Jama-assigned IDs.

On resume with assigned Jama IDs, replace each placeholder throughout the feature brief before any downstream phase runs. Update each matching `pending_requirements` entry with `status = "resolved"` and `jama_id = "<assigned ID>"`. Once no unresolved pending entry remains and the brief no longer contains `PENDING-JAMA-*`, set `last_substep = "jama_ids_resolved"` and complete `requirements_authoring`.

In Perforce workspaces, ensure the feature brief is opened for add/edit in the active APLC changelist before writing or updating it.

### Step 4 - Ask Developer Approval To Proceed To Design

Pause after requirements authoring or requirements refinement. Present a concise requirements checkpoint:

- Feature brief path
- Requirement IDs or short names
- Acceptance criteria summary
- Assumptions and unresolved questions
- Any requirement gaps that still need developer judgment

Before asking for approval to proceed to design, check `state["plc-v-model"].pending_requirements` and the feature brief content. If any entry is unresolved or any `PENDING-JAMA-*` placeholder remains, do not ask for design approval. Instead, repeat:

```text
A new requirement is needed. Please create it in Jama with the details below and bring back the assigned ID — I'll insert it into the SRD and SADD once you have it.
```

Display each pending requirement's Name, Description, Context, Verification Method, Requirement Type, and Target Release. Keep `requirements_authoring.status = "in_progress"` and block Step 5 until all placeholders have resolved Jama IDs.

Ask the developer whether the requirements are approved for design authoring. Do not call `plc-design-authoring` until the developer gives explicit consent to proceed to design. If the developer requests changes, run or resume `plc-requirements-authoring`, update the feature brief, and repeat this approval gate.

Record the gate outcome in working notes or the feature brief only as developer consent to proceed to design. Do not describe it as formal PLC approval or release signoff.

In Perforce workspaces, shelve the active APLC changelist for milestone `requirements` before asking for or immediately after presenting this approval gate, so reviewers can inspect the drafted requirements.

### Step 5 - Run Design Authoring

Call `plc-design-authoring` only after explicit developer consent from Step 4, after all `PENDING-JAMA-*` placeholders have been resolved, **and unless Step 2 marked `design_authoring` as reused**. On reuse, read the prior feature brief's design sections and skip to Step 6.

Pass through:

- The same feature brief path/content
- Requirements and acceptance criteria
- Any SDD source information
- Repo context and feature constraints

Expected output:

- The same feature brief updated after `## Acceptance Criteria` with design delta, test implications, security/PLC implications, and traceability sections from `design-sections.md`.
- Design components trace back to requirements where applicable.
- Unit tests are proposed for design components where applicable.
- The output calls out that threat modeling is not captured yet.
- The developer has enough context to review the drafted design before implementation planning begins.

If the feature brief lacks design sections after this step, pause and rerun or resume `plc-design-authoring` before asking for implementation approval.

Maintain state per *Per-phase state writes*: set `design_authoring.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`preflight_complete`, `design_drafted`, `brief_updated`, `validation_offered`), and finalize `status = "complete"` with `inputs_covered = {feature_request_digest, requirements_slice_digest, template_path, source_docs}` (consuming the requirements slice produced by `requirements_authoring`) and `produced_artifacts = {brief_path, design_slice_digest}` (digest computed over the design slice per *Brief content slices*) on success.

In Perforce workspaces, ensure the feature brief is opened for edit in the active APLC changelist before updating design sections.

### Step 6 - Ask Developer Approval To Proceed To Test Plan Authoring

Pause after design authoring. Present a concise design checkpoint:

- Feature brief path
- Design components and requirement traceability
- Proposed unit tests and verification implications
- Security/PLC implications, including threat-modeling caveat
- Open questions that still affect verification scope

Ask the developer whether the design is approved for verification planning. Do not call `plc-test-plan-authoring` until the developer gives explicit consent to proceed. If the developer requests design changes, run or resume `plc-design-authoring`, update the feature brief, and repeat this approval gate.

Record the gate outcome in working notes or the feature brief only as developer consent to proceed to verification planning. Do not describe it as formal PLC approval or release signoff.

In Perforce workspaces, shelve the active APLC changelist for milestone `design` before asking for or immediately after presenting this approval gate.

### Step 7 - Run Test Plan Authoring

Call `plc-test-plan-authoring` only after explicit developer consent from Step 6, **and unless Step 2 marked `test_plan_authoring` as reused**. On reuse, read the prior test plan at the canonical test plan path and skip to Step 8.

Pass through:

- The feature brief path and content (requirements, acceptance criteria, design sections)
- Repo context including the test framework, fixture conventions, CI layout, and any existing STP source
- Output path preference for the test plan, defaulting to the canonical test plan path

Expected output:

- A test plan markdown file at the canonical test plan path containing test cases grouped by PLC test layer, Requirement Coverage, Acceptance Criterion Coverage, and Layer Coverage tables, plus Entry/Exit Criteria, Environments And Fixtures, and Advisory Notes.
- Every acceptance criterion (AC-*) has at least one TC-* citation or is listed in the plan's Coverage Gaps with a recorded reason.
- The feature brief metadata table has been updated with a `Test plan path` pointer to the test plan file.
- The developer has enough context to review the drafted verification scope before implementation planning begins.

If the test plan lacks coverage tables or AC traceability after this step, pause and rerun or resume `plc-test-plan-authoring` before asking for implementation approval.

Maintain state per *Per-phase state writes*: set `test_plan_authoring.status = "in_progress"` before invoking the subskill, update `last_substep` after each completed substep (`preflight_complete`, `test_cases_drafted`, `subagent_review_complete`, `plan_written`, `brief_updated`), and finalize `status = "complete"` with `inputs_covered = {feature_request_digest, requirements_slice_digest, design_slice_digest, template_path, source_docs}` (consuming the requirements and design slices produced by the prior phases) and `produced_artifacts = {test_plan_path, test_plan_digest, brief_path}` (test plan digest hashes the test plan file's full bytes) on success.

In Perforce workspaces, ensure both the test plan and feature brief are opened for add/edit in the active APLC changelist before writing them.

### Step 8 - Ask Developer Approval To Plan And Implement

Pause after test plan authoring. Present a concise verification checkpoint:

- Test plan path and feature brief path
- Test case count by layer
- Requirement and AC coverage summary, including any Coverage Gaps and their dispositions
- Environment and fixture dependencies
- Open questions that still affect implementation

Ask the developer whether the verification scope is approved for implementation planning and execution. Do not plan implementation, edit files, or run implementation-oriented subskills until the developer gives explicit consent to proceed. If the developer requests test plan changes, run or resume `plc-test-plan-authoring`, update the test plan and the brief metadata, and repeat this approval gate.

Record the gate outcome in working notes or the feature brief only as developer consent to proceed to implementation planning. Do not describe it as formal PLC approval or release signoff.

In Perforce workspaces, shelve the active APLC changelist for milestone `test-plan` before asking for or immediately after presenting this approval gate.

### Step 9 - Plan Implementation From the Feature Brief

After explicit developer consent from Step 8, read the updated feature brief and the test plan, then produce a concise implementation plan:

- Requirements and acceptance criteria to implement
- Design components and affected files
- Interfaces, config surfaces, data model changes, logging, metrics, and secure defaults
- Unit tests and other tests to add or update
- Security-sensitive areas needing extra care
- Open questions that still affect implementation

Present the plan before editing unless the developer's Step 8 consent explicitly included authorization to implement without another planning review. Preserve unrelated worktree changes. Reference the test plan's TC IDs in the implementation plan so each code change has a named verification target.

Maintain state per *Per-phase state writes*: set `implementation.status = "in_progress"` once planning begins, with `inputs_covered = {plan_digest, changed_files}` populated as the plan and edits develop and `produced_artifacts = {}` (implementation has no generated artifact path). Update `last_substep` to `plan_drafted` once the plan is accepted.

### Step 10 - Implement the Feature and Tests

Implement only the scoped feature:

- Follow the requirements and design in the feature brief and the test cases in the test plan.
- Keep code changes traceable to requirement IDs, design elements, acceptance criteria, and the TC IDs in the test plan.
- Add or update unit tests for each applicable design component and TC ID; cite the TC ID in the test name or docstring where useful.
- Add integration, e2e, or manual verification hooks when unit tests are insufficient.
- Update docs/config examples only when required by the feature.
- Keep security-sensitive changes aligned with secure defaults and the security design notes.

In Perforce workspaces, every existing file must be opened with `aplc p4 open --action edit` before mutation, every new review-relevant file must be added with `aplc p4 open --action add` immediately after creation, and every deletion must be opened with `aplc p4 open --action delete` before removal. If any of those commands fail, stop before mutating the file and report the exact P4 error.

Run `plc-security-scan` during implementation when touching security-sensitive code, secrets handling, auth, crypto, input validation, privilege boundaries, data access, network calls, logging of sensitive data, or dependency/container surfaces.

Per *Per-phase state writes*, update `last_substep` to `code_changes_applied` once edits are in place, then to `tests_added` once tests have been added or updated. Finalize `implementation.status = "complete"` with the final `changed_files` recorded in `inputs_covered` when both code and tests are written.

### Step 11 - Run Verification

Run the smallest safe local verification commands that are provided by the user or clearly defined by the repo:

- Unit tests for changed components
- Relevant integration tests when available and safe
- Linters/type checks/builds when repo conventions make them clear

Do not invent coverage. Treat commands requiring unavailable infrastructure, credentials, GPU, privileged access, or network as skipped with a precise reason.

Record:

- Command
- Exit code
- High-signal output summary
- Log path when output is saved
- Requirements, design components, or acceptance criteria covered

Maintain state per *Per-phase state writes*: set `verification.status = "in_progress"` before running commands, update `last_substep` after `commands_run` and `results_recorded`, and finalize `status = "complete"` with `inputs_covered = {test_command, changed_files}` and `produced_artifacts = {log_path, log_digest}` on success.

After recording the verification result, invoke `plc-evidence-ingestion` to persist a normalized row to the canonical per-feature evidence log for every requirement / acceptance criterion the verification command exercised. This is what Step 13's traceability matrix and the downstream `plc-acceptance-validation` skill will read. Always pass `--feature-brief` and `--evidence-log` explicitly so the CLI does not auto-detect a different path on each invocation:

```bash
python3 source/plc-evidence-ingestion/scripts/plc_evidence_ingest.py \
    <check> --ref <FR-*|NFR-*|AC-*> [--url <log-url> | --path <log-path>] \
    --status <pass|fail|partial|skip> [--tc-id TC-N] [--notes "<one-line>"] \
    --feature-brief <canonical feature brief path> \
    --evidence-log  <canonical per-feature evidence log path>
```

- `<check>` is one of `jenkins-build`, `perf-report`, `accuracy-report`, `external-test`, or `other` depending on what produced the result. Unit-test runs from the local agent are typically `external-test` with `tool: pytest|jest|go-test|...` and a `path` pointing at the verification log written above.
- Emit **one row per exercised `(check, tool, ref)`** — a single test run covering three ACs becomes three rows. The CLI rejects multi-ref attempts.
- Only invoke this sub-step when the verification command produced a definitive result. A `SKIP` (no test command) does not need a row; the verification log already records the skip reason.
- If the CLI is unavailable (subskill not packaged for this install), fall back to hand-writing the JSONL line per `plc-evidence-ingestion/SKILL.md` → *Schema*. Never invent ref IDs — only persist rows for requirements the verification command actually exercised. Write the row to the same per-feature path so Step 13 finds it.

### Step 12 - Run PLC Security Scan

Call `plc-security-scan` before final handoff.

Pass through:

- Repo root
- Changed files
- Output directory if specified

Capture:

- Status
- Evidence/report path, such as `plc-evidence.jsonl` or the canonical per-feature evidence log path
- Skipped tools and precise reasons
- Errors separated from expected environment limitations

Do not duplicate secret details from the security scan in the final summary.

Maintain state per *Per-phase state writes*: set `security_scan.status = "in_progress"` before invoking the subskill, update `last_substep` after `scanners_run` and `evidence_written`, and finalize `status = "complete"` with `inputs_covered = {scan_scope, scan_scope_content_digest, scanner_versions}` (digest computed per *Content digests for path lists*) and `produced_artifacts = {evidence_path, evidence_digest}` on success.

When surfacing `plc-security-scan` status in this skill's reports or the feature brief, preserve the local-advisory framing per `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*. Do not relabel a local PASS as release-gate or compliance-equivalent.

## Completion Event

At the end of the orchestration run, record a completion event when `aplc` is available. Use `PASS` when completed phases have no remaining gaps, `WARN` when validation, traceability, verification, or security gaps remain, `SKIP` with `skip_reason` when orchestration could not run, and `ERROR` with `error_reason` for unexpected failures. Keep `summary` to aggregate counts only.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"validation","check":"plc-v-model","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-v-model","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```

### Step 13 - Update Feature Brief Traceability and Notes

Update the feature brief when useful and safe:

- Fill or revise traceability rows with code/files and tests/evidence; cite test plan TC IDs alongside test file paths so the traceability row links Req → Design → Code → TC → Test artifact.
- Confirm the `Test plan path` row in the brief metadata table still points at the latest test plan file.
- Add implementation notes, verification results, security scan status, and unresolved questions in the most appropriate existing section.
- Do not add unrelated design or validation sections.
- Do not mark requirements or design as formally PLC-approved. If useful, record only the developer's explicit consent to proceed between skill phases.

Then invoke `plc-traceability-matrix` to emit the full Requirement ↔ Design ↔ Code ↔ Test ↔ Evidence audit matrix at the canonical traceability matrix path. Prefer sibling path `../plc-traceability-matrix/SKILL.md` when available; otherwise invoke the skill by name. Pass through:

- The feature brief path (the slim Traceability table inside the brief is consumed as a hint).
- The canonical test plan path when present; the matrix reads its *Requirement Coverage* table for TC IDs.
- The canonical per-feature evidence log path — the same path Step 11 wrote VERIFICATION rows to. Do not derive this independently from `plc-security-scan`'s output; the matrix must read the same log the verification step wrote so the Last verified column reflects the row this run produced. If `plc-security-scan` wrote its CODING rows to a different path (the repo-root `plc-evidence.jsonl` default), surface that as a follow-up in the summary; the matrix itself stays single-log for this run.
- The repo root.

Expected output:

- A flat matrix table sorted by Req ID with one row per `FR-*` / `NFR-*` and nine columns: Req ID, Req text, Repo, Revision, Design ref, Code (`path:line`), Test ID, Last verified, Status.
- A `## Gaps` section listing every `PARTIAL` and `GAP` row with the named missing column(s) and a one-line suggested next step.
- A `## Coverage Summary` grouping rows by the 13 PLC requirement types with Covered / Partial / Gap counts.
- A `Traceability matrix path` row added to the feature brief metadata table.

If the matrix surfaces any `GAP` rows, list them in the *Final Advisory Summary* (Step 14) under remaining risks. Do not block on `PARTIAL` rows — they are advisory; treat them as candidates for follow-up evidence collection or a re-run of verification, not as broken state.

### Step 14 - Final Advisory Summary

Return:

- Feature brief path and test plan path
- Traceability matrix path and the `COVERED / PARTIAL / GAP` counts from its *Coverage Summary*
- Changed files
- Requirements/design/test areas implemented
- Tests and verification results, including which TC IDs from the test plan were exercised and their outcomes
- Security scan status and evidence path
- **Freshness summary** — for each phase (including `test_plan_authoring`), whether it was rerun this invocation or reused from `.plc/v-model-state.json` (and at which commit). Phrase reused entries as "current for workflow reuse, not formal PLC approval."
- Skipped checks with reasons
- Remaining risks or open questions, including any Coverage Gaps from the test plan that remain unaddressed
- Recommended next actions, including any remaining requirements/design/test plan review or formal signoff if not already done

State that the result is advisory and does not replace authoritative PLC gates.

In Perforce workspaces, shelve the active APLC changelist for milestone `final` after traceability and notes are updated, then include the CL and shelf milestone in the final summary.

When surfacing `plc-security-scan` status in this skill's reports or the feature brief, preserve the local-advisory framing per `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*. Do not relabel a local PASS as release-gate or compliance-equivalent.
