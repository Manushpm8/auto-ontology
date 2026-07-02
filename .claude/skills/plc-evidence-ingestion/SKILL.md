---
name: plc-evidence-ingestion
description: Normalize developer-provided pointers to external verification evidence (Jenkins build URLs, perf reports, manual signoffs, ScanSpect/nSpect run IDs, accuracy reports, external test runs) into append-only `plc-evidence.jsonl` rows keyed by a feature brief's `FR-*` / `NFR-*` / `AC-*` IDs. Use when the user says "log this Jenkins run as evidence for AC-3", "record manual signoff", "ingest the perf report", "add scanspect SS-12345 to the evidence log", "log verification evidence", or after a verification command completes and the result needs to be persisted against a requirement. The skill never fetches evidence on its own — it accepts user-supplied references, validates them against the brief, and appends. Future fetch-and-summarize via MCP is out of scope for v1.
---

# PLC Evidence Ingestion

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-evidence-ingestion","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-evidence-ingestion"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-evidence-ingestion","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-evidence-ingestion"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Take pointers to external verification evidence — Jenkins URLs, perf reports, manual signoff notes, ScanSpect/nSpect run IDs, accuracy reports, screenshots — and normalize them into the append-only `plc-evidence.jsonl` schema. The schema is a strict superset of what `plc-security-scan` already writes: same `pillar` / `check` / `tool` / `branch` / `commit` / `timestamp` fields, plus a new mandatory `ref` field naming the requirement or acceptance criterion the evidence supports.

This skill is the right-side V-model counterpart to `plc-security-scan`: security scans emit CODING-pillar rows, this skill emits VERIFICATION-pillar rows. Downstream skills consume both:

- `plc-acceptance-validation` reads VERIFICATION-pillar rows keyed by `AC-*` to upgrade UNVERIFIED ACs to PASS.
- `plc-traceability-matrix` reads VERIFICATION-pillar rows keyed by `FR-*` / `NFR-*` to fill the *Last verified* column.

**This skill is advisory.** Ingesting an authoritative-source ID (a Jenkins build URL, a ScanSpect run ID, an nSpect result) makes the *pointer* available to downstream tooling; it does NOT make the local entry release-gate-equivalent. The ingested evidence inherits whatever authority its source has. The skill itself does not satisfy PLC requirements, replace formal acceptance review, or replace deterministic release validation (qualified scanners, evidence generation, policy enforcement via LaunchAPI, TAVA, nSpect, ScanSpect, SonarQube, Coverity, BlackDuck, OSRB).

---

## Where This Sits in the V-Model

```
plc-test-plan-authoring       →  TC-* with verification methods
implementation + verification →  Jenkins build, perf run, manual review happens
plc-evidence-ingestion       ←   THIS SKILL: persist Req/AC ↔ evidence pointer to plc-evidence.jsonl
plc-acceptance-validation    →  reads the log, classifies each AC
plc-traceability-matrix      →  reads the log, fills Last verified column
```

The skill runs at the moment evidence becomes available. In an automated flow it can be wired as a post-step of `plc-v-model` Step 11 (Verification) so each verification command's result is automatically persisted. In a manual flow, a developer invokes it directly after copying a Jenkins URL or a signoff note from chat.

---

## Schema

Every line written by this skill is a one-line JSON object with the following shape. Keys are sorted by `plc-security-scan/scripts/plc_security_scan.py` → `append_evidence()` (`json.dumps(record, sort_keys=True, separators=(",", ":"))`), so the on-disk shape is byte-stable across writers.

**Required fields (every line):**

