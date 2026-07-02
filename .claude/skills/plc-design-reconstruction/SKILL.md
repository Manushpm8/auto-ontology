---
name: plc-design-reconstruction
description: Skill to reconstruct a Software Design Document (SDD or SADD) for an existing project, when the user says "Reconstruct the software design document for this project", asks to rebuild an SDD or SADD from an SRD, source code, infrastructure, and interviews, or needs a project-level design document covering one or more repos.
---

# PLC Design Reconstruction

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"design","check":"plc-skill-invocation","tool":"plc-design-reconstruction","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-design-reconstruction"}}
JSON
```

```powershell
@'
{"pillar":"design","check":"plc-skill-invocation","tool":"plc-design-reconstruction","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-design-reconstruction"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to reconstruct a full NVIDIA PLC-L1 Software Design Document (SDD/SADD) from a reconstructed SRD, codebase inspection, infrastructure inspection, source artifacts, and explicit user interviews.

## Resources

- [sdd_doc_template.md](assets/sdd_doc_template.md) is the required SDD/SADD structure. Use it as the output skeleton.

Required inspection topics include entry points, data interfaces, APIs, security AuthN/AuthZ, operational patterns, logging standards, helm charts, Dockerfiles, and other deployment infrastructure.

## Hard Rules

- Do not independently assume architecture, design decisions, repo scope, APIs, data flows, security controls, operational behavior, reviewers, approvers, dependencies, or traceability, only draft and confirm with the user.
- Interview explicitly. If any section, interface, component boundary, security behavior, operational pattern, deployment artifact, logging convention, or design rationale is unclear, ask the user and record the answer or an open question.
- Treat this output as an advisory reconstructed SDD draft. Do not claim PLC approval, release-gate satisfaction, or owner signoff.
- Do not print secrets or persist credentials, cookies, access tokens, private MCP authentication material, or secret values found in config files.
- Ignore the `Reviewers` section when writing the reconstructed SDD. Do not copy the template-maintained appendix into the project SDD.
- Reconstructed SDDs are committed project work products. They belong under `.plc/design/`, not ignored scratch state.

## Prerequisites

1. The workspace must be profiled with `plc-workspace-profiling`.
2. Ensure the `.plc/profile.json` points to a Software Requirements Document (SRD) is present (either as a local file or via Jama requirements).

If either prerequisite is missing, run the missing skill first. Do not continue as though the profile or SRD exists.

## Output Location

Write the final SDD to:

```text
.plc/design/SWE-PLC-L1-003-BasicPLC-SADD-<PROJECT-OR-FEATURE-NAME>.md
```

Derive `<PROJECT-OR-FEATURE-NAME>` from the confirmed project or feature name. Use uppercase ASCII letters and digits, replace runs of non-alphanumeric characters with `-`, and trim leading or trailing `-`. Create `.plc/design/` if needed. If the name cannot be derived confidently, ask the user before writing.

After writing the SDD, update `.plc/profile.json` so `artifacts.sadd` is current:

```json
{
  "status": "available",
  "source_type": "markdown",
  "pointer": ".plc/design/SWE-PLC-L1-003-BasicPLC-SADD-<PROJECT-OR-FEATURE-NAME>.md"
}
```

Preserve unrelated profile fields and write the JSON atomically.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Workflow

Track this checklist during the work:

```markdown
Design Reconstruction Progress:
- [ ] Step 1: Profile and SRD preflight
- [ ] Step 2: Project and workspace scope confirmation
- [ ] Step 3: Codebase and infrastructure inspection
- [ ] Step 4: SDD template section mapping
- [ ] Step 5: User interview for open questions
- [ ] Step 6: User refinement and explicit confirmation
- [ ] Step 7: Write SDD and update profile
- [ ] Step 8: Record advisory completion event
```

### Step 1 - Profile and SRD Preflight

Read `.plc/profile.json` before interviewing.

- If `.plc/profile.json` is missing, invalid JSON, or lacks usable artifact and repo-shape fields, run `plc-workspace-profiling` first.
- If `artifacts.srd.status` is not `available`, `artifacts.srd.pointer` is missing, or the pointed SRD is not readable when it is a local Markdown path, run `plc-requirements-reconstruction` first.
- If the SRD pointer is external, use the appropriate MCP or ask the user for a local/exported SRD before reconstructing the SDD.
- If `agentic_plc/workspace.json` exists, read it before the scope interview.
- Report what the profile says about `profile_type`, project ID, nSpect registration ID, SRD/SADD/SUTP artifacts, repo layout, and workspace repos.
- Read the reconstructed SRD and extract requirement IDs, requirement types, scope, interfaces, security requirements, platform constraints, test automatability requirements, assumptions, and open questions.

Do not proceed to SDD reconstruction until a profile and reconstructed SRD are available, or the user explicitly confirms a skip reason. If skipped, record the skip reason in the SDD's assumptions or open questions.

### Step 2 - Project and Workspace Scope Confirmation

Ask whether the SDD covers a single repo or a whole project containing multiple repos. The default expectation is that an SDD covers the same whole project scope as the reconstructed SRD, and the project may consist of multiple repos.

For a multi-repo or workspace SDD, ask and confirm:

- Workspace root and whether `agentic_plc/workspace.json` is authoritative.
- Every repo in scope, including repo ID, path or URL, role, owned components, and revision/baseline if known.
- Component ownership and interfaces between repos.
- Repos or components intentionally out of scope.
- How design sections should identify applicable repo, component, interface, platform, or deployment scope.

For a single-repo SDD, still ask whether external repos, services, firmware, tools, test harnesses, or deployment systems must be covered or referenced.

### Step 3 - Codebase and Infrastructure Inspection

Thoroughly inspect the codebase(s) before drafting. Prefer `rg` and `rg --files`; inspect all in-scope repos in a workspace.

Map and record:

- Build and runtime entry points: package manifests, CLIs, service startup files, `main` functions, route registration, workers, init scripts, and generated-code boundaries.
- Component/module structure and ownership.
- Data interfaces: schemas, DTOs, protobufs, OpenAPI specs, database models, queues, streams, files, IPC, RPC, and internal contracts.
- APIs: REST, gRPC, GraphQL, SDK/public APIs, internal APIs, callbacks, webhooks, and versioning behavior.
- Security: AuthN/AuthZ, identity propagation, permissions, token/session handling, secret handling, encryption, trust boundaries, threat model evidence, and abuse prevention.
- Operational patterns: configuration, rollout, feature flags, migrations, health checks, readiness/liveness checks, background jobs, retries, rate limits, backpressure, recovery, and failure modes.
- Logging standards: log levels, structured fields, correlation IDs, PII/secrets redaction, debug hooks, audit logs, metrics, traces, dashboards, and alerting.
- Infrastructure: helm charts, Dockerfiles, compose files, Kubernetes manifests, Terraform, CI/CD config, service accounts, network policies, storage, and deployment topology.
- Tests and automation hooks that inform integration validation and test automation sections.

Never treat inferred findings as final. Mark each inferred design fact as `confirmed by code`, `confirmed by source document`, `confirmed by user`, or `open question`.

### Step 4 - SDD Template Section Mapping

Use `assets/sdd_doc_template.md` as the section structure. Interview for every project SDD section except `Reviewers` and the template-maintained appendix.

Create a section map before drafting:

- Documentation Control, Approvers, and Revision History.
- Introduction: purpose and scope, assumptions, constraints, dependencies, definitions, and references.
- Architectural Details: high-level architecture, static architecture, dynamic architecture, assumptions, limitations, and diagrams when useful.
- Design Details: alternatives, selected design, static design, configuration data, external interfaces, dependencies, and integration validation plan.
- Dynamic Design: functionality and behavior, control flow, data flow, error handling, logging and debugging, state machine, security design, and test automation.
- Other Design Considerations: resource limits, high availability, scalability, and future work.

Add a Revision History row that says the output was reconstructed by Agentic PLC from the reconstructed SRD, codebase/infrastructure inspection, and interviewed material.

### Step 5 - User Interview for Open Questions

Interview the user on all open questions before writing final content. Ask specifically about:

- Design rationale and alternatives considered.
- Architecture diagrams or diagram style preferences.
- Component boundaries and ownership across repos.
- External interface owners, commitments, and integration validation.
- Security controls and threat-model assumptions not obvious in code.
- Operational behavior, rollout, deployment, monitoring, logging, and incident-handling standards.
- Resource limits, high availability, scalability, and future work.
- Any code-inferred design facts that need correction.

Do not collapse multiple unresolved design decisions into a vague assumption. Ask precise questions and record precise answers.

### Step 6 - User Refinement and Explicit Confirmation

Work iteratively:

- Present section drafts in manageable chunks.
- Ask the user to confirm source interpretation, SRD traceability, project scope, multi-repo coverage, code-inferred facts, architecture/design rationale, security design, operational behavior, and open questions.
- Revise until the user explicitly confirms the reconstructed SDD is accurate enough to write.

If the user asks for best-effort output before all questions are resolved, write only after listing unresolved questions and receiving confirmation to proceed with those open items in the SDD.

### Step 7 - Write SDD and Update Profile

Write the final SDD at the required `.plc/design/SWE-PLC-L1-003-BasicPLC-SADD-<PROJECT-OR-FEATURE-NAME>.md` path.

Before writing, ensure the repo `.gitignore` allows reconstructed SRDs and SDDs to be committed. The recommended shape is:

```gitignore
.plc/*
!.plc/
!.plc/profile.json
!.plc/requirements/
!.plc/requirements/*.md
!.plc/design/
!.plc/design/*.md
!.plc/security/
!.plc/security/**
.plc/tools/
.plc/telemetry/
plc-evidence.jsonl
```

If `.gitignore` has a blanket `.plc` or `.plc/` ignore, minimally update it so `.plc/profile.json`, `.plc/requirements/*.md`, `.plc/design/*.md`, and reviewable `.plc/security/` artifacts are unignored while tool and telemetry state remain ignored.

Update `.plc/profile.json` `artifacts.sadd` to point to the reconstructed SDD. Preserve all other fields. Validate the profile with `aplc profile validate --profile .plc/profile.json` when `aplc` is available.

### Step 8 - Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` for a completed reconstructed SDD with no material gaps, `WARN` when assumptions or open questions remain, `SKIP` with `skip_reason` when the skill could not run, and `ERROR` with `error_reason` for unexpected failures. Keep the event compact and omit secrets.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"design","check":"plc-design-reconstruction","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"design","check":"plc-design-reconstruction","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
