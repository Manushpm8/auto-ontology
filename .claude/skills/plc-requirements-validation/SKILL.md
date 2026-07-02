---
name: plc-requirements-validation
description: >-
  Validate SRD template compliance and check consistency between SRD and SADD
  documents for PLC. Detects gaps bidirectionally and reports findings with
  severity levels and remediations. Use when checking requirements-to-architecture
  alignment, verifying SRD completeness, or when the user mentions SRD, SADD,
  requirements validation, PLC document review, or requirements-design consistency.
---

# PLC Requirements Validation

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-requirements-validation","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-requirements-validation"}}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-requirements-validation","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-requirements-validation"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Validates that an SRD meets PLC-L1 template expectations and that SRD requirements are
consistent with the SADD. Reports findings bidirectionally with severity and remediations.

**This skill is advisory.** Findings improve quality and surface gaps early but do NOT
satisfy PLC requirements or replace deterministic release validation.

---

## Background: SRD, SADD, and How They Fit Together

### SRD — Software Requirements Document

The SRD defines **what** the software must do. It is the first PLC-L1 work product created,
capturing the project's purpose, use cases, and the full set of requirements (functional,
non-functional, interface, security, KPI, platform, etc.). It serves as the contract between
product/architecture, engineering, QA, and security teams.

The SRD must be reviewed and approved by the PLC-PIC, Product Owner, or Functional Manager,
and registered in nSpect under the program's Lifecycle & Documentation page.

### SADD — Software Architecture and Design Document

The SADD defines **how** SRD requirements will be implemented. It describes the architecture,
components, interfaces, data flows, design alternatives, and key decisions. A SADD explicitly
references the SRD it realizes, ideally tracing design elements back to requirement IDs.

### How They Relate in the PLC Process

```
SRD (what to build)
  │
  │  Requirements trace forward to design
  ▼
SADD (how to build it)
  │
  │  Design traces forward to implementation
  ▼
Code (the implementation)
```

1. The SRD is drafted and reviewed until requirements are signed off.
2. The SADD is drafted referencing those SRD requirements, showing how each is satisfied.
3. Together they create a traceability chain: **Requirements → Architecture → Code → Tests**.

This skill validates the SRD → SADD link in that chain. The `plc-design-validation` skill handles SADD → Code.

---

## Prerequisites

- Access to the project's SRD document (required)
- Access to the project's SADD document (optional — if not available, Steps 3 and 4 are skipped and the report covers SRD template compliance only)
- Documents must be readable (PDF, HTML, doc, md, or any text-extractable format)

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

---

## Workflow

Copy this checklist and track progress:

```
Validation Progress:
- [ ] Step 1: Obtain SRD (and SADD if available)
- [ ] Step 2: SRD template compliance check
- [ ] Step 3: Cross-validate SRD → SADD  (skip if no SADD)
- [ ] Step 4: Cross-validate SADD → SRD  (skip if no SADD)
- [ ] Step 5: Generate report
```

### Step 1 — Obtain Documents

**If invoked by `SKILL.md` Phase 1:** document URLs are pre-supplied in the invocation context. Use them directly — do not ask the developer for locations. Fetch immediately:
1. SRD URL → `confluence_get_page(page_id=...)` or `glean_get_file(url=..., system=<datasource>)`
2. SADD URL (if provided) → same approach

**If invoked directly by a developer:** ask for the SRD and SADD locations.

**Fetching strategy:**
1. Confluence URL → `confluence_get_page` MCP tool
2. Google Drive or SharePoint link → `glean_get_file(url=..., system="gdrive")` or `glean_get_file(url=..., system="sharepoint")` via the Glean MCP tool
3. Local path → read the file directly

Accept any readable format. Do not require a specific format or structure.

**After obtaining both documents:**
- Read them thoroughly before proceeding
- Identify how the SRD organizes its content (sections, headings, tables)
- Identify how requirements are structured (IDs, tables, lists)
- Identify how the SADD references or traces to SRD requirements
- Ask the developer what project type this is (GPU Driver, Cloud Service, General) —
  this affects whether Test Automatability is mandatory

### Step 2 — SRD Template Compliance (Findings Group A)

Check the SRD against the expected content areas defined in [srd-sections.md](assets/srd-sections.md).

**How to check:**
- For each area in `assets/srd-sections.md`, determine whether the SRD contains content
  that matches the area's **purpose and content indicators**
