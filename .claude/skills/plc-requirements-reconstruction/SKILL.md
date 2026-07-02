---
name: plc-requirements-reconstruction
description: Skill to reconstruct a Software Requirements Document (SRD) for an existing project, when the user says "Reconstruct the software requirements document for this project", sourcing data from Confluence, Jama, Google Docs, local documents, codebases along with user input to refine and shape the document.
---

# PLC Requirements Reconstruction

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-requirements-reconstruction","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-requirements-reconstruction"}}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-requirements-reconstruction","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-requirements-reconstruction"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to reconstruct a full NVIDIA PLC-L1 Software Requirements Document (SRD) from confirmed source material and explicit user interviews.

## Resources

- [srd_doc_template.md](assets/srd_doc_template.md) is the required SRD structure. Use it as the output skeleton.
- [EARS_reference.md](assets/EARS_reference.md) is the required requirement syntax reference. Every requirement row must follow the EARS (Easy Approach to Requirements Syntax) patterns.

## Hard Rules

- Do not independently assume requirements, project scope, source documents, repo coverage, reviewers, approvers, constraints, dependencies, or traceability, only draft and confirm with the user.
- Interview explicitly. If a section, source, repo boundary, requirement, threshold, platform, or verification criterion is unclear, ask the user and record the answer or an open question.
- Treat this output as an advisory reconstructed SRD draft. Do not claim PLC approval, release-gate satisfaction, or owner signoff.
- Do not print secrets or persist credentials, cookies, access tokens, or private MCP authentication material.
- Ignore the `Reviewers` section when writing the reconstructed SRD. Do not copy the template-maintained appendix into the project SRD.
- Reconstructed SRDs are committed project work products. They belong under `.plc/requirements/`, not ignored scratch state.

## Output Location

Write the final SRD to:

```text
.plc/requirements/SWE-PLC-L1-002-BasicPLC-SRD-<PROJECT-OR-FEATURE-NAME>.md
```

Derive `<PROJECT-OR-FEATURE-NAME>` from the confirmed project or feature name. Use uppercase ASCII letters and digits, replace runs of non-alphanumeric characters with `-`, and trim leading or trailing `-`. Create `.plc/requirements/` if needed. If the name cannot be derived confidently, ask the user before writing.

After writing the SRD, update `.plc/profile.json` so `artifacts.srd` is current:

```json
{
  "status": "available",
  "source_type": "markdown",
  "pointer": ".plc/requirements/SWE-PLC-L1-002-BasicPLC-SRD-<PROJECT-OR-FEATURE-NAME>.md"
}
```

Preserve unrelated profile fields and write the JSON atomically.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Workflow

Track this checklist during the work:

```markdown
Reconstruction Progress:
- [ ] Step 1: Profile preflight
- [ ] Step 2: Project and workspace scope interview
- [ ] Step 3: Source interview and document retrieval
- [ ] Step 4: SRD template section interview
- [ ] Step 5: Requirement type interview and EARS drafting
- [ ] Step 6: User refinement and explicit confirmation
- [ ] Step 7: Write SRD and update profile
- [ ] Step 8: Record advisory completion event
```

### Step 1 - Profile Preflight

Read `.plc/profile.json` before interviewing.

- If `.plc/profile.json` is missing, invalid JSON, or lacks usable artifact and repo-shape fields, run `plc-workspace-profiling` first.
- If `agentic_plc/workspace.json` exists, read it before the scope interview.
- Report what the profile says about `profile_type`, project ID, nSpect registration ID, SRD/SADD/SUTP artifacts, repo layout, and workspace repos.
- Do not proceed to reconstruction until the profile exists or the user explicitly confirms a skip reason. If skipped, record the skip reason in the SRD's assumptions or open questions.

### Step 2 - Project and Workspace Scope Interview

Ask whether the SRD covers a single repo or a whole project containing multiple repos. The default expectation is that an SRD covers the whole project, and the project may consist of multiple repos.

For a multi-repo or workspace SRD, ask and confirm:

- Workspace root and whether `agentic_plc/workspace.json` is authoritative.
- Every repo in scope, including repo ID, path or URL, role, owned components, and revision/baseline if known.
- Interfaces and dependencies between repos.
- Components or repos intentionally out of scope.
- How each requirement should identify applicable repo coverage: whole project, named repo(s), component, interface, or platform.

For a single-repo SRD, still ask whether there are external repos, services, firmware, tools, or test harnesses that the SRD must cover or reference.

### Step 3 - Source Interview and Retrieval

Always ask the user where requirement source material lives. This information is not guaranteed to be in the codebase.

Supported sources include:

- Confluence pages or spaces.
- Jama projects, baselines, filters, folders, or item IDs.
- Google Docs or Google Drive documents.
- Local Markdown, PDF, DOCX, or exported documents.
- Perforce depot paths or workspace-local documents.
- Jira, NVBugs, customer asks, standards, architecture notes, pasted excerpts, or meeting notes.

Use MCP servers only after the user provides source pointers:

- For Confluence, use the available Confluence MCP server.
- For Google Docs, Google Drive, or SharePoint, use the available Glean/GDrive MCP path.
- For Jama, use the available Jama MCP server.
- For Perforce, use the available Perforce MCP server when configured.

If an MCP server is unavailable or unauthenticated, state the limitation and ask for an export, local file, or pasted excerpts. Do not continue as if external content was read.

Record every source used with path, URL, title, revision, baseline, or user-provided excerpt label. Record inaccessible sources and why they were skipped.

### Step 4 - SRD Template Section Interview

Use `assets/srd_doc_template.md` as the section structure. Interview for every project SRD section except `Reviewers` and the template-maintained appendix.

Confirm at minimum:

- Documentation Control: title, authors, revision date, state, review tool, template revision.
- Approvers: approver names and notes if known; otherwise mark as open.
- Revision History: add a row that says the output was reconstructed by Agentic PLC from interviewed and sourced material.
- Introduction: overview, problem solved, main functions, stakeholders, developing team.
- Assumptions, constraints, dependencies, definitions, acronyms, abbreviations, and references.
- Use cases.
- Requirements table format. Use the enhanced format when the user confirms GPU Driver release applicability; otherwise use the four-column SRD requirement table.
- Test Automatability content and security implications.

Do not leave template placeholders in the final SRD. Replace unknown content with explicit open questions or "Not applicable" only after the user confirms that classification.

### Step 5 - Requirement Type Interview and EARS Drafting

Interview for each PLC requirement type and explicitly ask whether it applies:

- Functional
- System or other non-functional
- Interface
- Safety
- KPI
- Platform
- Security
- Legal and Standards
- Telemetry
- Backward Compatibility
- Virtualization
- Test Automatability

For every requirement, produce:

- ID
- Title
- Description
- Type
- Source or rationale
- Applicable repo/component/platform scope when the SRD covers multiple repos
- Verification criteria, or the enhanced-format verification fields when applicable

Requirement descriptions must follow EARS syntax from `assets/EARS_reference.md`:

- Ubiquitous: `The <system name> shall <system response>.`
- State-driven: `While <precondition>, the <system name> shall <system response>.`
- Event-driven: `When <trigger>, the <system name> shall <system response>.`
- Optional feature: `Where <feature is included>, the <system name> shall <system response>.`
- Unwanted behavior: `If <trigger>, then the <system name> shall <system response>.`
- Complex: combine EARS clauses in the order defined by the reference.

Use `shall`, active voice, one obligation per requirement, measurable thresholds where needed, and defined system/component names. Split any requirement that contains multiple obligations, triggers, states, platforms, or verification targets.

### Step 6 - User Refinement and Explicit Confirmation

Work iteratively:

- Present section drafts in manageable chunks.
- Ask the user to confirm source interpretation, project scope, multi-repo coverage, assumptions, requirement type applicability, EARS wording, and verification criteria.
- Revise until the user explicitly confirms the reconstructed SRD is accurate enough to write.

If the user asks for best-effort output before all questions are resolved, write only after listing unresolved questions and receiving confirmation to proceed with those open items in the SRD.

### Step 7 - Write SRD and Update Profile

Write the final SRD at the required `.plc/requirements/SWE-PLC-L1-002-BasicPLC-SRD-<PROJECT-OR-FEATURE-NAME>.md` path.

Before writing, ensure the repo `.gitignore` allows reconstructed SRDs to be committed. The recommended shape is:

```gitignore
.plc/*
!.plc/
!.plc/profile.json
!.plc/requirements/
!.plc/requirements/*.md
!.plc/security/
!.plc/security/**
.plc/tools/
.plc/telemetry/
plc-evidence.jsonl
```

If `.gitignore` has a blanket `.plc` or `.plc/` ignore, minimally update it so `.plc/profile.json`, `.plc/requirements/*.md`, and reviewable `.plc/security/` artifacts are unignored while tool and telemetry state remain ignored.

Update `.plc/profile.json` `artifacts.srd` to point to the reconstructed SRD. Preserve all other fields. Validate the profile with `aplc profile validate --profile .plc/profile.json` when `aplc` is available.

### Step 8 - Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` for a completed reconstructed SRD with no material gaps, `WARN` when assumptions or open questions remain, `SKIP` with `skip_reason` when the skill could not run, and `ERROR` with `error_reason` for unexpected failures. Keep the event compact and omit secrets.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"requirements","check":"plc-requirements-reconstruction","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-requirements-reconstruction","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
