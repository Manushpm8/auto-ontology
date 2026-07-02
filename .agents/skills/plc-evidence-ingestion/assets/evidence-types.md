# PLC Evidence Types

Eight evidence types are accepted by `plc-evidence-ingestion`. The skill rejects unknown values for `check` — every row in `plc-evidence.jsonl` written by this skill carries one of these exact strings.

The required-fields table below is the contract. The CLI ([scripts/plc_evidence_ingest.py](../scripts/plc_evidence_ingest.py)) enforces it; agents hand-writing JSONL lines must enforce it themselves.

---

## Type Catalog

| `check` | When to use | `tool` examples | Pointer (required) | Status/decision (required) | Other recommended fields |
|---|---|---|---|---|---|
| `jenkins-build` | A Jenkins/Blossom CI build run that exercised the requirement or AC | `jenkins`, `blossom` | `url` | `status` ∈ {`green`, `red`, `aborted`} | `notes`, `tc_id` |
| `perf-report` | A performance/throughput/latency run with a numeric result against a threshold | `locust`, `wrk`, `nvbench`, `internal-perf-rig` | `url` *or* `path` | `status` ∈ {`pass`, `fail`, `partial`} | `notes` (one-line metric vs threshold), `tc_id` |
| `accuracy-report` | Accuracy / model-quality run with a numeric result against a baseline | `nemo-eval`, `mlperf`, `internal-accuracy-rig` | `url` *or* `path` | `status` ∈ {`pass`, `fail`, `partial`} | `notes` (delta vs baseline), `tc_id` |
| `manual-signoff` | A human reviewer accepted/rejected the implementation against the requirement | `manual` | none required (URL/path optional) | `decision` ∈ {`accepted`, `rejected`, `pending`} | `reviewer` (REQUIRED), `notes` |
| `scanspect-run` | A ScanSpect scan run | `scanspect` | `external_id` (e.g., `SS-12345`) *or* `url` | `status` ∈ {`clean`, `findings`, `error`, `skip`} | `notes`, `tc_id` |
| `nspect-result` | An nSpect compliance check result | `nspect` | `external_id` (e.g., `NS-789`) *or* `url` | `status` ∈ {`pass`, `warn`, `critical`, `skip`} | `notes`, `tc_id` |
| `external-test` | A test run — either from an external test-management system or from a local test runner whose log is checked into the repo | `testrail`, `qtest`, `jama`, `pytest`, `jest`, `go-test`, ... | At least one of `url`, `path`, `external_id` (use `path` for local runner logs per `plc-v-model`) | `status` ∈ {`pass`, `fail`, `blocked`, `skip`} | `notes`, `tc_id`, `reviewer` |
| `other` | Anything that doesn't fit the above — exception path | (free-form, kebab-case) | At least one of `url`, `path`, `external_id` | `status` *or* `decision` (one) | `notes`, `reviewer`, `tc_id` |

`other` is an exception path. Prefer one of the named types — if `other` would be used repeatedly for the same kind of evidence, add a new named type to this catalog instead.

---

## Field-Level Rules

### `status` values

Build/run-shaped evidence uses `status`:

- `green` / `red` / `aborted` — Jenkins-style terminal states.
- `pass` / `fail` / `partial` — generic run states. `partial` is for runs that exercised some assertions but not all (e.g., a perf suite that timed out before the last benchmark).
- `clean` / `findings` — scanner-shaped runs. `clean` means zero findings; `findings` means one or more findings were recorded by the scanner (severity lives in the linked artifact, not this row).
- `warn` / `critical` — compliance-shaped runs. nSpect uses these to map health-score thresholds.
- `error` — the scanner/runner itself failed to complete (not the system under test failing). Distinguished from `fail` so downstream tooling can separate environment errors from real defects.
- `skip` — the run did not happen for an expected, recorded reason (no credentials, no infra, no changed files). Always pair with `notes` explaining why.
- `blocked` — the test could not start because a prerequisite was missing (e.g., external test in TestRail blocked on an upstream test).

### `decision` values

Review-shaped evidence uses `decision`:

- `accepted` — the reviewer signed off the implementation against the cited requirement.
- `rejected` — the reviewer rejected the implementation; ingestion still happens (so the rejection is recorded), but downstream skills will treat the requirement as not satisfied.
- `pending` — the reviewer has acknowledged the request but has not yet decided. Append a new line with `accepted` or `rejected` when the decision is made; downstream skills use the latest line by `timestamp`.

### Mutual exclusivity

A row has **exactly one** of `status` or `decision`. The CLI rejects both. If you genuinely have both kinds of information (e.g., a manual signoff that includes the run result), record two lines: one `manual-signoff` with `decision`, and one `external-test` or `jenkins-build` with `status` and the same `ref`.

### `reviewer`

