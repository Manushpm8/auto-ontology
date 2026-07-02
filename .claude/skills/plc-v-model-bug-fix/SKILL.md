---
name: plc-v-model-bug-fix
description: Take a bug report end-to-end through a right-sized PLC L1 V-model and open a merge request — reproduce, fix, verify, security-scan, validate acceptance, generate traceability, and publish — with a single human approval gate immediately before the MR is created. Entry sources are NVBugs tickets, GitLab issues, Jira tickets, local Markdown files, or bug text pasted directly into the prompt. Reuses `plc-security-scan`, `plc-evidence-ingestion`, `plc-acceptance-validation` (compact mode), and `plc-traceability-matrix` (compact mode). Use when the user asks to "fix NVBug N", "fix this bug", "drive this bug through the V-model", "open a fix MR for ticket X", or pastes bug text and asks for a fix. Use `plc-v-model` instead when the entry point is a feature request, not a bug.
---

# PLC V-Model Bug Fix

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-v-model-bug-fix","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-v-model-bug-fix"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-v-model-bug-fix","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-v-model-bug-fix"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to drive a single bug from intake to a published merge request under PLC L1 evidence discipline. The skill sits alongside `plc-v-model` (feature authoring) and `plc-v-model-validate` (validation of existing artifacts) as a third peer; together they cover the three entry shapes Agentic PLC supports.

The skill is right-sized for bug work: the brief is a *narrow feature brief*, the test plan is inline, the traceability matrix is inline, and there are no developer gates between phases. Phases 1–7 run autonomously. Phase 8 is the **single human checkpoint** — the agent summarizes the state and asks for approval before opening the MR.

## Hard Rules

- Treat all output as advisory developer feedback. Do not claim PLC compliance or release-gate satisfaction.
- Do not replace nSpect, Pulse, ScanSpect, SonarQube, Coverity, BlackDuck, Anchore, OSRB, LaunchAPI, TAVA, or authoritative PLC gates.
- Do not invent requirements, design decisions, or acceptance criteria. When no SRD match is found, synthesize a numeric `FR-N` row (e.g., `FR-1`) and mark its `Source` column as `implicit — no SRD match found` so the inference is explicit. The "implicit" signal lives in the `Source` column, not the ID — this keeps IDs compatible with the downstream leaf-skill schemas (`plc-evidence-ingestion` validates `^(FR|NFR|AC)-\d+$`).
- Do not mutate canonical SRD, SDD, SADD, or STP artifacts. The bug brief at `.plc/briefs/bug-<id>-brief.md` is the only authored artifact this skill writes.
- Do not print secrets. Do not write secrets or raw credentials anywhere.
- Do not run destructive commands. Preserve unrelated user changes in the worktree.
- Always run `plc-security-scan` (Phase 5) on the changed paths before opening the MR. If the scan is unavailable, record `security_scan: SKIP` with reason `subskill_not_available` and surface to the developer.
- The human gate at Phase 8 is the only place the skill asks the developer to approve. If the developer declines, write the partial brief, record state, exit cleanly — do not open an MR.
- Treat the developer's Phase 8 approval as workflow consent to open the MR, not formal PLC approval, release-gate approval, or replacement for team signoff.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Bug Sources

Phase 1 (Bug Intake) accepts five entry sources. The downstream flow is identical for all five — only intake changes:

| Source | Recognized input shape | MCP / tool used |
|---|---|---|
| NVBugs ticket | `"fix NVBug 5234567"`, NVBugs URL | `MaaS_NVBugs` MCP |
| GitLab issue | `"fix gitlab-master.nvidia.com/foo/bar#412"`, GitLab issue URL | `MaaS_GitLab` MCP (`gitlab_get_issue`) |
| Jira ticket | `"fix DSSDK-1042"`, Jira URL | `MaaS_Jira` MCP |
| Local file | `"fix the bug described in ~/bugs/<file>.md"` | direct file read |
| Inline prompt | `"fix this bug: <pasted text>"` | direct prompt parse |

Each source resolves to: bug ID (or generated slug for inline/local), summary, repro steps, expected vs actual, severity, affected platforms, and (when available) attachments / linked tickets / status.

Slug generation for inline-prompt and local-file sources: lowercase the first line of the bug summary, replace runs of non-alphanumeric characters with a single hyphen, trim, and prefix with `bug-` (e.g., `bug-rtsp-ipv6-crash`).

## FR Binding — Three-Tier Lookup

Every bug brief must contain at least one row in `## Proposed Functional Requirements` so downstream PLC skills (`plc-evidence-ingestion`, `plc-acceptance-validation`, `plc-traceability-matrix`) accept the brief as valid. The agent resolves the FR using three tiers, in order:

### Tier 1 — In-repo SRD

Look for an SRD-like file at conventional paths in the repo (search order):

1. `docs/srd.md`, `docs/SRD.md`
2. `requirements/SRD.md`, `requirements.md`
3. Any `*.md` file under `.plc/srd/`

For each found SRD, grep for FR rows whose `Requirement` text shares salient nouns with the bug summary. The best match (longest contiguous noun-phrase overlap; tie-break on FR ID ascending) is the bound FR. Copy that FR row verbatim into the bug brief's `## Proposed Functional Requirements` table.

### Tier 2 — Configured external source

If Tier 1 yields no match, look for `.plc/sources.yaml`. Recognized shape:

```yaml
srd:
  type: jama         # or: confluence
  project: <project name or ID>
  # optional, type-specific fields below
  page: <Confluence page title>
  space: <Confluence space key>
```

Resolve via the corresponding MCP (`MaaS_Jama_Cache` or `MaaS_Confluence`), fetch the SRD, and search the same way as Tier 1. Copy the matched FR row into the brief; record the upstream URL in the brief metadata `SRD source used` row.

### Tier 3 — Implicit FR

If Tiers 1 and 2 both come up empty, synthesize a numeric `FR-N` row whose `Source` column carries the implicit-synthesis marker:

| ID | Requirement | Source | Notes |
|---|---|---|---|
| FR-1 | <one-line requirement inferred from the bug, e.g., "RTSP input handler shall not crash when source URI uses IPv6"> | implicit — no SRD match found | bound at bug-fix time |

Use sequential numeric IDs (`FR-1`, `FR-2`, ...) starting at `1` for each bug brief. The "implicit" signal lives in the `Source` column, not the ID — this keeps IDs compatible with `^(FR|NFR|AC)-\d+$` (the regex enforced by `plc-evidence-ingestion`, `plc-acceptance-validation`, and `plc-traceability-matrix`). The bug brief's metadata table also records `SRD source used = none — implicit FR synthesized` so the audit trail is unambiguous at the top of the brief.

## Required Skills

Resolve and use these skills as subskills:

1. `plc-security-scan` — Phase 5, mandatory.
2. `plc-acceptance-validation` (CLI: `plc_acceptance_validate.py`) — Phase 6, compact mode scoped to the linked FR.
3. `plc-evidence-ingestion` (CLI: `plc_evidence_ingest.py`) — sub-step inside Phases 4 and 5, also invoked by Phase 6 if acceptance writes a row.
4. `plc-traceability-matrix` (CLI: `plc_traceability_matrix.py`) — Phase 7, compact mode scoped to the linked FR; output captured to stdout and written into the brief's `## Traceability` section.

Prefer repo-local sibling paths under `.agents/skills` or the current skill bundle when available. If `plc-security-scan` is unavailable, continue only when explicitly authorized by the developer and record `security_scan: SKIP` with reason `subskill_not_available`. If any of the other three subskills is unavailable, pause and tell the developer which dependency is missing — the skill does not silently degrade.

## Superpowers Integration

If Superpowers skills are present in the session, use them as workflow discipline for the bug-fix flow; do not treat them as competing instructions:

- Phase 2 (Reproduce) uses Superpowers `test-driven-development` to write the failing test before any fix is attempted.
- Phase 3 (Root Cause + Fix) uses Superpowers `systematic-debugging` for root-cause analysis and `executing-plans` to apply the fix.
- Phases 2 and 3 may use Superpowers `subagent-driven-development` / `dispatching-parallel-agents` when reproduce and fix work can be split safely. v1 of this skill does not require sub-agents; defer to v2 unless the session policy explicitly allows them.
- Superpowers `using-git-worktrees` is used when multiple bugs are being worked simultaneously in the same repo or when fix isolation is valuable.
- Phases 4 and 6 lean on Superpowers `verification-before-completion`: the orchestrator does not advance to the human MR gate while repro, regression, security, or acceptance checks are still pending without an explicit skip reason.
- Before the human MR gate (Phase 8), Superpowers `requesting-code-review` provides an automated review pass on the bug fix when available.
- Phase 9 (Publish MR) uses Superpowers `finishing-a-development-branch` for final branch hygiene before opening the MR.
- PLC-specific instructions in this skill remain the source of truth for the bug-shaped V-model phases, security-scan invocation, evidence ingestion, acceptance validation, traceability, and advisory framing.
- Follow active tool and session policy. If Superpowers is unavailable or disallowed, the skill continues normally without it.

## Freshness and Resume

