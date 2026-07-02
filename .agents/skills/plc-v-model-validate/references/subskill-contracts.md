# Sub-skill Contracts

Use this reference to normalize interaction with existing PLC skills. Do not copy their full instructions into this skill.

## Loading Order

1. Prefer release-relative sibling paths when the skill bundle contains them:
   - `../plc-requirements-validation/SKILL.md`
   - `../plc-design-validation/SKILL.md`
   - `../plc-security-scan/SKILL.md`
   - `../plc-acceptance-validation/SKILL.md`
   - `../plc-traceability-matrix/SKILL.md`
2. If sibling paths are unavailable, invoke the skill by name.
3. If a required sub-skill is unavailable, record `SKIP` with reason `subskill_not_available` unless the phase was attempted and failed, in which case record `ERROR`.

## plc-requirements-validation

Use when an SRD is available.

Pass through:

- repo root
- SRD path/URL
- SDD/SADD path/URL when available
- change summary when available
- output directory when specified by the user

Capture:

- status
- report path
- finding counts or a concise finding summary
- missing inputs, blocked connectors, or parsing failures

## plc-design-validation

Use when an SDD/SADD is available.

Pass through:

- repo root
- SDD/SADD path/URL
- changed files when available
- SRD path/URL when available for traceability context
- output directory when specified by the user

Capture:

- status
- report path
- finding counts or a concise finding summary
- code/design drift notes
- missing inputs, blocked connectors, or parsing failures

## plc-security-scan

Use before commit or PR handoff.

Pass through:

- repo root
- changed files when available
- output directory when specified by the user

Capture:

- status
- report path or `plc-evidence.jsonl`
- unavailable tools and precise skip reasons
- error reasons separated from expected sandbox/network limitations

Do not duplicate secret details from the security scan into the V-model summary.

- Preserve the local-advisory authority wording when surfacing status. Do not relabel a local PASS as release-gate or compliance-equivalent. Follow the rules in `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy* when rendering scan status in any V-model report.

## plc-acceptance-validation

Use after `plc-security-scan` so the evidence log is available for status resolution. Skip if no feature brief or SRD with `## Acceptance Criteria` table can be located.

Pass through:

- repo root
- feature brief path (preferred) or SRD path that contains the AC table
- test plan path when available (`agentic_plc/work_products/<feature-name>/test-plan.md` in workspace mode, `.plc/briefs/<feature-name>-test-plan.md` in single-repo mode, or canonical STP)
- evidence log path when available (`agentic_plc/work_products/<feature-name>/evidence.jsonl` in workspace mode, `evidence_path` produced this run, or pre-existing `plc-evidence.jsonl`)
- changed files when available
- output directory when specified by the user

Capture:

- status (`PASS` only when no FAIL and no Critical-severity MISSING ACs)
- report path (default `agentic_plc/work_products/<feature-name>/acceptance-validation.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-acceptance-validation.md`)
- counts per AC status (PASS / FAIL / UNVERIFIED / MISSING / N/A) by severity
- a short list of any Critical findings to surface in *Top Risks*
- missing inputs, parsing failures, or unresolved pointers

Do not invent verification pointers. For an AC with no test pointing at it, the sub-skill returns UNVERIFIED, never PASS — surface that wording verbatim in the V-model summary.

## plc-traceability-matrix

Use after `plc-acceptance-validation` so the *Coverage Summary* in the V-model summary can place per-AC status and per-Req coverage side by side. Skip if no feature brief with FR/NFR tables can be located.

Pass through:

- repo root
- feature brief path (the slim Traceability table inside the brief is consumed as a hint)
- test plan path when available (matrix reads the *Requirement Coverage* table for TC IDs)
- evidence log path when available (matrix uses `timestamp` + `commit` to populate the Last verified column and apply the freshness check)
- changed files when available
- output directory when specified by the user

Capture:

- status (`PASS` when zero `GAP` rows; `WARN` when any `PARTIAL` or `GAP` rows exist)
- report path (default `agentic_plc/work_products/<feature-name>/traceability.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-traceability.md`)
- counts per Req-row status (`COVERED` / `PARTIAL` / `GAP`)
- a short list of any `GAP` rows to surface in *Top Risks*
- missing inputs, parsing failures, or unresolved pointers

Do not invent linkages. A requirement with no code pointer is `GAP`, regardless of test presence — surface that wording verbatim in the V-model summary.