Required for `manual-signoff`. Use the corp username (e.g., `sgupta`), not the display name. Stable filter keys are more useful than human-readable ones in a long-running log.

For other types, `reviewer` is optional and means "the human who approved/dispatched/witnessed this evidence" — not the same as `decision`. A Jenkins build that ran automatically has no reviewer; a Jenkins build that a release manager kicked off and watched can record their corp username.

### `notes`

≤ 280 chars. One line. No raw test output, no full error stack traces, no source snippets. Suitable content:

- A numeric headline ("p99 21ms vs 25ms budget").
- The specific assertion that failed ("retry budget exceeded at 3 attempts").
- A pointer to the failure mode in the artifact ("see step 4 of console log").
- A short reason for `skip` ("no perf rig available this week").

### `tc_id`

When the evidence pertains to a specific Test Case in `plc-test-plan-authoring`'s output (`TC-3`, `TC-12`), record it here. `plc-acceptance-validation` joins through the test plan's `Verifies` field to fold TC-keyed evidence back to the parent Req/AC.

### `ac_id`

Use only when you want both the parent Req **and** the AC indexed in the same row. Set `ref` to the Req ID (`FR-3`) and `ac_id` to the AC ID (`AC-7`). This is useful when an AC's verification is also evidence for the parent Functional Requirement. Otherwise, set `ref` to the AC ID directly and omit `ac_id`.

### Workspace attribution

When one SRD/SADD maps to multiple source repos, every evidence, finding, or traceability record that points into a repo must carry enough context to identify the exact repo state:

- `workspace_id` or `workspace_root`
- `repo_id` or `repo_url`
- `repo_revision` (commit SHA or equivalent revision)
- `path` when the record points at a file
- `ref` for requirement/AC records, or `finding_id` for finding records

The CLI emits these fields from explicit flags, `AGENTIC_PLC_*` environment variables, or a discovered `agentic_plc/workspace.json`. Single-repo rows may omit them and retain the historical schema.

---

## Pointer Rules

### `url`

- Must start with `http://` or `https://`.
- Must not embed credentials (`https://user:pass@host/path` is rejected; strip and warn).
- Query strings are allowed; tokens inside query strings are stripped (parameter name kept, value blanked: `?token=` not `?token=abc123`).

### `path`