| Field | Type | Notes |
|---|---|---|
| `pillar` | string | Always `"VERIFICATION"` for this skill. (`plc-security-scan` writes `"CODING"`.) |
| `check` | string | One of: `jenkins-build`, `perf-report`, `accuracy-report`, `manual-signoff`, `scanspect-run`, `nspect-result`, `external-test`, `other`. Stable across tool changes; see [evidence-types.md](assets/evidence-types.md). |
| `tool` | string | Specific tool name. `jenkins`, `nspect`, `scanspect`, `manual`, etc. The `(check, tool)` pair identifies the row's origin. |
| `ref` | string | The Req ID (`FR-*` / `NFR-*`) or AC ID (`AC-*`) the evidence supports. Multiple refs → emit multiple rows (one ref per row keeps downstream filtering trivial). |
| `branch` | string | Git branch at write time, or `p4/<client>` in a Perforce workspace. Detached Git HEAD → `"detached/<short-sha>"`. |
| `commit` | string | Full Git SHA at write time, or `p4/<client>` in a Perforce workspace. Never abbreviate Git SHAs in the log. |
| `timestamp` | string | ISO-8601 UTC, second precision, e.g. `"2026-05-15T18:30:00Z"`. |
| `producer` | string | `"local"` (the agentic-plc skills running in this repo wrote the row) or `"external"` (an upstream pipeline like the DeepStream bug-fixer wrote it on our behalf). Defaults to `"local"` when not supplied. |
| `producer_id` | string | Pipeline identifier — e.g., `"agentic-plc"` (default), `"agentic-plc-bugfix"`, `"deepstream-bugfix-agent"`. **Required when `producer = "external"`**; auto-filled to `"agentic-plc"` when `producer = "local"` and not supplied. |
| `producer_context` | string | Repo and commit context where the row was produced — e.g., `"<repo>@<sha>"`. **Required when `producer = "external"`**; auto-filled to `"<branch>@<commit>"` from the current worktree when `producer = "local"` and not supplied. |
| **One of:** `status` or `decision` | string | See per-type rules in [evidence-types.md](assets/evidence-types.md). `status` for build/run-shaped evidence (`green` / `red` / `aborted` / `pass` / `fail` / `partial`); `decision` for review-shaped evidence (`accepted` / `rejected` / `pending`). |

Legacy rows written before the provenance fields were added do not carry `producer` / `producer_id` / `producer_context`. Downstream readers should treat an absent `producer` field as equivalent to `"local"` with unknown id and context.

**Optional fields (per evidence type):**

| Field | Type | Used by |
|---|---|---|
| `url` | string | `jenkins-build`, `perf-report`, `accuracy-report`, `scanspect-run`, `nspect-result`, `external-test` — the public/internal URL to the canonical artifact. |
| `path` | string | `perf-report`, `accuracy-report`, `external-test`, `other` — a repo-relative path when the artifact is checked into the repo. Cannot start with `..` or be absolute. |
| `external_id` | string | `scanspect-run`, `nspect-result`, `external-test`, `other` — a vendor/system identifier (e.g., `SS-12345`, `NS-789`, TestRail case ID). |
| `reviewer` | string | `manual-signoff` (required) and any other type where a named human signed off. Use the corp username, not the display name, to keep filtering stable. |
| `notes` | string | Free-form, ≤ 280 chars, sanitized (see *Secrets rule* below). |
| `tc_id` | string | When the evidence pertains to a specific test plan TC (e.g., `TC-3`). Optional but lets `plc-acceptance-validation` join through the test plan's `Verifies` field even when `ref` points at the parent Req. |
| `ac_id` | string | Alias of an AC when `ref` names a parent Req. Use when you want both the parent Req and the AC indexed in the same row. |
| `worktree_digest` | string | Optional sha256 over relevant evidence content when the artifact lives in the repo. |
| `finding_id` | string | Finding identifier when the row points at a repo finding instead of only a requirement/AC. |
| `workspace_id` | string | Stable workspace identifier linking the repos for a multi-repo PLC feature. |
| `workspace_root` | string | Stable manifest-relative or caller-supplied workspace root identifier when no stable `workspace_id` exists or when both are useful. Do not emit local host paths by default. |
| `repo_id` | string | Workspace-local repo identifier for the repo this row points into. |
| `repo_url` | string | Repository URL for the repo this row points into. |
| `repo_revision` | string | Commit SHA or other revision for the repo this row points into. |

**Forbidden fields:** secret values, raw credentials, raw source snippets, full screenshots-as-base64-strings, anything the artifact retriever could subpoena. URLs are fine — `https://blossom.nvidia.com/job/123` reveals only the job name, not its contents. See *Secrets rule* below.

**Workspace attribution minimum:** In workspace mode, every evidence, finding, or traceability record that points into a repo must include:

