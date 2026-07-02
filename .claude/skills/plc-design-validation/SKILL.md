---
name: plc-design-validation
description: >-
  Validate a software design document (SADD/SDD) against PLC guidelines and check
  consistency between design and codebase. Each finding includes confidence, severity,
  and remediation. Use this skill whenever the user asks to review, validate, audit,
  check, or critique a design document, or asks whether a design doc matches the code,
  or asks about PLC compliance for a design doc, even if they don't explicitly say
  "validate". Trigger on phrases like "review this SADD", "review this SDD", "check
  this design doc", "does the code match the design", "audit the design doc",
  "validate SADD", "validate SDD".
---

# PLC Software Architecture and Design Document (SADD) Validation

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"design","check":"plc-skill-invocation","tool":"plc-design-validation","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-design-validation"}}
JSON
```

```powershell
@'
{"pillar":"design","check":"plc-skill-invocation","tool":"plc-design-validation","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-design-validation"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Validate a Software Architecture and Design Document (SADD, also referred to as SDD)
against PLC guidelines and against the actual codebase. Produce a structured markdown
report with three finding groups.

**This skill is advisory.** Findings improve quality and surface gaps early but do NOT
satisfy PLC requirements or replace deterministic release validation.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Workflow

Follow these phases in order. Do not skip phases — each one feeds the next.

### Phase 1: Obtain the SADD

**If invoked by `SKILL.md` Phase 1:** the SADD URL is pre-supplied in the invocation context. Fetch it immediately — do not ask the developer for the location:
1. Confluence URL → `confluence_get_page` MCP tool
2. Google Drive or SharePoint link → `glean_get_file(url=..., system="gdrive")` or `glean_get_file(url=..., system="sharepoint")`
3. Local file path (document just generated, not yet published) → read the file directly

**If invoked directly by a developer:** the user may have already pasted text or pointed at a path — use it. Otherwise, ask for the SADD source (Confluence, Google Docs, SharePoint, Perforce, or local path).

> **Upstream check:** If the SADD is weak or incomplete, the SRD it was derived from may also have gaps. Consider running `plc-requirements-validation` to check SRD completeness and SRD↔SADD consistency before or alongside this skill.

Do **not** use `WebFetch` for the SADD — SADDs are typically internal/auth-gated. Stick to MCP tools or a local path.

If fetching fails for a reason the user can fix (auth, VPN, wrong URL, missing MCP), tell them what you tried and what you'd need. Don't silently fall back without telling them.

Once obtained, save the SADD text to a scratch location you can re-read (e.g., `/tmp/sadd-<short-name>.md`) so later phases can reference specific sections without re-fetching.

### Phase 2: Understand the Codebase

Before comparing anything, build a genuine mental model of the code. The comparison is only as good as this step.


Do these in parallel where possible (use available code exploration tools: file reads, directory listings, glob/grep, or IDE code navigation depending on your agent surface):
- Read the root `README.md`, `CLAUDE.md`, and any top-level docs.
- Map the module layout (directories and their purpose).
- Identify the key entry points (APIs, CLIs, workflows, services).
- Identify config surfaces (env vars, settings files, feature flags).
- Identify external integrations (databases, queues, third-party APIs, other services).
- Identify data flow and control flow through the main paths.

For larger codebases, systematically map the architecture rather than reading every file — read key entry points, top-level module structure, config surfaces, and external integrations first, then drill into relevant areas. Produce a written architectural summary you can reference in later phases.

**Important**: the SADD may span multiple codebases/repos. If the current working directory only covers part of the system, note it — this affects confidence in Phase 4.

### Phase 3 (Group A): SADD vs. PLC Guidelines

Check the SADD against the PLC design guidelines. The guidelines require the SADD to cover these sections:

1. **Proposed Design** — captures the design in detail; describes significant alternatives considered and why they were rejected.
2. **Interface Architecture** — for each interface with other software (internal and external), specifies:
   - Type of interface
   - Operational implications of data transfer, including security considerations
   - Data transfer requirements (content, format, sequence)
   - Data formats for both sending and receiving systems (item names, codes, abbreviations)
   - Interface procedures
   - Interface equipment
   - Data conversion requirements
3. **System KPIs & Metrics** — how requirement KPIs are instrumented and measured.
4. **Security Design** — use and management of integrity and access controls on physical components (schema, sub-schema, partitions, files, records, tables, sets, relations, data elements); security standard (e.g., NIST) or security requirement IDs.
5. **Configure Software to Have Secure Settings by Default** — default settings that reduce compromise risk at install time.
6. **Debugging & Troubleshooting** — debug prints, error messages and their definitions, how to troubleshoot each error, any new tools to be developed.
7. **Logging and Instrumentation** — all logging needs and the definition of each log.
8. **Operational Considerations** — Recoverability, Failure Contingencies, Availability, Capacity, Performance and Timing, Data Retention, Error Handling, Adaptability.

For each guideline, decide:
- **Covered** — section is present and substantive; no finding.
- **Partially covered** — section is present but thin or missing sub-items → finding (severity = medium, usually).
- **Missing** — section absent or effectively empty → finding (severity = high for Security, Interface, Operational; medium for others unless context elevates it).

Don't penalize the SDD for section-naming differences. If "Observability" covers Logging + Metrics substantively, that counts. Judge content, not headings.

### Phase 4 (Group B): Design → Code Inconsistency

Read the SADD end-to-end with the codebase map in hand. For every concrete claim the SADD makes about behavior, structure, interfaces, configuration, or operational properties, ask: *does the code actually reflect this?*

Examples of Group B findings:
- SADD says "all API calls retry with exponential backoff" — grep shows endpoints with no retry wrapper.
- SADD documents an interface spec that the code implements differently (different field names, different sequence).
- SADD says "rate limited at 100 req/s per tenant" — no rate limiter found.
- SADD describes a caching layer — code calls the backend directly.

**Confidence calibration**:
- **High** — you can point to specific code (or its absence in a path you verified) that clearly contradicts the SDD.
- **Medium** — strong signals of a mismatch but some ambiguity remains.
- **Low** — the SDD claim might be implemented elsewhere (another repo, infra-as-code, a service you don't have access to). Default to Low if the claim is about infra/deployment/another service and you can't see that codebase.

Be honest: a Low-confidence finding is still worth flagging, but mark it clearly.

### Phase 5 (Group C): Code → Design Inconsistency

Now the reverse: walk the codebase and identify meaningful behavior, structure, or configuration that the SDD does not capture. Focus on these axes:
- **Architecture** — modules, services, deployment topology, dependencies
- **Usage patterns** — how external callers use the system; auth; rate limits
- **Data flow** — how data enters, is transformed, persisted, emitted
- **Control flow** — key orchestration, retries, fallbacks, error paths
- **Configuration** — env vars, feature flags, tunables that affect behavior
- **Operational surfaces** — logs, metrics, health checks, failure modes

A Group C finding is any meaningful aspect the code embodies that a reader of the SDD would not know about. Not every trivial detail — focus on things a new engineer or operator would want to know.

Don't flag implementation trivia (variable names, helper functions). Flag things that affect behavior, operability, or integration.

### Phase 6: Generate the Report

Write the report as markdown. Save it to a path adjacent to the SADD or to a location the user specifies. Suggested default: `sadd-validation-<short-name>.md` in the current working directory.

Use exactly this structure:

```markdown
# SADD Validation Report: <SADD title>

**SADD source**: <url or path>
**Codebase(s) reviewed**: <paths / repos>
**Date**: <YYYY-MM-DD>

## Summary

<3-6 bullets: headline counts per group, top risks, scope caveats (e.g., "infra repo not reviewed — Group B confidence capped at Medium for deployment claims").>

## Group A — Design Guidelines Compliance

### A1. <Guideline name, e.g., "Security Design">
- **Status**: Missing | Partial | Covered
- **Severity**: High | Medium | Low
- **Confidence**: High | Medium | Low
- **Evidence**: <quote or cite SDD section, or note its absence>
- **Remediation**: <concrete action the author should take>

### A2. ...

## Group B — Design-to-Code Inconsistency

### B1. <short title>
- **SDD claim**: <quote or paraphrase, cite section>
- **Code reality**: <what the code actually does, cite `path/file.py:line`>
- **Severity**: High | Medium | Low
- **Confidence**: High | Medium | Low
- **Remediation**: <fix the code, fix the doc, or clarify scope — pick one and justify>

### B2. ...

## Group C — Code-to-Design Inconsistency

### C1. <short title>
- **Code behavior**: <describe, cite `path/file.py:line`>
- **SDD gap**: <what the SDD doesn't say>
- **Severity**: High | Medium | Low
- **Confidence**: High | Medium | Low
- **Remediation**: <what section of the SDD should be extended and with what>

### C2. ...

## Scope & Caveats

<List repos/areas not reviewed, assumptions made, anything that would change the findings.>
```

**Severity rubric** (apply consistently):
- **High** — security, data integrity, interface contracts, operational failure modes; or missing guideline sections that block PLC sign-off.
- **Medium** — incomplete coverage of behavior a reader would reasonably need; partial guideline coverage.
- **Low** — minor gaps, cosmetic inconsistencies, or low-confidence findings that may dissolve with more context.

**Confidence rubric**:
- **High** — directly verified in this session (quoted SDD text + cited code).
- **Medium** — verified with mild inference.
- **Low** — the claim crosses a boundary (another repo, infra, external service) you couldn't fully inspect.

## Working principles

- **Cite everything.** Every finding should point to a specific SDD section/quote and (for B and C) a specific code path. Ungrounded findings are not useful.
- **Be specific in remediation.** "Add a security section" is weak. "Add a Security Design section covering (a) the auth mechanism for the `/webhook` endpoint, (b) secret storage via X, (c) NIST control references" is useful.
- **Don't invent findings to fill groups.** If Group C has two real findings and Group A has eight, that's fine. A padded report hides the real issues.
- **Default Group B infra claims to Low confidence** unless you can actually see the infra repo.
- **Flag uncertainty explicitly.** If you're unsure whether something is a Group B or Group C finding, say so in the finding itself.
- **This skill is advisory only.** Never claim that passing this validation means PLC compliance. The governed release process (qualified scanners, nSpect, LaunchAPI) remains authoritative.

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` when there are no findings, `WARN` when findings or coverage gaps are reported, `SKIP` with `skip_reason` when required inputs are unavailable, and `ERROR` with `error_reason` for unexpected failures. Keep `summary` to aggregate finding counts only.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"design","check":"plc-design-validation","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0,"findings_high":0}}
JSON
```

```powershell
@'
{"pillar":"design","check":"plc-design-validation","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0,"findings_high":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