- Repo-relative. No leading `/`, no `..` segments, no drive letter (`C:\...`).
- Must resolve inside the repo working tree (resolved path's prefix matches the repo root).
- The file does not need to exist at write time — the skill records the pointer even when the artifact is generated later (e.g., a perf report path the run will write to). Downstream skills check existence themselves.

### `external_id`

- Non-empty string.
- No whitespace.
- No slashes.
- Common vendor shapes: `SS-*` (ScanSpect), `NS-*` (nSpect), `TR-*` (TestRail), `JIRA-PROJ-N`, etc.
- Free-form acceptable for `other`, but ask the developer to confirm the value before writing.

---

## Provenance Fields

Every row written by the CLI carries three provenance fields that identify who produced the row and under what context:

### `producer`

One of:

- `"local"` — the agentic-plc skills running in this repo wrote the row. This is the default when `--producer` is not supplied.
- `"external"` — an upstream pipeline (e.g., the DeepStream Autonomous Bug-Fix Framework) wrote the row on our behalf via an external pipeline integration.

### `producer_id`

Pipeline identifier. Examples:

- `"agentic-plc"` — generic local default when no override is supplied.
- `"agentic-plc-bugfix"` — the `plc-v-model-bug-fix` orchestrator.
- `"deepstream-bugfix-agent"` — the DeepStream pipeline writing through the external integration.

**Required when `producer = "external"`.** Auto-filled to `"agentic-plc"` when `producer = "local"` and not supplied.

### `producer_context`

Repo and commit context where the row was produced. Convention: `"<repo>@<sha>"`.

- For local rows: defaults to `"<branch>@<commit>"` from the current worktree (e.g., `"feat/rate-limit@abc1234..."`).
- For external rows: must identify the upstream pipeline's repo and commit (e.g., `"DeepStreamSDK/Autonomous-BugFix-Framework@abc1234"`).

**Required when `producer = "external"`.**

### Trust tiers

Downstream PLC consumers (`plc-acceptance-validation`, `plc-traceability-matrix`, the validate flow) treat rows differently based on `producer`:

- **Local** rows are treated as attested PLC evidence.
- **External** rows are treated as advisory PLC evidence — surfaced with a provenance annotation in the *Last verified* column / Coverage Summary, but not rejected.

Legacy rows (written before the provenance fields were added) carry none of these fields. Downstream readers treat an absent `producer` as equivalent to `"local"` with unknown id and context.

---

## Examples by Type

> The examples below elide the `producer` / `producer_id` / `producer_context` fields for readability. See *Provenance Fields* above for the full schema. The example with all fields populated lives in [`SKILL.md` → *Example Lines*](../SKILL.md#example-lines).

### `jenkins-build`

```json
{"branch":"feat/rate-limit","check":"jenkins-build","commit":"abc1234def5678901234567890abcdef12345678","notes":"100/min boundary test","pillar":"VERIFICATION","ref":"AC-1","status":"green","timestamp":"2026-05-13T18:30:00Z","tool":"jenkins","url":"https://blossom.nvidia.com/job/rate-limit-ci/123"}
```

### `perf-report`

```json
{"branch":"feat/rate-limit","check":"perf-report","commit":"abc1234def5678901234567890abcdef12345678","notes":"p99 21ms vs 25ms budget","pillar":"VERIFICATION","ref":"NFR-2","status":"pass","tc_id":"TC-12","timestamp":"2026-05-13T18:33:00Z","tool":"locust","url":"https://internal.nvidia.com/perf-runs/abc"}
```

### `accuracy-report`

```json
{"branch":"feat/llm-eval","check":"accuracy-report","commit":"abc1234def5678901234567890abcdef12345678","notes":"mmlu +0.4 vs baseline","path":".plc/briefs/llm-eval-accuracy.html","pillar":"VERIFICATION","ref":"NFR-5","status":"pass","timestamp":"2026-05-13T18:34:00Z","tool":"nemo-eval"}
```

### `manual-signoff`

```json
{"branch":"feat/rate-limit","check":"manual-signoff","commit":"abc1234def5678901234567890abcdef12345678","decision":"accepted","pillar":"VERIFICATION","ref":"AC-5","reviewer":"sgupta","timestamp":"2026-05-13T18:31:00Z","tool":"manual"}
```

### `scanspect-run`

```json
{"branch":"feat/rate-limit","check":"scanspect-run","commit":"abc1234def5678901234567890abcdef12345678","external_id":"SS-12345","pillar":"VERIFICATION","ref":"NFR-3","status":"clean","timestamp":"2026-05-13T18:32:00Z","tool":"scanspect"}
```

### `nspect-result`

```json
{"branch":"feat/rate-limit","check":"nspect-result","commit":"abc1234def5678901234567890abcdef12345678","external_id":"NS-789","pillar":"VERIFICATION","ref":"NFR-7","status":"pass","timestamp":"2026-05-13T18:35:00Z","tool":"nspect"}
```

### `external-test`

External system (TestRail row):

```json
{"branch":"feat/rate-limit","check":"external-test","commit":"abc1234def5678901234567890abcdef12345678","external_id":"TR-4421","pillar":"VERIFICATION","ref":"AC-9","status":"pass","tc_id":"TC-15","timestamp":"2026-05-13T18:36:00Z","tool":"testrail","url":"https://testrail.nvidia.com/index.php?/cases/view/4421"}
```

Local test runner (pytest log checked into the repo):

```json
{"branch":"feat/rate-limit","check":"external-test","commit":"abc1234def5678901234567890abcdef12345678","path":".plc/logs/pytest-rate-limit.log","pillar":"VERIFICATION","ref":"AC-1","status":"pass","timestamp":"2026-05-13T18:36:00Z","tool":"pytest"}
```

Cross-repo workspace row:

```json
{"branch":"feat/rate-limit","check":"external-test","commit":"abc1234def5678901234567890abcdef12345678","path":"tests/rate_limit/test_retry.py","pillar":"VERIFICATION","ref":"AC-1","repo_id":"runtime","repo_revision":"def5678901234567890abcdef1234567890abcd","repo_url":"https://gitlab.example.com/team/runtime.git","status":"pass","timestamp":"2026-05-13T18:36:00Z","tool":"pytest","workspace_id":"rate-limit-workspace"}
```

Cross-repo finding row:

```json
{"branch":"feat/rate-limit","check":"scanspect-run","commit":"abc1234def5678901234567890abcdef12345678","external_id":"SS-12345","finding_id":"SAST-12","path":"src/auth/retry.py","pillar":"VERIFICATION","ref":"NFR-3","repo_id":"control-plane","repo_revision":"0123456789abcdef0123456789abcdef01234567","status":"findings","timestamp":"2026-05-13T18:37:00Z","tool":"scanspect","workspace_id":"rate-limit-workspace"}
```

### `other`

```json
{"branch":"feat/rate-limit","check":"other","commit":"abc1234def5678901234567890abcdef12345678","notes":"customer demo at OEM partner","pillar":"VERIFICATION","ref":"AC-12","reviewer":"sgupta","status":"pass","timestamp":"2026-05-13T18:37:00Z","tool":"customer-demo","url":"https://recording.example.com/demo-5"}
```