Re-running this skill on the same bug must not redo work that is already current, and a phase that fails mid-flow must be resumable from the failed substep without restarting from scratch.

### State file

State is shared with `plc-v-model` and `plc-v-model-validate` at `.plc/v-model-state.json`. This skill owns the top-level key `plc-v-model-bug-fix` and must not write into the other two skills' blocks. Write atomically: write `.plc/v-model-state.json.tmp` then rename.

When this skill writes the state file for the first time on a repo, it bumps the file's `schema_version` from `"2"` to `"3"`. The addition is purely additive — the new top-level key does not affect `plc-v-model` or `plc-v-model-validate`, which continue to read their own keys.

Top-level schema:

```json
{
  "schema_version": "3",
  "plc-v-model": "<owned by plc-v-model>",
  "plc-v-model-validate": "<owned by plc-v-model-validate>",
  "plc-v-model-bug-fix": {
    "identity": {
      "bug_id": "<id>",
      "bug_brief": ".plc/briefs/bug-<id>-brief.md",
      "bug_source": "<nvbugs | gitlab-issue | jira | local-file | inline-prompt>"
    },
    "last_run_commit": "<sha at last write>",
    "last_run_timestamp": "<ISO-8601 UTC>",
    "phases": {
      "bug_intake":      "<phase record>",
      "reproduce":       "<phase record>",
      "root_cause_fix":  "<phase record>",
      "verify":          "<phase record>",
      "security_scan":   "<phase record>",
      "acceptance":      "<phase record>",
      "traceability":    "<phase record>",
      "human_gate":      "<phase record>",
      "publish_mr":      "<phase record>"
    },
    "archived_runs": []
  }
}
```

Phase record shape (same as `plc-v-model`):

```json
{
  "status":       "complete | in_progress | not_started | skipped",
  "commit":       "<sha at last completion or in_progress write, or null>",
  "timestamp":    "<ISO-8601 UTC or null>",
  "last_substep": "<bounded substep name or null>",
  "notes":        "<optional one-line note>"
}
```

The freshness rule and atomic-write semantics from `plc-v-model/SKILL.md` → *Freshness rule* apply unchanged. The bug-fix skill is generally fast enough that freshness checks rarely save time on a fresh run; the primary use of state is **resume on partial failure** (see *Failure Modes* below).

### Bounded substep names

Each phase has a fixed substep list. The state file records `last_substep` after each substep completes; on resume the phase picks up at the next substep.

| Phase | Substeps (in order) |
|---|---|
| `bug_intake` | `bug_fetched`, `fr_bound`, `brief_written` |
| `reproduce` | `test_written`, `test_fails_confirmed` |
| `root_cause_fix` | `root_cause_recorded`, `fix_applied` |
| `verify` | `repro_test_passes`, `regression_passes`, `evidence_written` |
| `security_scan` | `scan_run`, `evidence_written` |
| `acceptance` | `validation_run`, `verdict_recorded` |
| `traceability` | `matrix_generated`, `inlined_into_brief` |
| `human_gate` | `summary_presented`, `approval_received` |
| `publish_mr` | `branch_created`, `committed`, `pushed`, `mr_opened` |

Do not invent substeps outside this list.

### Identity binding

The `identity` block binds the owned top-level block to a specific bug. The preflight (Step 2) verifies identity before classifying any phase. If the current bug ID does not match `state["plc-v-model-bug-fix"].identity.bug_id`, append the current owned-block snapshot to `archived_runs` and initialize a fresh block for the new bug. Mirror the archival logic from `plc-v-model/SKILL.md` → *Identity binding*.

## Inputs

Required:

- Repo root, from the current working directory or explicit path.
- Bug source: one of NVBugs ID, GitLab issue URL, Jira ID, local-file path, or inline prompt text.

Optional:

- Explicit FR ID (`--linked-fr FR-024`) when the developer wants to bypass the three-tier lookup.
- Test command override (`--test-command "pytest tests/regression/ -k rtsp"`) when the default regression scope is wrong.
- Output path override for the bug brief; default and preferred location is `.plc/briefs/bug-<id>-brief.md`.

## Workflow

Track this checklist during the run:

```markdown
Bug-Fix Progress:
- [ ] Step 1: Establish scope and identify bug source
- [ ] Step 2: Freshness preflight
- [ ] Phase 1: Bug intake
- [ ] Phase 2: Reproduce
- [ ] Phase 3: Root cause + Fix
- [ ] Phase 4: Verify
- [ ] Phase 5: Security scan
- [ ] Phase 6: Acceptance
- [ ] Phase 7: Traceability
- [ ] Phase 8: Human gate
- [ ] Phase 9: Publish MR
- [ ] Final advisory summary
```