- Do NOT require exact section names — match semantically by content and intent
- Use the "Example headings" as hints, not as mandatory matches
- A section counts as present if its content indicators are substantially covered,
  regardless of how the document is organized
- Mark as "Incomplete" if the area is partially covered but missing key content indicators

**For each missing or incomplete area, create a finding:**
- Assign severity per `assets/srd-sections.md` (Critical / Warning / Info)
- Describe what is missing and why it matters
- Suggest specific remediation

**Special handling — Requirement Type Coverage (Area 10):**
- Extract all requirement types present in the SRD's requirements table
- Compare against the 13 PLC requirement types listed in `assets/srd-sections.md`
- Flag any types not represented and not explicitly marked as N/A
- Security, Functional, and Platform types are especially important

**Special handling — Test Automatability (Area 11):**
- Only flag as Critical for GPU Driver projects
- For other project types, flag as Warning if missing

### Step 3 — Cross-Validate SRD → SADD (Findings Group B)

> **Skip this step if no SADD was provided.** Note in the report: "Bidirectional consistency check skipped — no SADD available. Recommend creating an SADD before implementation."

For each requirement in the SRD, check whether the SADD provides architecture or
design coverage for it.

**How to check:**
- If the SRD has structured requirement IDs (REQ-1, REQ-2, etc.), check each by ID
- If the SADD explicitly references SRD requirement IDs, use those traces
- If there are no explicit ID references, match by **topic and intent** — does the SADD
  describe architecture/design that addresses what the requirement asks for?
- A requirement is "covered" if the SADD describes how it will be realized through
  architecture, components, interfaces, data flows, or design decisions

**For each uncovered requirement, create a finding:**
- Critical: Functional or Security requirements without SADD coverage
- Warning: Non-functional, KPI, or Platform requirements without coverage
- Info: Low-priority requirements without coverage
- Suggest what kind of SADD content should be added

### Step 4 — Cross-Validate SADD → SRD (Findings Group C)

> **Skip this step if no SADD was provided.**

For each major design element, component, or architectural decision in the SADD,
check whether it traces back to an SRD requirement.

**How to check:**
- Identify the SADD's major components, modules, services, and design decisions
- For each, determine if there is a corresponding SRD requirement that justifies it
- Design elements without any requirement traceability may indicate:
  - A missing requirement in the SRD (the SRD should be updated)
  - Scope creep (design work beyond what was required)
  - Infrastructure/utility components that are implicitly needed (lower severity)

**For each untraceable design element, create a finding:**
- Warning: Major component or architectural decision with no SRD traceability
- Info: Minor utility or infrastructure component with no explicit requirement
- Suggest either adding an SRD requirement or documenting the rationale

### Step 5 — Generate Report

Generate the validation report as a markdown file using the template in
[report-template.md](assets/report-template.md).

**Report requirements:**
- File name: `plc-requirements-validation-report.md`
- Place in the current working directory (or ask the developer for a preferred location)
- Include all finding groups with findings sorted by severity (Critical first)
- Include the summary table with counts per group and severity
- Include the disclaimer about advisory-only status
- Remove any finding groups that have zero findings

**After generating the report:**
- Present a brief summary to the developer (total findings, critical count, top issues)
- Offer to walk through specific findings if needed

---

## Important Constraints

- **Advisory only:** Never claim that passing this validation means PLC compliance.
  Always include the disclaimer in the report.
- **Bidirectional:** Always check both directions (SRD → SADD and SADD → SRD).
  Inconsistencies can originate from either document.
- **Semantic matching:** Never require exact section names or document structure.
  Match by content and intent.
- **No document modification:** This skill only reports findings. It does not
  modify the SRD or SADD. The developer decides what to fix and how.
- **Format agnostic:** Accept any readable document format without complaint.

---

## Additional Resources

- Semantic SRD section reference: [srd-sections.md](assets/srd-sections.md)
- Report output template: [report-template.md](assets/report-template.md)

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` when there are no findings, `WARN` when findings or coverage gaps are reported, `SKIP` with `skip_reason` when required inputs are unavailable, and `ERROR` with `error_reason` for unexpected failures. Keep `summary` to aggregate finding counts only.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"requirements","check":"plc-requirements-validation","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0,"findings_high":0}}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-requirements-validation","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0,"findings_high":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