- `workspace_id` or `workspace_root`
- `repo_id` or `repo_url`
- `repo_revision`
- `path` when the record points at a file
- `ref` for requirement/AC records, or `finding_id` for finding records

The bundled CLI enforces the first three bullets whenever workspace mode is active through CLI flags, `AGENTIC_PLC_*` environment variables, or a discovered `agentic_plc/workspace.json`. Prefer `workspace_id`; if `workspace_root` is needed, use a stable manifest-relative value or explicitly set `AGENTIC_PLC_WORKSPACE_ROOT`. The CLI keeps the existing single-repo schema unchanged when no workspace context is present.

---

## Example Lines

Local producer (the agentic-plc skills wrote these in this repo):

```json
{"branch":"feat/rate-limit","check":"jenkins-build","commit":"abc1234def5678901234567890abcdef12345678","notes":"100/min boundary test","pillar":"VERIFICATION","producer":"local","producer_context":"feat/rate-limit@abc1234def5678901234567890abcdef12345678","producer_id":"agentic-plc","ref":"AC-1","status":"green","timestamp":"2026-05-13T18:30:00Z","tool":"jenkins","url":"https://blossom.nvidia.com/job/rate-limit-ci/123"}
{"branch":"feat/rate-limit","check":"manual-signoff","commit":"abc1234def5678901234567890abcdef12345678","decision":"accepted","pillar":"VERIFICATION","producer":"local","producer_context":"feat/rate-limit@abc1234def5678901234567890abcdef12345678","producer_id":"agentic-plc","ref":"AC-5","reviewer":"sgupta","timestamp":"2026-05-13T18:31:00Z","tool":"manual"}
{"branch":"feat/rate-limit","check":"scanspect-run","commit":"abc1234def5678901234567890abcdef12345678","external_id":"SS-12345","pillar":"VERIFICATION","producer":"local","producer_context":"feat/rate-limit@abc1234def5678901234567890abcdef12345678","producer_id":"agentic-plc","ref":"NFR-3","status":"clean","timestamp":"2026-05-13T18:32:00Z","tool":"scanspect"}
{"ac_id":"AC-7","branch":"feat/rate-limit","check":"perf-report","commit":"abc1234def5678901234567890abcdef12345678","notes":"p99 21ms vs 25ms budget","pillar":"VERIFICATION","producer":"local","producer_context":"feat/rate-limit@abc1234def5678901234567890abcdef12345678","producer_id":"agentic-plc","ref":"NFR-2","status":"pass","tc_id":"TC-12","timestamp":"2026-05-13T18:33:00Z","tool":"locust","url":"https://internal.nvidia.com/perf-runs/abc"}
{"branch":"feat/rate-limit","check":"external-test","commit":"abc1234def5678901234567890abcdef12345678","path":"tests/rate_limit/test_retry.py","pillar":"VERIFICATION","producer":"local","producer_context":"feat/rate-limit@abc1234def5678901234567890abcdef12345678","producer_id":"agentic-plc","ref":"AC-1","repo_id":"runtime","repo_revision":"def5678901234567890abcdef1234567890abcd","repo_url":"https://gitlab.example.com/team/runtime.git","status":"pass","timestamp":"2026-05-13T18:34:00Z","tool":"pytest","workspace_id":"rate-limit-workspace"}
{"branch":"feat/rate-limit","check":"scanspect-run","commit":"abc1234def5678901234567890abcdef12345678","external_id":"SS-12345","finding_id":"SAST-12","path":"src/auth/retry.py","pillar":"VERIFICATION","producer":"local","producer_context":"feat/rate-limit@abc1234def5678901234567890abcdef12345678","producer_id":"agentic-plc","ref":"NFR-3","repo_id":"control-plane","repo_revision":"0123456789abcdef0123456789abcdef01234567","status":"findings","timestamp":"2026-05-13T18:35:00Z","tool":"scanspect","workspace_id":"rate-limit-workspace"}
```

External producer (an upstream pipeline wrote this on our behalf — `producer_id` and `producer_context` identify which pipeline and where it was running):