### Step 1 — Establish Scope and Bug Source

1. Resolve repo root and capture branch + commit (`git rev-parse --show-toplevel`, `git branch --show-current`, `git rev-parse HEAD`).
2. Capture staged/unstaged files (`git status --short`) so unrelated changes are preserved.
3. Parse the developer's input to identify the bug source:
   - NVBugs: matches `\bNVBug\s*\d+\b` or NVBugs URL.
   - GitLab issue: matches `gitlab-master.nvidia.com/.+#\d+` or GitLab issue URL.
   - Jira: matches `[A-Z]+-\d+` (heuristic; confirm with developer if ambiguous).
   - Local file: developer points at an existing path containing bug text.
   - Inline prompt: bug text pasted in the prompt itself.
4. If the developer is actually asking to validate existing artifacts or feature-author, switch to `plc-v-model-validate` or `plc-v-model` respectively.

### Step 2 — Freshness Preflight

1. Locate `.plc/v-model-state.json`. If missing, this is a fresh run for this bug — initialize the `plc-v-model-bug-fix` block (bumping `schema_version` to `"3"`) with the current bug ID, brief path, and source; every phase `not_started`. Skip to Phase 1.
2. If the state file exists, verify `identity.bug_id` matches the current bug. If not, archive the current owned block to `archived_runs` and initialize a fresh block (per *Identity binding* in *Freshness and Resume*).
3. For each phase, classify by the rule in `plc-v-model/SKILL.md` → *Freshness rule*. Phases with `status: complete` and unchanged inputs are reused; phases with `status: in_progress` resume at the next bounded substep.
4. Present a short freshness summary to the developer ("phase X complete from <sha>, resume at phase Y substep Z") and confirm. Default: reuse complete phases, resume in-progress phase. Stale phases re-run.

### Phase 1 — Bug Intake

Fetch the bug, bind to an FR, and write the initial brief.

