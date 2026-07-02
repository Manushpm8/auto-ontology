---
name: plc-design-authoring
description: Draft, refine, and append PLC L1 software design components for a feature in this codebase. Use when user needs to design a feature from written requirements, update a feature brief with design deltas and test sections, or respond to prompts like "I need to design a feature for xyz" or "Design a feature for requirements xyz". This skill depends on plc-requirements-authoring having produced a requirements feature brief first.
---

# PLC Design Authoring

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"design","check":"plc-skill-invocation","tool":"plc-design-authoring","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-design-authoring"}}
JSON
```

```powershell
@'
{"pillar":"design","check":"plc-skill-invocation","tool":"plc-design-authoring","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-design-authoring"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to source existing SDD context, understand the current codebase, design a feature from existing requirements, and update the same feature brief markdown with PLC L1 design sections.

This skill does not capture threat modeling yet. Call that out when presenting security design output or final notes.

## Output Mode

Default to **concise** output. Cover every required PLC L1 design area, but in the most compact form the area allows — short paragraphs, terse bullets, no boilerplate restatement of the requirements. Switch to verbose output only when the author asks (phrasings like *"expand the design"*, *"give me the full version"*, *"walk me through the architecture"*, or when the design is intended to graduate to a release SADD that needs prose context for reviewers outside the team).

## Diagrams

When a design naturally calls for a sequence, component, deployment, state, or data-flow diagram, emit it as a Mermaid fenced block by default. Mermaid is reviewable in-place by anyone who can read the Markdown, so the diagram does not block on tooling. If the team's existing SADDs use a different diagram source format (PlantUML, Graphviz dot, linked SVG, etc.), follow that convention instead — see *Good-example reading* in Step 3.

**Rendering handoff (Mermaid).** Mermaid source can only be rendered by a Mermaid-capable renderer. Do **not** hand a Mermaid block to a PlantUML-only renderer — the formats are not interchangeable. When the session exposes a Mermaid renderer (for example, a `render-mermaid` skill, an Anthropic Files Mermaid render capability, a CLI like `mmdc`, or a Superpowers workflow that explicitly accepts Mermaid input), hand off the Mermaid block for image rendering after drafting and link the rendered image alongside the source block in the brief — keep the source so the diagram remains editable.

**Rendering handoff (PlantUML).** If the team convention or selected template uses PlantUML, emit PlantUML source (`@startuml … @enduml`) instead of Mermaid and hand off to a PlantUML-capable renderer (`render-plantuml`, `plantuml` CLI, equivalent). Do not mix Mermaid source with a PlantUML renderer or vice versa.

If no compatible renderer is available, leave the source block inline and note in `Advisory Notes` that rendering is pending plus which compatible tool the author can use locally (`mmdc` for Mermaid, `plantuml` for PlantUML). Never paste a rendered image without the source block; the source is what the team edits.

## Dependency

Start only after `plc-requirements-authoring` has been used and requirements have been written to a feature brief markdown file. If the feature brief requirements are not in context, ask the user where the feature brief is, or recommend running `plc-requirements-authoring` first.

Required input:

- Feature brief markdown path or content
- Requirements and acceptance criteria from that brief
- Feature scope and any user-provided design constraints

[design-sections.md](assets/design-sections.md) is the bundled fallback design-section template and the required-field contract. Use it as the source for the sections to append after `## Acceptance Criteria` only when preflight (Step 2) finds no team SADD template, or when the author explicitly chooses it. When a team SADD template is selected, use its structure and treat `design-sections.md` as the checklist of required PLC L1 design areas the output must still cover.

## Feature Brief Location

Use the canonical feature brief path for the active mode.

- In workspace mode (`agentic_plc/workspace.json` exists), expect `plc-requirements-authoring` to create `agentic_plc/work_products/<feature-name>/brief.md`.
- Otherwise expect `plc-requirements-authoring` to create `.plc/briefs/<feature-name>-brief.md`.
- Derive `<feature-name>` from the feature brief metadata, file name, or feature request using the same slug as requirements authoring: lowercase ASCII letters and digits, replacing runs of non-alphanumeric characters with one hyphen, then trimming leading or trailing hyphens.
- If a caller provides pasted requirements content or a non-canonical brief path, write or update the canonical copy before adding design sections. Preserve the original source file unless the author explicitly asks to move or delete it.
- Create the canonical parent directory if it does not already exist.
- Use a different path only when the author explicitly requests a non-canonical path; report that deviation in the final notes.

## Workspace Preflight

When `agentic_plc/workspace.json` is present, validate every `repos[].path` before drafting design updates:

- `repos[].path` must be relative, must use `/` separators, and must not contain `..` or resolve outside the workspace root.
- Use `agentic_plc/workspace.json` as the source of truth for repo membership and paths. Use `.plc/profile.json` only as advisory brownfield metadata for artifact pointers, repo layout, nSpect/project pointers, and orientation hints.
- Consume the persisted `agentic_plc/work_products/<feature-name>/traversal-plan.json` snapshot. Refresh it only when scope, feature references, manifest content, repo availability, repo revision, or profile metadata changed.
- Inspect all accessible repos listed in the manifest when the feature brief, SADD/SDD, interface map, imports, tests, or user request points across repos.
- Record which repos were consulted and which repos were selected for design/code/test pointers.
- Surface missing, inaccessible, skipped, warned, failed, or invalid repos in `agentic_plc/work_products/<feature-name>/traversal-summary.md`. Do not silently treat a partial workspace as fully inspected.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Superpowers Integration

If Superpowers skills are present in the session, use them as workflow discipline for the Agentic PLC flow; do not treat them as competing instructions.

- Use Superpowers `brainstorming` to drive design interviews, alternative exploration, tradeoff analysis, interface discovery, security/operations questions, and assumption discovery.
- Use Superpowers `writing-plans` as the execution driver for planning the design authoring work before updating the feature brief or SDD.
- Keep the PLC-specific instructions in this skill as the content standard: SDD sourcing, codebase understanding, PLC L1 design coverage, unit test traceability, feature brief updates, threat-modeling caveat, and advisory design review language still apply.
- Follow active tool and session policy. If Superpowers are unavailable or disallowed, continue with this skill normally and do not block on them.

## Workflow

Track this checklist during the work:

```markdown
Design Authoring Progress:
- [ ] Step 1: Confirm canonical requirements feature brief
- [ ] Step 2: Source and template preflight
- [ ] Step 3: Load existing design context, if available
- [ ] Step 4: Understand the codebase
- [ ] Step 5: Draft feature design components
- [ ] Step 6: Refine with the author
- [ ] Step 7: Update the canonical feature brief
- [ ] Step 8: Ask whether to update writable SDD
- [ ] Step 9: Offer to run design validation
- [ ] Step 10: Recommend design review before proceeding
```

### Step 1 - Confirm Canonical Requirements Feature Brief

Before designing, confirm that requirements are available from the canonical feature brief produced by `plc-requirements-authoring` (`agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`).

If requirements are missing:

- Ask the user for the feature name, canonical feature brief markdown path, or pasted content.
- If no feature brief exists, tell the user to run `plc-requirements-authoring` first.
- Do not invent requirements. If assumptions are needed, mark them clearly.

If the user provides a non-canonical path or pasted content, derive the feature name, create or update the canonical feature brief for the active mode, and use that canonical file as the design working file. Do not delete or overwrite the original non-canonical source unless explicitly asked.

Read and retain:

- Functional requirements
- Non-functional requirements
- Acceptance criteria
- Open questions, assumptions, and requirement sources

Before moving to design source preflight, check the feature brief and `.plc/v-model-state.json` when present. If the brief contains any `PENDING-JAMA-*` requirement ID, or if `state["plc-v-model"].pending_requirements` has any entry with `status != "resolved"` or an empty `jama_id`, stop. Do not draft design sections, SADD references, unit-test traceability, code/file traceability, or implementation notes against placeholder IDs. Tell the user:

```text
A new requirement is needed. Please create it in Jama with the details below and bring back the assigned ID — I'll insert it into the SRD and SADD once you have it.
```

Display the pending requirement's Name, Description, Context, Verification Method, Requirement Type, and Target Release from the brief's `Pending Jama ID Resolution` table or the state file. Resume design only after each placeholder has been replaced by its Jama-assigned ID.

### Step 2 - Source and Template Preflight

Before asking the author about SDD source, run active discovery. A pilot user previously saw design output that did not follow the team's PLC template and missed an existing SADD; this step prevents both failure modes. Do not skip discovery even when the prompt already names a feature - the repo may carry templates or SADD references the author did not mention.

Run discovery first, then report findings, then interview.

#### 2a. Search for templates

Look for an SDD/SADD template the team may have placed in the repo:

- `plc/templates/`, `.plc/templates/`, `plc/docs/`, and `docs/` for files whose names contain `sadd`, `sdd`, `design`, or `template` (case-insensitive).
- The repo root for files matching `*template*.md` or `*template*.pdf`.

Capture each candidate path. If none are found, fall back to the bundled [design-sections.md](assets/design-sections.md) and state that explicitly to the author.

#### 2b. Search for existing SDD/SADD documents

Look for existing design artifacts before drafting:

- `plc/docs/`, `.plc/`, and `docs/` for files matching `*SADD*`, `*SDD*`, or `*design*` (case-insensitive, both `.md` and `.pdf`).
- **The repo root (max-depth 1)** for files matching `SADD.md`, `SADD.pdf`, `*SADD*.md`, `*SADD*.pdf`, `SDD.md`, `SDD.pdf`, `*SDD*.md`, `*SDD*.pdf`, `design.md`, `design.pdf`, `*design*.md`, or `*design*.pdf`. A repo with `SADD.md`, `SDD.md`, or `design.md` sitting next to `README.md` is a common layout and must not be missed. Restrict the root search to `.md` and `.pdf` so it does not pick up source files like `design.go` or `design.py`; if the team uses `.docx` for SADDs, add `SADD.docx`, `SDD.docx`, and `*design*.docx` to this list explicitly.
- Top-level `README.md`, `CLAUDE.md`, and any `plc/docs/README*` for inline design references or links to canonical documents.

Capture each match with its path.

#### 2c. Search for external design-system references

Even if no local SDD is present, the repo may reference an external system that holds the canonical design:

- Grep `README.md`, `CLAUDE.md`, `plc/docs/`, and top-level docs for Confluence, Google Drive, SharePoint, or Perforce URLs that look like design documents (page titles or paths containing `sadd`, `sdd`, `design`, `architecture`).
- Note any internal document system references the team uses (Confluence space, Glean datasource, depot path).

Capture each reference (file path, line, surrounding context).

#### 2d. Report findings to the author

Before any interview question, tell the author exactly what preflight found, in this shape:

```text
SDD preflight summary:
- Templates found: <paths, or "none - falling back to bundled design-sections.md">
- Local SDD/SADD documents found: <paths, or "none">
- External design-system references found: <URLs / depot paths / "none">
```

Reporting these findings up front is required by the issue acceptance criteria - the skill must say which template and source documents it used (or considered).

#### 2e. Interview the author

After the preflight report, ask the author:

1. Which discovered template should I use? Default is the bundled `design-sections.md` if no team template was found.
2. Which discovered SDD/SADD should I treat as the source of truth? Or is the source somewhere preflight did not find?

Handle sources as follows:

- **PDF/Markdown SDD:** Ask the user to place it in the codebase, preferably at `plc/docs/<SDD_file>.pdf` or `plc/docs/<SDD_file>.md`, then read it directly. Accept any local path they provide.
- **MCP-backed SDD:** Use available MCP servers when present, such as Google Drive/Glean, Confluence, or Perforce. Ask for the URL, depot path, page, or file identifier needed to fetch it.
- **No SDD source accessible:** If preflight surfaced external design references but the author cannot or will not provide source input, do not silently continue. Capture a clear `Skip reason for source` to repeat in the final brief's notes block (for example, "Confluence page referenced in `README.md:L88` but Confluence MCP returned 403; author chose to proceed without external SDD"). Then continue with a feature-brief design delta only, and state in the brief that clash/fit checks against the canonical SDD were not performed.

If an existing SDD is loaded, extract existing design components relevant to the feature and note any conventions, interfaces, security patterns, operational patterns, logging standards, KPI instrumentation, or test traceability expectations. Do not silently invent design elements that may overlap with existing components surfaced in preflight.

#### 2f. Exit criteria for this step

Before moving to Step 3 you must have:

- The preflight summary from 2d on record (for inclusion in the final brief).
- An explicit choice from the author of which template and which source to use, or a recorded skip reason if no source is accessible.

### Step 3 - Load Existing Design Context

Use existing SDD content, feature brief content, and nearby repo docs to avoid conflicts:

- Identify existing design components this feature may modify or depend on.
- Identify interfaces, contracts, data models, operational behavior, logging, metrics, and security patterns already described.
- Flag potential conflicts with existing design components before drafting changes.
- Do not update the SDD document yet. First produce the feature brief design sections and ask before writing into the SDD.

**Good-example reading for project-native style.** Templates and good examples have orthogonal jobs: the selected SADD template (Step 2) defines section contract and required design areas; an accessible good-example SDD/SADD calibrates style, density, diagram convention, and heading depth. Read both whenever they are available — do **not** skip the good-example pass just because a template was found.

When an existing SDD/SADD is accessible, use it as the project's style reference. Look at the document's actual shape — heading hierarchy and depth, component-row template (table vs prose), diagram convention (Mermaid vs PlantUML vs linked images), interface-spec format (JSON schema vs OpenAPI vs prose), how traceability is recorded, and the level of detail the team treats as canonical. Apply that style on top of the template's section structure. Record the matched style in the brief's design-delta notes (one line, e.g., "Style matched existing SADD: components-as-table; sequence diagrams as PlantUML; interfaces as OpenAPI"). If template and good example conflict, keep the template's required sections and choose the good example for density, wording, and diagram convention — call the conflict out for the reviewer.

If the existing SADD uses a non-Mermaid diagram convention, follow that convention (and the matching renderer) instead of the Mermaid default and note the deviation.

### Step 4 - Understand the Codebase

Build a working map of the current codebase before proposing design details.

Read or inspect:

- Root `README.md`, `CLAUDE.md`, and top-level docs.
- Infrastructure components, if any.
- Development dependencies, build tooling, linting, and test framework.
- Module layout: directories and their purpose.
- Key entry points: APIs, CLIs, workflows, services, schedulers, jobs, or package exports.
- Config surfaces: environment variables, settings files, feature flags, CLI options.
- External integrations: databases, queues, third-party APIs, internal services, storage, auth providers.
- Main-path data flow and control flow.

For larger repos, map the architecture first and then drill into the modules relevant to the feature requirements.

### Step 5 - Draft Feature Design Components

Design from the requirements and acceptance criteria. Work with the author by asking targeted questions where design choices, constraints, or tradeoffs are unclear.

Cover these PLC L1 design areas:

1. **Proposed Design** - Describe the design in detail. Include major components, responsibilities, data/control flow, state changes, failure behavior, and significant alternatives considered with reasons rejected.
2. **Interface Architecture** - For each internal or external interface, specify:
   - Type of interface
   - Operational implications of data transfer, including security considerations
   - Data transfer requirements: content, format, sequence
   - Data formats for sending and receiving systems: item names, codes, abbreviations
   - Interface procedures
   - Interface equipment
   - Data conversion requirements
3. **System KPIs & Metrics** - Explain how requirement KPIs are instrumented, measured, emitted, stored, and reviewed.
4. **Security Design** - Describe integrity and access controls for physical/logical components such as schemas, partitions, files, records, tables, relations, data elements, or equivalent repo-specific storage. Cite security standards or security requirement IDs where known.
5. **Secure Settings by Default** - Identify default settings that reduce compromise risk at install time or first use.
6. **Debugging & Troubleshooting** - Define debug prints, error messages, meanings, troubleshooting steps, and any new diagnostic tools.
7. **Logging and Instrumentation** - Define each required log, metric, trace, event, or audit signal and when it is emitted.
8. **Operational Considerations** - Cover recoverability, failure contingencies, availability, capacity, performance and timing, data retention, error handling, and adaptability.
9. **Unit Test Traceability** - Ensure every design component has proposed unit tests where applicable. Trace each test to a design component and to the requirement ID it verifies when a requirement applies. If a design component cannot reasonably be unit tested, explain the manual, integration, or system-level verification approach.

Keep the design at the level needed for an SDD or design delta. Do not implement code unless the user explicitly asks.

### Step 6 - Refine With the Author

Before updating the feature brief, show the proposed design sections or a concise summary and ask the author to confirm:

- Major design choices and rejected alternatives
- Interfaces and contract changes
- Security assumptions and secure defaults
- Logs, metrics, KPIs, and troubleshooting behavior
- Operational assumptions
- Unit test traceability
- Existing design conflicts or open questions

If the author asks for a best-effort draft without another round trip, proceed and list assumptions and unresolved questions in the feature brief.

### Step 7 - Update the Canonical Feature Brief

Update the canonical feature brief that contains the requirements. Append the design sections immediately after the `## Acceptance Criteria` section, using the template the author selected in Step 2e.

Template choice drives the section structure:

- **Team SADD template selected.** Use its design section structure. Then ensure every required PLC L1 design area is covered, mapping into the closest matching section in the team template when names differ: Proposed Design Delta (or equivalent), Test Implications, Security And PLC Implications, Traceability, and a `Sources Used` line at the top of the design delta. Record the mapping (which PLC L1 area landed under which team-template section) at the start of the design delta so a downstream reader can find each area.
- **Bundled [design-sections.md](assets/design-sections.md) selected** (preflight found no team template, or author chose the bundled one). Use it directly with no mapping notes.
- **Team template cannot accommodate a required PLC L1 design area.** Say that explicitly at the top of the design delta, append the missing area under its standard heading after the team-template sections, and call the deviation out for the reviewer.

When filling those sections:

- `## Proposed Design Delta`: summarize affected components/files, the design, interface or contract changes, data model changes, and operational considerations. Lead this section with a **Sources Used** line that names the template path used (or "bundled `design-sections.md`" if no team template was found), every SDD/SADD document consulted (with path or URL), and every external design-system reference surfaced in preflight. If a source was referenced but not accessible, include the Step 2e skip reason verbatim.
- `## Test Implications`: include unit tests for each design component where applicable, plus integration, e2e, manual, or other tests needed by the design.
- `## Security And PLC Implications`: include security design notes, secure defaults, logging/instrumentation implications, and explicitly state that threat modeling is not captured by this skill yet.
- `## Traceability`: map each requirement to design elements, code/files, and tests/evidence. Include unit test traceability for each design component where applicable.

If any of those sections already exist in the feature brief, update them instead of duplicating headings.

### Step 8 - Ask Whether to Update Writable SDD

If an SDD was provided and is writable, ask the user whether to update that SDD with the feature design details.

- For Markdown SDDs, offer to edit the document directly.
- For PDF SDDs, do not edit the PDF directly; offer markdown text that can be incorporated into the canonical SDD.
- For MCP-backed docs, ask before writing changes through an MCP server, and only proceed if the available MCP supports safe updates.
- If there is no SDD, note that the feature brief design delta can seed the future SDD.

### Step 9 - Offer to Run Design Validation

After the feature brief (and any writable SDD) has been updated, hand off to `plc-design-validation` against the updated brief so coverage gaps and design-vs-code drift are caught early.

Choose one of three paths:

- **The calling workflow already authorized running `plc-design-validation`** (for example, the `plc-v-model` orchestration ran this skill with explicit approval to chain the validator). Invoke `plc-design-validation` directly with the active-mode canonical brief path as the SADD source. Do not re-prompt.
- **No prior authorization is in effect.** Ask the author: "Run `plc-design-validation` against the updated feature brief now? (y/n)". On `y`, invoke it with the canonical brief path. On `n`, note in the final summary that design validation was offered and declined, and recommend it as a separate manual step.
- **`plc-design-validation` is unavailable in this session.** Tell the author the validator was not available, and recommend running it as a follow-up once it is.

This handoff exists because issue #5 acceptance criteria require that design validation be offered or triggered after SADD generation.

### Step 10 - Recommend Design Review Before Proceeding

End every authoring run by recommending that the developer get the drafted design reviewed before proceeding with implementation, SDD updates, or treating it as an accepted design. Present this as a recommendation from the skill, not as a hard requirement.

Mention likely reviewers when relevant: requirements author, feature owner, product owner, functional lead, engineering manager, QA, security, SRE/operations, or owners of impacted interfaces.

Never claim PLC compliance or approval. State that the draft supports PLC L1 design authoring and still needs the team's normal review/signoff workflow if they choose to use one.

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` for a completed design draft with no material gaps, `WARN` when assumptions or gaps remain, `SKIP` with `skip_reason` when the skill could not run, and `ERROR` with `error_reason` for unexpected failures. Keep the event compact and omit artifact paths.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"design","check":"plc-design-authoring","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"design","check":"plc-design-authoring","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
