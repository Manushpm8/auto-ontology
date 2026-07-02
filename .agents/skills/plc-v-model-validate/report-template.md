# PLC V-Model Validation Summary

| Field | Value |
|---|---|
| Date | <YYYY-MM-DD> |
| Run ID | <run_id> |
| Repo | <path> |
| Branch | <branch or unknown> |
| Commit | <sha or unknown> |
| Mode | validation |
| Comparison base | <base ref, explicit target, or unknown> |
| Merge base | <sha or N/A> |
| Change summary | <summary or not provided> |
| Source docs used | <SRD/SDD/SADD/STP paths or none> |
| Generated artifacts | <paths or none> |

## Phase Status

Status values in the per-phase table are local advisory. None of these phases satisfy the authoritative PLC release gates listed in the Disclaimer below.

The `Freshness` column reports workflow reuse only, not formal PLC approval. Values: `Rerun` (ran this invocation), `Reused @<sha>` (reused prior report from `.plc/v-model-state.json` at the given commit), or `N/A`.

| Phase | Status | Freshness | Details | Report |
|---|---|---|---|---|
| Requirements | <PASS/WARN/SKIP/ERROR> | <Rerun / Reused @sha / N/A> | <counts or reason> | <path or N/A> |
| Design | <PASS/WARN/SKIP/ERROR> | <Rerun / Reused @sha / N/A> | <counts or reason> | <path or N/A> |
| Code implementation | <PASS/WARN/SKIP/ERROR/N/A> | <Rerun / Reused @sha / N/A> | <notes> | <path or N/A> |
| Verification | <PASS/WARN/SKIP/ERROR/N/A> | <Rerun / Reused @sha / N/A> | <test command, duration, or skip reason> | <path or N/A> |
| Security scan | <PASS/WARN/SKIP/ERROR> (local advisory) | <Rerun / Reused @sha / N/A> | <latest evidence summary> | `plc-evidence.jsonl` or report path |
| Acceptance validation | <PASS/WARN/SKIP/ERROR> | <Rerun / Reused @sha / N/A> | <PASS/FAIL/UNVERIFIED/MISSING counts by severity, or skip reason> | <path or N/A> |
| Traceability matrix | <PASS/WARN/SKIP/ERROR> | <Rerun / Reused @sha / N/A> | <COVERED/PARTIAL/GAP counts, or skip reason> | <path or N/A> |

## Top Risks

1. <risk>

## Next Actions

1. <action>

## Advisory Notes

1. <notable advisory finding or gap>

## Disclaimer

This V-model validation summary is advisory developer feedback. It does not satisfy or replace authoritative PLC release gates, including nSpect, Pulse, ScanSpect, SonarQube, Coverity, BlackDuck, OSRB, LaunchAPI, TAVA, or any team-specific release approval process.