```json
{"branch":"bug/5234567","check":"external-test","commit":"def5678901234567890abcdef1234567890abcd1","path":"tests/regression/test_rtsp_ipv6.cpp","pillar":"VERIFICATION","producer":"external","producer_context":"DeepStreamSDK/Autonomous-BugFix-Framework@abc1234","producer_id":"deepstream-bugfix-agent","ref":"FR-024","status":"fail","tc_id":"TC-bug-5234567-repro","timestamp":"2026-05-22T18:00:00Z","tool":"gtest"}
```

---

## Prerequisites

Required:

- A feature brief at `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md` (or a user-provided path), containing at minimum the `## Proposed Functional Requirements`, `## Proposed Non-Functional Requirements`, or `## Acceptance Criteria` tables. The brief is the source of truth for which `ref` IDs are valid. If the brief is missing, the skill can still write rows but every ingestion will be flagged as orphan and require explicit confirmation (`--allow-orphan` in the CLI).
- Read access to the repo's `git` so the writer can capture `branch` and `commit`.

Optional:

- A test plan adjacent to the feature brief, named `test-plan.md` in workspace mode or `<feature-name>-test-plan.md` in single-repo mode. Used only when the user supplies a `tc_id` — the skill verifies the TC exists in the plan and that its `Verifies` field includes the `ref` ID.
- A multi-repo workspace manifest at `agentic_plc/workspace.json`. When present, the CLI can populate `workspace_id`, `repo_id`, `repo_url`, and `repo_revision` from the manifest and the active git repo.

---

## Workflow

Track this checklist:

```markdown
Evidence Ingestion Progress:
- [ ] Step 1: Collect inputs (type, ref, pointer, status/decision, optional fields)
- [ ] Step 2: Validate the ref against the feature brief
- [ ] Step 3: Validate the pointer (URL shape, path inside repo, external_id non-empty)
- [ ] Step 4: Run the secrets-rule check on every string field
- [ ] Step 5: Capture branch + commit + timestamp
- [ ] Step 6: Append the line atomically
- [ ] Step 7: Confirm to the user
```

### Step 1 — Collect Inputs

For each piece of evidence the developer wants to ingest, gather:

1. **Type** — exactly one of the eight values in *Schema* above. If the developer's wording is ambiguous (e.g., "log this build" could be `jenkins-build` or `external-test`), ask. Default `jenkins-build` when the URL is a Blossom/Jenkins URL.
2. **Ref** — one of `FR-*`, `NFR-*`, or `AC-*`. Reject anything else (e.g., `TC-3` belongs in `tc_id`, not `ref`).
3. **Pointer** — `url`, `path`, or `external_id`. Each evidence type has its required minimum; see [evidence-types.md](assets/evidence-types.md).
4. **Status or decision** — exactly one. Build/run-shaped types take `status`; review-shaped types take `decision`. See per-type rules.
5. **Optional fields** — `reviewer`, `notes`, `tc_id`, `ac_id`.
6. **Workspace attribution** — when the pointer targets a source repo in a multi-repo workspace, capture `workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, the repo-relative `path` when applicable, and either `ref` or `finding_id`.

If the developer is ingesting many entries at once (e.g., a batch of Jenkins build URLs from a single CI run), collect each independently. **One evidence line covers one `(check, tool, ref)` combination.** A single Jenkins build that exercises three ACs becomes three lines with the same URL and three different `ref` values — never one line with three refs comma-separated.

### Step 2 — Validate the Ref Against the Feature Brief

For each ingestion, find the feature brief (default `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`). Slug rules match `plc-requirements-authoring`: lowercase ASCII letters and digits, runs of non-alphanumeric characters collapsed to a single hyphen, trimming leading/trailing hyphens.

Grep the brief for the `ref` value. If the brief contains a row with that ID in `## Proposed Functional Requirements`, `## Proposed Non-Functional Requirements`, or `## Acceptance Criteria`, the ref is valid. Otherwise, **warn and ask for confirmation** before writing — the developer might be ingesting evidence for a feature whose brief lives elsewhere, or might have typoed the ID. Never silently accept an unmatched ref.

If the user explicitly says "this ref isn't in any brief, log it anyway", record the line with an additional field `"ref_unverified":true` so downstream skills can flag the orphan when they parse the log.

