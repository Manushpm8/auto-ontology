---
name: plc-workspace-profiling
description: Profile or discover a codebase or multi-repo workspace for agentic PLC, SDLC, or software development workflows. Use when the user asks to profile, discover, or optimize this codebase or workspace for NVIDIA PLC L1, agentic PLC, SDLC, or agentic software development. Interviews for SRD, SADD, SUTP, nSpect registration, and repo/workspace shape, then persists advisory metadata in .plc/profile.json.
---

# PLC Workspace Profiling

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-workspace-profiling","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-workspace-profiling"}}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-workspace-profiling","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-workspace-profiling"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to create or refresh durable Agentic PLC project metadata at `.plc/profile.json`.

Trigger examples:

- "Profile this codebase for agentic PLC."
- "Discover this workspace for agentic SDLC."
- "Optimize this codebase for agentic software development."
- "Profile this workspace for PLC L1."

## Hard Rules

- Treat the profile as advisory developer metadata. It does not prove PLC compliance or replace nSpect, Pulse, ScanSpect, LaunchAPI, TAVA, SRD/SADD/SUTP approval, or release gates.
- Do not invent artifact locations, nSpect IDs, repo membership, requirements, design claims, or traceability.
- Do not store secrets, credentials, access tokens, or raw private cookies in `.plc/profile.json`.
- Redact token-like URL query values before persisting pointers.
- Persist stable orientation metadata only; `.plc/profile.json` is not a continuously refreshed inventory.
- Do not persist generated file lists. Use `aplc profile inspect` to rescan current files on demand.
- Do not claim requirement-to-code traceability.
- Preserve unrelated user changes. If `.gitignore` already has user-specific policy, make the minimum edit needed to track `.plc/profile.json`.

## Workflow

1. Inspect before interviewing.
   - Look for `agentic_plc/workspace.json` at the current root or parent workspace root.
   - Run `aplc profile inspect --repo-root .` when available, or manually inspect repo metadata, build roots, docs roots, test roots, and existing `.plc` files.
   - In workspace mode, inspect each accessible `repos[]` entry from `agentic_plc/workspace.json`.
   - Treat inspect output as transient. Use it to choose stable roots and entrypoints, not to copy file inventories into `.plc/profile.json`.

2. Interview the user for PLC artifacts.
   - Ask where the SRD, SADD, and SUTP come from.
   - Supported `source_type` values: `local`, `confluence`, `pdf`, `markdown`, `url`, `google_doc`, `perforce`, `unknown`.
   - Google Docs pointers must be a stable URL or document ID, with no credentials.
   - Perforce pointers may be depot paths such as `//depot/docs/srd.md` or workspace-local paths.
   - Mark unavailable artifacts as `missing`; use `unknown` only when the user does not know yet.

3. Interview for PLC project identity.
   - Ask whether the user has an nSpect registration ID. Store it as `nspect_registration_id`; use `null` when absent or unknown.
   - Ask for a project/program ID only if it is useful and not discoverable from existing profile/workspace metadata.

4. Confirm repo shape.
   - Single repo: write this repo's `.plc/profile.json` with `profile_type: "repo"`.
   - Multi-repo workspace: ensure each accessible repo has its own `.plc/profile.json`, then write the workspace root `.plc/profile.json` with `profile_type: "workspace"`.
   - Use `agentic_plc/workspace.json` as the source of truth for workspace repo membership.

5. Validate and record.
   - Run `aplc profile validate --profile .plc/profile.json` for single-repo profiles.
   - Run `aplc profile workspace --workspace-root <workspace-root>` to aggregate a workspace profile, then validate the written workspace-root `.plc/profile.json`.
   - Record advisory completion when profile work finishes:

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"requirements","check":"workspace-profile","tool":"plc-workspace-profiling","status":"PASS","runtime_decision":"allow","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"workspace-profile","tool":"plc-workspace-profiling","status":"PASS","runtime_decision":"allow","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```

Use `pillar: "requirements"`, `check: "workspace-profile"`, `tool: "plc-workspace-profiling"`, and `status: "pass"` when profiling completed without unresolved blocking issues. Use `status: "warn"` when artifacts or repo profiles remain missing.

## Profile State

Canonical path: `.plc/profile.json`.

Schema version: `plc-workspace-profile/v1`.

Repo profile:

```json
{
  "schema_version": "plc-workspace-profile/v1",
  "profile_type": "repo",
  "generated_at": "2026-06-02T00:00:00Z",
  "repo": {
    "repo_id": "runtime",
    "root": ".",
    "scm": "git | p4 | none",
    "remote": "string-or-null",
    "branch": "string",
    "revision": "string"
  },
  "plc_project": {
    "project_id": "string-or-null",
    "nspect_registration_id": "string-or-null",
    "workspace_id": "string-or-null"
  },
  "artifacts": {
    "srd": {"status": "available | missing | unknown", "source_type": "local | confluence | pdf | markdown | url | google_doc | perforce | unknown", "pointer": "string-or-null"},
    "sadd": {"status": "available | missing | unknown", "source_type": "local | confluence | pdf | markdown | url | google_doc | perforce | unknown", "pointer": "string-or-null"},
    "sutp": {"status": "available | missing | unknown", "source_type": "local | confluence | pdf | markdown | url | google_doc | perforce | unknown", "pointer": "string-or-null"}
  },
  "repo_layout": {
    "source_roots": ["src"],
    "test_roots": ["tests"],
    "docs_roots": ["docs"],
    "build_entrypoints": ["pyproject.toml"],
    "test_entrypoints": ["pytest"],
    "plc_profile_path": ".plc/profile.json"
  },
  "orientation_hints": {
    "repo_role": "runtime",
    "owned_components": ["request handling"],
    "integration_points": ["control-plane"],
    "open_questions": ["string"]
  }
}
```

Workspace profile is also written to `.plc/profile.json` at the workspace root. It uses `profile_type: "workspace"` and contains `workspace`, `plc_project`, `artifacts`, and `repos[]` entries. Each `repos[]` item records `repo_id`, workspace-relative `path`, `url`, `profile` such as `repos/runtime/.plc/profile.json`, and `status` as `profiled`, `missing`, or `inaccessible`. Do not copy transient per-repo file discoveries into the workspace profile; rescan current files on demand during later authoring, validation, or traceability work.

## Gitignore Policy

`.plc/profile.json` is committed project metadata. Reconstructed SRDs under `.plc/requirements/*.md` and reconstructed SDDs under `.plc/design/*.md` are also committed project metadata. Local security scan artifacts under `.plc/security/` are reviewable advisory evidence and must not be hidden by a broad `.plc/*` ignore. Other `.plc` runtime/cache content should usually stay ignored.

Recommended `.gitignore` shape:

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

If a repo already has a blanket `.plc` or `.plc/` ignore, replace it with the pattern above before adding `.plc/profile.json`, `.plc/requirements/*.md`, `.plc/design/*.md`, or `.plc/security/` artifacts. Do not unignore `.plc/tools/`, `.plc/telemetry/`, local scanner caches, evidence logs, or other runtime state unless the user explicitly requests it.

## Output Expectations

Finish with a concise summary that includes:

- Profile path(s) written.
- Artifact availability for SRD, SADD, and SUTP.
- nSpect registration ID status.
- Repos profiled and any missing/inaccessible repos.
- Validation command results or the exact reason validation was skipped.