1. **Fetch the bug** via the appropriate MCP (NVBugs / GitLab / Jira) or read the local file / inline prompt. Capture: summary, repro steps, expected vs actual, severity, affected platforms, upstream link. Generate a slug if the source is inline / local.
2. **Bind the linked FR** using the three-tier lookup (in-repo SRD → configured external → implicit). Record the binding source in the brief metadata.
3. **Write the brief** at `.plc/briefs/bug-<id>-brief.md` using the bundled `bug-brief-template.md`. Populate Metadata, Bug Context, Proposed Functional Requirements (with the bound or implicit FR row, numeric ID `FR-1`), Proposed Non-Functional Requirements (empty unless the bug regresses an NFR), and Acceptance Criteria (the bug's primary AC at minimum, numeric ID `AC-1`). Leave the later sections as template placeholders. Numeric IDs are required by the downstream leaf-skill CLIs — see *Hard Rules* above.

State writes: `status = in_progress` before fetch; `last_substep = bug_fetched` after fetch; `last_substep = fr_bound` after FR binding; `last_substep = brief_written` and `status = complete` after the brief lands on disk.

### Phase 2 — Reproduce

Write a failing test that captures the bug.

1. Use Superpowers `test-driven-development` (when present) to draft the failing test in the conventional test location for the repo (`tests/regression/`, `__tests__/`, etc.). The test must assert the *expected* behavior (no crash, correct output, etc.) so it fails on the current code and will pass once the fix lands.
2. Run the test. Confirm it fails as expected.
3. Update the bug brief's `## Reproduce Test` section with the test file path and a one-line description.

State writes: `last_substep = test_written` after the test lands; `last_substep = test_fails_confirmed` and `status = complete` after the failing run is observed.

If the test cannot be written (the bug isn't reproducible locally — needs specific hardware, external dependencies, etc.), see *Failure Modes* → Reproduce fails.

### Phase 3 — Root Cause + Fix

Diagnose and apply the fix.

1. Use Superpowers `systematic-debugging` (when present) to identify the root cause. Record one paragraph in the brief's `## Root Cause` section.
2. Apply the fix in the worktree. Preserve unrelated user changes.
3. Update the brief's `## Fix Summary` section with the changed files (path:line range) and a brief rationale.

State writes: `last_substep = root_cause_recorded` after the root-cause paragraph lands; `last_substep = fix_applied` and `status = complete` after the fix is in place.

If the fix attempt fails (test still fails after fix), see *Failure Modes* → Fix fails. No retry budget in v1.

### Phase 4 — Verify

Run the repro test and the regression scope; persist evidence rows.

1. Re-run the failing test from Phase 2. Confirm it now passes.
2. Run the regression scope: every test under the touched component's test directory by default, or the developer-supplied `--test-command` override.
3. For each command, persist an evidence row using `plc_evidence_ingest.py`. Prefer rows with **both** `--ref <FR-id>` (the linked Functional Requirement) and `--ac-id <AC-id>` so both requirement-level and AC-level consumers can resolve the row without ambiguity. Downstream traceability also resolves AC-keyed rows (`--ref AC-<id>` or `--ac-id AC-<id>`) back to the parent requirement from the brief's Acceptance Criteria table.

   ```bash
   plc_evidence_ingest.py external-test \
       --ref FR-<id> \
       --ac-id AC-<id> \
       --status <pass|fail|skip> \
       --path <test log path or test file> \
       --tool <pytest|jest|go-test|gtest|...> \
       --feature-brief .plc/briefs/bug-<id>-brief.md \
       --evidence-log  .plc/briefs/bug-<id>-evidence.jsonl
   ```

   `--producer` defaults to `local`; `--producer-id` and `--producer-context` are auto-filled.
4. Update the brief's `## Verification Results` table.

State writes: `last_substep = repro_test_passes` after the repro test passes; `last_substep = regression_passes` after the regression scope runs; `last_substep = evidence_written` and `status = complete` after the evidence rows are appended.

### Phase 5 — Security Scan

Run `plc-security-scan` on the changed paths.

1. Invoke `plc-security-scan` scoped to the files changed in Phase 3.
2. Read the result (status, findings, evidence path, skipped tools).
3. Update the brief's `## Security Scan Summary` section.
4. Persist a VERIFICATION-pillar evidence row for the scan outcome via `plc_evidence_ingest.py`. Use the `other` check type (security-scan results don't fit the test-shaped check types). **The `other` type requires at least one pointer** (`--path`, `--url`, or `--external-id`) — pass `--path` pointing at the changed file (or the scan's evidence file under `.plc/security/`):

   ```bash
   plc_evidence_ingest.py other \
       --ref FR-<id> \
       --ac-id AC-<id> \
       --tool <semgrep|pip-audit|pip-licenses|pulse-secret-scanner|osv-scanner> \
       --status <clean|findings|warn|critical|skip|error> \
       --path <changed-file-or-scan-evidence-path> \
       --notes "<one-line summary, e.g., '0 findings on changed files' or 'docker daemon not running; gap logged'>" \
       --feature-brief .plc/briefs/bug-<id>-brief.md \
       --evidence-log  .plc/briefs/bug-<id>-evidence.jsonl
   ```

   Emit one row per scanner that ran or was skipped (one row for Semgrep, one for pip-audit, one for the secret scanner, etc.) so the verdict shows which scanners cleared and which were skipped. `plc-security-scan` also writes its own CODING-pillar rows in parallel — these `other`-typed VERIFICATION rows are how downstream `plc-acceptance-validation` and `plc-traceability-matrix` see the scan outcome. Per Phase 6's classification rule, these scan-shaped rows are *secondary* citations; the AC's primary verification stays the pytest row from Phase 4.

State writes: `last_substep = scan_run` after the scan completes; `last_substep = evidence_written` and `status = complete` after the evidence row(s) are appended.

If the scan reports WARN or ERROR on security-sensitive findings, see *Failure Modes* → Security scan fails. **Do not auto-open MR.**

### Phase 6 — Acceptance

Validate that the fix meets the bug's AC and doesn't regress other ACs on the linked FR.

1. Invoke `plc_acceptance_validate.py` in compact mode scoped to the linked FR:

   ```bash
   plc_acceptance_validate.py \
       --feature-brief .plc/briefs/bug-<id>-brief.md \
       --evidence-log  .plc/briefs/bug-<id>-evidence.jsonl \
       --scope-fr <FR-id> \
       --mode compact \
       --output stdout \
       --format json
   ```

2. Parse the JSON verdict (`PASS`, `FAIL`, `UNVERIFIED`, `MIXED`, `EMPTY`).
3. Write the per-AC results into the brief's `## Acceptance Check` section, and record the overall verdict.

State writes: `last_substep = validation_run` after the CLI call; `last_substep = verdict_recorded` and `status = complete` after the brief update.

If verdict is FAIL, see *Failure Modes* → Acceptance fails. No MR.

### Phase 7 — Traceability

Generate the compact traceability matrix and inline it.

1. **Pre-populate the brief's `## Traceability` table for the linked FR.** The matrix CLI reads this table for the Design / Code / Test columns — if any cell is empty, the matrix demotes the row to `PARTIAL`. For a bug fix, fill each cell concretely:

   - **Design element:** short description of the affected design surface (e.g., "RTSP URI parser", "Catalog provider parser"). `—` is acceptable for trivial fixes where no specific design element applies — but the row will then come back `PARTIAL`.
   - **Code/files:** the actual `path:line` of the fix from Phase 3 (e.g., `src/rtsp_parser.cpp:142`, `llm_router/catalog/utils.py:132`). Not the brief itself, not a placeholder.
   - **Test/evidence:** the actual path to the regression test from Phase 2 (e.g., `tests/regression/test_rtsp_ipv6.cpp`). **Use the test file path, not `TC-N` placeholders** — the bug-fix flow does not author a separate test plan with `TC-N` rows, so a TC-N reference there would dangle. The matrix CLI accepts a test file path in the Test ID column.

   Concrete example before the matrix CLI runs:

   ```markdown
   ## Traceability

   | Requirement | Design element | Code/files | Test/evidence |
   |---|---|---|---|
   | FR-1 | Catalog provider parser | `llm_router/catalog/utils.py:132` | `tests/test_catalog_provider_whitespace.py` |
   ```

2. Invoke `plc_traceability_matrix.py` in compact mode scoped to the linked FR:

   ```bash
   plc_traceability_matrix.py \
       --feature-brief .plc/briefs/bug-<id>-brief.md \
       --evidence-log  .plc/briefs/bug-<id>-evidence.jsonl \
       --scope-fr <FR-id> \
       --mode compact \
       --output stdout \
       --format markdown
   ```

3. Capture the markdown table from stdout. Replace the brief's `## Traceability` placeholder with the matrix output. Store the same markdown for use in the MR description (Phase 9).

State writes: `last_substep = matrix_generated` after the CLI call; `last_substep = inlined_into_brief` and `status = complete` after the brief update.

### Phase 8 — Human Gate

The single human checkpoint. Do not skip.

1. **Summarize the state** for the developer:
   - Bug ID + one-line summary
   - Linked FR (and whether it was bound from SRD or synthesized as implicit)
   - Changed files
   - Test added (path)
   - Verify: repro now passes; regression scope passed
   - Security scan: PASS / WARN / ERROR + findings count
   - Acceptance verdict
   - Traceability status (COVERED / PARTIAL / GAP)

2. **Detect which publish path will fire** in Phase 9 so the developer sees the consequences before approving. Check in order:
   - **Tier 1 ready** if `glab` (GitLab remote) or `gh` (GitHub remote) is on PATH (`shutil.which` or `command -v`).
   - **Tier 2 ready** if `GITLAB_TOKEN` / `GITHUB_TOKEN` is set in the shell env, OR a `.env` file at the repo root contains a `GITLAB_TOKEN=` / `GITHUB_TOKEN=` line. Check both before declaring Tier 2 unavailable.
   - **Tier 3** is always available as a fallback (URL handoff — one human click in the browser).

3. **Present the choice with annotated hints**. Use the AskUserQuestion tool (or your harness's equivalent structured prompt) with these options:

   - **Approve and open MR** — *(via [tier hint])*
   - **Set up `GITLAB_TOKEN` first for zero-click** — *(takes ~2 min, I'll guide you)* — **only when Tier 1 and Tier 2 are both not ready**, so the user has the option to upgrade before publishing
   - **Decline** — *(no MR; exit cleanly)*

   Render the tier hint on the "Approve" option per the detection result:

   | Detected | Hint shown in parentheses |
   |---|---|
   | Tier 1 ready (glab/gh) | "via `glab`/`gh` CLI — fully automated, no clicks" |
   | Tier 2 ready (token in env or `.env`) | "via API token — fully automated, no clicks" |
   | Tier 3 only | "via URL handoff — opens the form in your browser pre-filled; you'll click Create to finish (no GITLAB_TOKEN set; install `glab` or set a token for zero-click)" |

4. **If the user picks "Set up `GITLAB_TOKEN` first"**, **pause publishing** and walk them through it interactively. **Do not proceed to Phase 9 until the token is in place and verified.**

   a. Derive the GitLab host from `git remote get-url origin` (e.g., `gitlab-master.nvidia.com`).
   b. Tell the user:
      ```
      Step 1: Open this URL in your browser:
        https://<host>/-/user_settings/personal_access_tokens

      Step 2: Create a token with scope 'api' (or 'write_repository'
      if your tier supports fine-grained tokens). Pick an expiration
      you're comfortable with.

      Step 3: Copy the token — it's only shown once.
      ```
   c. Ask the user to confirm the token is created (don't paste it into chat — keep it on their clipboard).
   d. Tell them how to store it (prefer `.env` over shell env because the agent process can verify `.env`; shell-env requires restarting the run):
      ```
      Paste this in your terminal, replacing <paste-token>:

        echo "GITLAB_TOKEN=<paste-token>" >> .env

      Or open .env in an editor and add the line manually.

      The publish helper auto-loads .env and will also silently
      add .env to .gitignore. If .env is already tracked or staged,
      the helper refuses to load tokens from it.
      ```
   e. Wait for the user to say they've done it.
   f. **Silently verify** by reading `.env` and checking for a line matching `^GITLAB_TOKEN=` (or `GITHUB_TOKEN=` for GitHub remotes). **Do not print the token value back to chat.** If no matching line is found, tell the user the line is missing and repeat from step (d).
   g. Once verified, tell the user the token is in place and proceed to Phase 9. The publish helper will auto-load it from `.env` and Tier 2 will fire.

5. **If the user picks "Approve"** with the current detected tier, proceed to Phase 9.

6. **If the user picks "Decline"**, write `human_gate.status = skipped` with `notes = "developer declined at human gate"`, exit cleanly, no MR.

State writes:

- `last_substep = summary_presented` after the summary table is printed.
- `last_substep = approval_received` on approval; `status = complete`.
- On "Set up token first" with successful setup: stay in `human_gate` (`status = in_progress`) until verification completes, then transition to `approval_received` / `complete`. If the user abandons setup, record `status = skipped` with `notes = "developer paused at gate for token setup; resume by re-running"`.
- On decline: `status = skipped`, exit cleanly.

The freshness/resume machinery means a re-invocation after token setup picks up at `human_gate` substep `summary_presented`, asks the choice again, and proceeds to Phase 9 (Tier 2 now ready).

### Phase 9 — Publish MR

Open the merge request.

1. **Branch**: create `bug/<bug-id>` (or `bug/<slug>` for inline/local sources) off the current HEAD if not already on it.
2. **Commit**: stage changed files and the brief, commit with a short subject (`"fix bug <id>"` / `"<short topic> fix"` — 2–3 words, matching the repo convention from `git log`). No multi-line body.
3. **Push**: push the branch to the remote (provider auto-detected from `git remote get-url origin`).
4. **Open MR** via `publish_mr.py`, which uses a **three-tier fallback**:

   - **Tier 1 — CLI:** if `gh` (GitHub) or `glab` (GitLab) is on PATH and authenticated, shell out to it. Best UX — fully automated, MR opens with no human action, auth handled by the CLI's own keyring.
   - **Tier 2 — REST API with PAT:** if the CLI isn't on PATH but a `GITLAB_TOKEN` / `GITHUB_TOKEN` env var is set, POST to the provider's API using stdlib `urllib`. **Zero clicks, no CLI install needed.** The helper auto-loads an untracked `.env` from the repo root and silently adds `.env` to `.gitignore` on first run. If `.env` is already tracked or staged, the helper refuses to load tokens from it. Works on managed machines where the user can't install software.
   - **Tier 3 — URL handoff:** if neither Tier 1 nor Tier 2 fires, the helper prints the provider's "Create new MR" web URL with the title, source branch, target branch, draft flag, and (when ≤ 1500 chars) description URL-encoded. The user clicks the URL, the form opens pre-filled, they click Create. **No install, no PAT, works on Windows / WSL / macOS / Linux identically.** Long descriptions are saved to `.plc/briefs/<branch>-mr-description.md` for paste.

   For Gerrit remotes, `publish_mr.py` exits with a "Gerrit not supported by this helper" message and the orchestrator should use the Gerrit MCP directly.

   MR title format: `"Fix bug <id>: <one-line summary>"`. MR description: the bug-specific sections of the brief (Bug Context summary, Root Cause, Fix Summary, Verification Results, Security Scan Summary, Acceptance Check, Traceability) plus a link back to `.plc/briefs/bug-<id>-brief.md`.

5. **Draft vs ready**: open as **ready** when security scan is PASS and acceptance verdict is PASS. Open as **draft** with caveats called out at the top of the description when either is WARN / UNVERIFIED. Note: GitHub doesn't accept `&draft=1` in its compare URL — when Tier 3 fires for GitHub with `--draft`, the helper prints an instruction telling the user to pick "Create draft pull request" from the dropdown after the form opens.

State writes: `last_substep = branch_created` after the branch is created; `last_substep = committed` after the commit lands; `last_substep = pushed` after push; `last_substep = mr_opened` and `status = complete` once the helper returns 0. Record which tier fired in `notes` so the audit trail is unambiguous:

- Tier 1: `MR opened at <url> via <gh|glab>`
- Tier 2: `MR opened at <url> via REST API (token from env var or .env)`
- Tier 3: `URL emitted at <url> — awaiting human click`

If MR creation fails (network, auth, remote rejection at Tier 1; HTTP 401/403/network at Tier 2; missing or invalid origin at Tier 3), record the failure in `notes` and exit cleanly. The brief, state, and pushed branch remain so the developer can retry or finish manually. Tier 2 auth errors (401, 403) print explicit remediation pointing the user at the PAT generation URL with required scopes.

### Final Advisory Summary

Return:

- Bug brief path
- Linked FR (and binding source)
- Changed files
- Test added
- Verification results
- Security scan status and evidence path
- Acceptance verdict
- Traceability status
- MR URL
- Skipped checks with reasons
- Remaining risks or open questions

State that the result is advisory and does not replace authoritative PLC gates.

## Failure Modes

Each failure leaves a partial brief on disk, records state at the failed substep so the developer can resume, and exits with a specific code so CI integrations can branch on it. **No retry budget in v1.**

| Failure | Behavior | Exit |
|---|---|---|
| Bug intake fails (MCP auth missing, bug not found, source unparseable) | Stop before writing the brief. Surface a clear remediation. | 1 |
| Reproduce fails (agent cannot write a failing test) | Write Bug Context + Linked Requirement + a `Failure Notes` section in Advisory Notes reading "Reproduce: could not reproduce locally". No MR. | 2 |
| Fix fails (test still fails after attempted fix) | Write brief through Fix Summary + a `Failure Notes` section reading "Verification: fix did not pass repro test". No MR. | 3 |
| Security scan WARN/ERROR | Write brief through Security Scan Summary with findings inlined. **Do not auto-open MR.** Surface findings to developer and ask whether to proceed manually (this is the one exception to "Phase 8 is the only gate"). | 4 |
| Acceptance verdict FAIL | Write brief through Acceptance Check with failing ACs called out. No MR. | 5 |
| Traceability matrix emits no rows (e.g., scope-fr matched nothing) | Investigate brief — likely the FR table is wrong. Write brief through Traceability with an empty table and a `Failure Notes` entry. No MR. | 6 |
| Human gate declined | Write the full brief as-is. `status = skipped` for `human_gate`. No MR. | 0 (clean exit) |
| MR publish fails (network, auth, remote rejection) | Brief complete; record failure in `publish_mr.notes`. Exit clean — developer retries by re-invoking the skill. | 7 |

Every non-zero-exit failure ends with: *"Partial brief at `.plc/briefs/bug-<id>-brief.md`. State recorded — resume after addressing X."* The freshness/resume machinery picks up at the failed phase's last completed substep on the next invocation.

## Freshness is not formal PLC approval

A "complete" phase status here means **the workflow can reuse the prior artifact without redoing the work**. It is not formal PLC approval, release-gate clearance, nSpect/Pulse/SonarQube/Coverity/BlackDuck/OSRB/LaunchAPI/TAVA satisfaction, or team signoff. Phrase it consistently as "current for workflow reuse" or "fresh for this run" in summaries.

## Additional Resources

- Bug brief template: [assets/bug-brief-template.md](assets/bug-brief-template.md)
- MR publish helper: [scripts/publish_mr.py](scripts/publish_mr.py)
- Required subskill CLIs:
  - [`plc-evidence-ingestion/scripts/plc_evidence_ingest.py`](../plc-evidence-ingestion/scripts/plc_evidence_ingest.py)
  - [`plc-acceptance-validation/scripts/plc_acceptance_validate.py`](../plc-acceptance-validation/scripts/plc_acceptance_validate.py)
  - [`plc-traceability-matrix/scripts/plc_traceability_matrix.py`](../plc-traceability-matrix/scripts/plc_traceability_matrix.py)
- Required subskill: [`plc-security-scan/SKILL.md`](../plc-security-scan/SKILL.md)
- Shared state file schema: [`plc-v-model/SKILL.md`](../plc-v-model/SKILL.md) → *Freshness and Resume* (rules referenced unchanged)
- Bug-fix integration design: project Google Doc (bug-fix orchestrator, external pipeline integration, and output contract sections)

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` when the bug-fix flow reaches the MR handoff with passing verification, security, acceptance, and traceability checks; `WARN` when local advisory findings, skipped checks, or draft caveats remain; `SKIP` with `skip_reason` when required subskills or inputs are unavailable; and `ERROR` with `error_reason` for unexpected failures. Keep `summary` to aggregate counts only.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"validation","check":"plc-v-model-bug-fix","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-v-model-bug-fix","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