If a `tc_id` is supplied and a test plan exists, also verify the TC exists in the test plan's per-layer Test Cases tables and that its `Verifies` field includes the `ref` ID. Mismatch → warn and ask.

### Step 3 — Validate the Pointer Shape

Per evidence type (see [evidence-types.md](assets/evidence-types.md)):

- **URL**: must start with `http://` or `https://`. Strip any embedded credentials (`https://user:pass@host/path` → `https://host/path`) and warn the developer.
- **Path**: must be repo-relative. Reject if it starts with `..`, is absolute, or resolves outside the repo working tree.
- **External ID**: non-empty, no whitespace, no slashes. Vendor systems use a stable shape — accept `SS-*` / `NS-*` / `TR-*` and similar; if the developer pastes a free-form string, ask whether it's the intended ID.

### Step 4 — Secrets Rule

Apply the same rule as `plc-security-scan/SKILL.md` → *Evidence Collection* §"Never include secret values…":

- Never write tokens, API keys, passwords, bearer values, or anything that looks like one (high-entropy strings ≥ 32 chars without obvious whitespace) into `notes`, `url`, or any free-form field. If a URL contains a query string with `token=…`, strip the value (keep the parameter name).
- Never embed screenshot contents (base64 image data) inline. Use a `path` pointing at the file in the repo, or a `url` pointing at the upload — never `notes: "data:image/png;base64,…"`.
- Never embed raw test output. The CI artifact already has it; the evidence row is a pointer, not a copy.

If any value looks suspect, ask the developer before writing. Better a redundant question than a leaked credential.

### Step 5 — Capture Branch + Commit + Timestamp

Right before writing, resolve SCM metadata. In Git, run:

```bash
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
COMMIT="$(git rev-parse HEAD)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

In Perforce workspaces, use `p4 -ztag info` and record `BRANCH="p4/<clientName>"`, `COMMIT="p4/<clientName>"`, plus `scm="p4"` and the client/server fields when the helper script emits them.

If HEAD is detached, render the branch field as `detached/<short-sha>` (first 7 chars of `commit`). Never write `branch: "HEAD"`.

These are captured **at write time, not at the user-supplied time**. The evidence row records when the ingestion happened, attributed to the worktree state at that moment. The evidence's own production time is implicit in its `url` or `external_id`; downstream tooling can fetch the run page if it needs the run-time timestamp.

### Step 6 — Append Atomically

Resolve the evidence log path:

1. If the user supplied an explicit path, use it.
2. Otherwise prefer per-feature `agentic_plc/work_products/<feature-name>/evidence.jsonl` in workspace mode.
3. Otherwise prefer per-feature `.plc/briefs/<feature-name>-evidence.jsonl` when the feature name was derived in Step 1.
4. Otherwise default to `plc-evidence.jsonl` at the repo root.

Append with the same writer pattern as `plc-security-scan/scripts/plc_security_scan.py` → `append_evidence(path, record)`:

```python
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
```

- **Sort keys.** Byte-stable lines make `diff` and `git blame` on the log meaningful.
- **Append-only.** Never edit an existing line. If the developer wants to correct an entry, append a new line with the same `(check, tool, ref)` and let downstream skills apply last-write-wins semantics (the `timestamp` field disambiguates).
- **One write per line.** Multiple ingestions in one user request → multiple `append_evidence` calls, each preceded by Step 4's secrets check.

If the parent directory had to be created, log that fact so the developer notices an unexpected default location.

### Step 7 — Confirm

Tell the developer: how many lines were written, which evidence log they went to, and the `(ref, check, tool)` triple for each. Offer a tail of the log so they can sanity-check. Do not print the secret-stripped values back to them — print the same string the file got.

---

## Bundled CLI

This skill ships [scripts/plc_evidence_ingest.py](scripts/plc_evidence_ingest.py) for non-interactive ingestion (CI hooks, post-test scripts, automation). It enforces the schema, captures branch/commit/timestamp, runs the secrets check on every string field, and validates the ref against the feature brief.

Invoke:

```bash
python3 source/plc-evidence-ingestion/scripts/plc_evidence_ingest.py \
    jenkins-build \
    --ref AC-1 \
    --url https://blossom.nvidia.com/job/rate-limit-ci/123 \
    --status green \
    --notes "100/min boundary test"
```

For cross-repo workspace records, pass the attribution fields explicitly or place `agentic_plc/workspace.json` at the parent workspace root:

```bash
python3 source/plc-evidence-ingestion/scripts/plc_evidence_ingest.py \
    external-test \
    --ref AC-1 \
    --path tests/rate_limit/test_retry.py \
    --status pass \
    --tool pytest \
    --workspace-id rate-limit-workspace \
    --repo-id runtime \
    --repo-url https://gitlab.example.com/team/runtime.git \
    --repo-revision def5678901234567890abcdef1234567890abcd
```

The agent should prefer the CLI when running non-interactively or when scripting ingestion. When running interactively with a single ad-hoc ingestion, hand-writing the JSONL line is acceptable as long as Steps 1–7 above are followed.

The CLI uses **stdlib only** — no third-party deps — same constraint as `plc_security_scan.py`. It is installed alongside this skill via `install.sh` (the `SKILLS` array copies the whole directory recursively).

---

## Important Constraints

- **Append-only.** The skill never edits existing lines. Corrections are new lines.
- **Schema is a strict superset of `plc-security-scan`.** Existing readers don't break. Required fields (`pillar`, `check`, `tool`, `branch`, `commit`, `timestamp`) match; this skill adds `ref` as another always-required field and per-type extras.
- **`ref` must match an ID in the feature brief.** Orphan refs trigger a warning-and-confirm cycle. Bypassing this is allowed (the developer's call) but recorded as `ref_unverified: true` so downstream tooling can surface it.
- **No secrets in the log.** Same rule as `plc-security-scan`. URLs are fine; raw credentials are not. The skill never writes a field whose value matches a credential heuristic without asking.
- **Branch + commit captured at write time** from Git or Perforce client metadata. Not user-supplied. Detached Git HEAD → `detached/<short-sha>`.
- **Stdlib-only CLI.** No third-party Python deps. Mirrors `plc_security_scan.py`.
- **Respect the advisory wording contract.** See `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*. Even when ingesting from authoritative sources (nSpect, ScanSpect), the *ingestion* row is local advisory wiring. The phrases `release-equivalent`, `would block release`, `PLC-approved`, `OSRB-clear`, `audit-clean` are reserved and must not appear in the log, agent output, or PR copy unless `~/.agentic-plc/scanner-approvals.jsonl` records an approval for that exact wording. Default to advisory framing.
- **One evidence line covers one `(check, tool, ref)` combination.** Never collapse multiple refs into a single comma-separated `ref` field — downstream filters key off exact match.
- **Workspace attribution is explicit.** In workspace mode, every repo-pointing row includes `workspace_id` or `workspace_root`, `repo_id` or `repo_url`, `repo_revision`, repo-relative `path` when applicable, and `ref` or `finding_id`.

---

## Advisory Disclaimer

When this skill writes a confirmation message to the developer, it ends with:

```markdown
Evidence rows are **advisory only**. They record pointers to verification
artifacts so downstream skills (`plc-acceptance-validation`,
`plc-traceability-matrix`) can cite them. They do NOT satisfy PLC
requirements, replace formal acceptance review, or replace deterministic
release validation (qualified scanners, evidence generation, policy
enforcement via LaunchAPI, TAVA, nSpect, ScanSpect, SonarQube, Coverity,
BlackDuck, OSRB). Your work must still go through the governed PLC release
process.
```

Do not soften, abridge, or relabel this disclaimer. Stronger wording requires explicit, recorded owner approval per `plc-security-scan/SKILL.md` → *Owner approval record*.

---

## Additional Resources

- Evidence type catalog: [evidence-types.md](assets/evidence-types.md)
- Bundled CLI: [scripts/plc_evidence_ingest.py](scripts/plc_evidence_ingest.py)
- Upstream contract: `plc-security-scan/SKILL.md` → *Evidence Collection* (schema this skill extends)
- Reference writer pattern: `plc-security-scan/scripts/plc_security_scan.py` → `base_record()`, `append_evidence()`
- Downstream consumers: `plc-acceptance-validation/SKILL.md`, `plc-traceability-matrix/SKILL.md`
- Advisory wording contract: `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy*
