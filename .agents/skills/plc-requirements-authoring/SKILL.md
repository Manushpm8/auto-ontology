---
name: plc-requirements-authoring
description: Draft, refine, and format software requirements for features in this codebase under NVIDIA Product Lifecycle (PLC) L1 SDLC expectations. Use when you need to write new requirements, modify existing requirements, decompose a feature into PLC-quality SRD requirements, work from Jama or local SRD documents, produce a feature brief, or define acceptance criteria that verify each requirement.
---

# PLC Requirements Authoring

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-requirements-authoring","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-requirements-authoring"}}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-skill-invocation","tool":"plc-requirements-authoring","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-requirements-authoring"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to interview the author, source existing SRD requirements, decompose a feature, and write PLC L1-quality requirements in the required markdown template.

## Output Mode

Default to **concise** output. Cover every required PLC L1 field, but in the most compact form the field allows — short sentences, terse bullets, no boilerplate explainers, no restating the prompt back at the author. Tables and ID rows stay; long prose justifications go. Switch to verbose output only when the author asks (phrasings like *"expand this section"*, *"give me the full version"*, *"walk me through it"*, or when the author is explicitly drafting a release-grade SRD that needs prose context for reviewers outside the team).

## Skill Resources

- [feature_brief.md](assets/feature_brief.md) is the bundled fallback template and the required-field contract. Use it as the output structure only when preflight (Step 1) finds no team template, or when the author explicitly chooses it. When a team template is selected, use its structure and treat `feature_brief.md` as the checklist of required Agentic PLC fields the output must still cover.

## Feature Brief Location

Store every generated or updated feature brief at the canonical work-product path for the current workspace.

- Derive `<feature-name>` from the user-provided feature name when available; otherwise derive it from the feature request summary.
- Slugify `<feature-name>` with lowercase ASCII letters and digits, replacing any run of non-alphanumeric characters with one hyphen, then trim leading or trailing hyphens.
- If the slug cannot be derived confidently, ask the author for the feature name before writing the brief.
- If `agentic_plc/workspace.json` exists, use workspace mode: `agentic_plc/work_products/<feature-name>/brief.md`. Create the feature directory if it does not already exist.
- Otherwise use the single-repo default: `.plc/briefs/<feature-name>-brief.md`. Create `.plc/briefs/` if it does not already exist.
- If the caller provides an output directory or loose output preference, still use the canonical path for the active mode.
- Use a different path only when the author explicitly requests a non-canonical path; report that deviation in `Advisory Notes`.

## Workspace Preflight

When `agentic_plc/workspace.json` is present, validate every `repos[].path` before drafting:

- `repos[].path` must be relative, must use `/` separators, and must not contain `..` or resolve outside the workspace root.
- Use `agentic_plc/workspace.json` as the source of truth for repo membership and paths. Use `.plc/profile.json` only as advisory brownfield metadata for artifact pointers, repo layout, nSpect/project pointers, and orientation hints.
- Generate or consume `agentic_plc/work_products/<feature-name>/traversal-plan.json` during workspace preflight. Do not re-enter the same repo/phase/action under the same plan version.
- Inspect all accessible repos listed in the manifest for relevant requirements context when the feature request, SRD/SADD, tests, or user request points across repos.
- Record consulted repos in the feature brief's workspace metadata or advisory notes.
- Surface missing, inaccessible, skipped, warned, failed, or invalid repos in `agentic_plc/work_products/<feature-name>/traversal-summary.md`. Do not silently treat a partial workspace as fully inspected.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Superpowers Integration

If Superpowers skills are present in the session, use them as workflow discipline for the Agentic PLC flow; do not treat them as competing instructions.

- Use Superpowers `brainstorming` to drive the interview, SRD-source clarification, feature decomposition, requirement coverage discussion, and assumption discovery.
- Use Superpowers `writing-plans` as the execution driver for planning the authoring work before drafting or updating the feature brief.
- Keep the PLC-specific instructions in this skill as the content standard: SRD sourcing, requirement quality rules, acceptance criteria, review checklist, feature brief template, and advisory review/signoff language still apply.
- Follow active tool and session policy. If Superpowers are unavailable or disallowed, continue with this skill normally and do not block on them.

## PLC L1 Requirement Quality Guidance

Apply this guidance when decomposing features and drafting requirements.

### Required Characteristics

Use these checks on every requirement:

| Characteristic | Requirement |
|---|---|
| Unambiguous | Lends itself to one interpretation by stakeholders, designers, QA, and validators. |
| Singular | Addresses one capability, characteristic, constraint, or quality factor. |
| Verifiable | Has at least one acceptance criterion that can prove the requirement is satisfied by automated test, manual test, inspection, analysis, or demonstration. |
| Complete | Stands alone with enough "what", "how well", and "under what conditions" detail. |
| Consistent | Does not contradict or overlap other requirements and uses the same terms, units, and meanings. |

### Writing Rules

- Use `shall` for required system behavior.
- Use active voice and a named system/component/actor.
- Use defined terms; add glossary assumptions if a term needs definition.
- Prefer one complete sentence per requirement.
- Include measurable thresholds, states, platforms, timing, units, data limits, or success/failure criteria when needed for verification.
- For every requirement, write at least one acceptance criterion that states the observable pass/fail condition and verification method. The criterion may be verified by automated test, manual test, inspection, analysis, or demonstration, but it must be specific enough for QA or reviewers to determine whether the requirement is satisfied.
- Repeat nouns instead of using pronouns when a pronoun could be ambiguous.
- Split requirements that contain multiple obligations, outcomes, modes, triggers, or verification targets.
- Trace derived requirements to the applicable superior source when known, such as stakeholder requirement, Jira/NVBug, customer ask, standard, architecture decision, or existing SRD item.

Avoid:

- Vague terms: `fast`, `easy`, `robust`, `typical`, `usually`, `similar`, `minimal`, `optimal`, `adequate`, `as possible`.
- Escape clauses: `should`, `could`, `may`, `optionally`, `TBD`.
- Open-ended lists: `etc.`, `and so on`, `including but not limited to`.
- Ambiguous combinators: `and/or`, broad `or`.
- Parenthetical obligations that hide requirements.
- Negative phrasing using `not` when a positive, testable behavior is possible.

### PLC Requirement Types

Assign each requirement one applicable type. Consider whether missing types are truly not applicable.

| Type | Use for |
|---|---|
| Functional | Intended behavior, service, task, or function. |
| System (Non-Functional) | Scalability, recoverability, maintainability, serviceability, manageability, data integrity, usability. |
| Interface | User, control, data, API, or system-to-system interface behavior. |
| Safety | Safety constraints or safety-critical behavior. |
| KPI | Capacity, throughput, response time, utilization, MTBF, availability, memory use, scalability metrics. |
| Platform | Supported hardware, software, OS, deployment, or environment. |
| Security | Encryption, logging, communication restrictions, data integrity, access control, abuse prevention. |
| Legal and Standards | Legal, regulatory, or standards obligations. |
| Telemetry | Instrumentation, field reporting, observability, remote accessibility. |
| Backward Compatibility | Legacy behavior, migration, compatibility, upgrade or downgrade support. |
| Virtualization | Emulation, simulation, virtual environments, or hypervisor behavior. |
| Automatability | Scriptable or white-box test automation support, including secure test hooks. |
| Other Non-Functional | Other operating constraints not covered above. |

### Syntax Patterns

Use these PLC syntax patterns as aids for clear requirements:

| Pattern | Use when | Syntax |
|---|---|---|
| Ubiquitous | The behavior is always true with no trigger or precondition. | `The <system name> shall <system response>.` |
| Event-driven | A trigger initiates the behavior. | `When <trigger><optional precondition>, the <system name> shall <system response>.` |
| Unwanted behavior | An error, fault, failure, or undesired event occurs. | `If <unwanted condition or event>, then the <system name> shall <system response>.` |
| State-driven | The behavior applies only while the system is in a state. | `While <system state>, the <system name> shall <system response>.` |
| Optional feature | The behavior applies only where a feature is included. | `Where <feature is included>, the <system name> shall <system response>.` |

### Common Pattern Issues

- For ubiquitous requirements, check whether the behavior actually has an unstated trigger, precondition, platform, state, or mode.
- For event-driven requirements, include the triggering event and any required precondition.
- For unwanted behavior requirements, name the error, fault, failure, invalid input, or other undesired event and the required response.
- For state-driven requirements, name the exact state or mode that makes the behavior applicable.
- For optional feature requirements, name the included feature, configuration, or capability that makes the behavior applicable.
- For all patterns, avoid vague timing such as `immediately`; use measurable response limits when timing matters.

### Review Checklist

For each individual requirement:

- Is it unambiguous?
- Is it one atomic thought?
- Is it verifiable?
- Does it have at least one acceptance criterion with a clear pass/fail condition and verification method?
- Is it a complete sentence using a PLC syntax pattern where useful?
- Is it traceable to a superior source when derived?

For the requirement set:

- Are all applicable requirement types represented or explicitly not applicable?
- Are the requirements complete for the feature scope?
- Are the requirements consistent with existing IDs, terms, units, and platforms?
- Are any requirements duplicates, conflicts, or design solutions outside the SRD scope?
- Are the requirements necessary and within the feature scope?
- Does every requirement ID trace to at least one acceptance criterion, and does every acceptance criterion trace back to a requirement ID?

For review and evidence:

- Recommend requirements author review as the minimum review checkpoint before proceeding.
- Recommend functional lead, engineering manager, QA, security, or other impacted stakeholder review when the feature scope warrants it.
- Do not claim PLC approval. The assigned owner or team PLC process may still require formal adherence confirmation and evidence, such as Jama review, meeting notes, checklist, or attestation.

Use a subagent for this review checklist when subagents are available and the active tool policy permits delegation. Treat this as an independent PLC requirement quality review, not as a second authoring pass. If subagents are unavailable, run the checklist locally and note that the subagent review was skipped.

Suggested subagent prompt:

```text
Review these draft PLC L1 software requirements against the PLC requirement quality checklist. Check individual requirements for ambiguity, atomicity, verifiability, completeness, syntax quality, traceability, and whether each requirement has at least one acceptance criterion with a clear pass/fail condition and verification method. Check the requirement set for type coverage, consistency with existing requirements, duplicates, conflicts, scope fit, missing requirement categories, missing acceptance criteria, and orphan acceptance criteria that do not trace to a requirement ID. Return findings only, grouped by requirement ID or set-level issue, with concise remediation suggestions.
```

## Workflow

Track this checklist during the work:

```markdown
Authoring Progress:
- [ ] Step 1: Source and template preflight
- [ ] Step 2: Load existing requirements, if available
- [ ] Step 3: Decompose the feature and review with the author
- [ ] Step 4: Draft new and modified requirements
- [ ] Step 5: Run subagent review checklist
- [ ] Step 6: Refine with the author
- [ ] Step 7: Write final markdown output
- [ ] Step 8: Optionally persist requirements
- [ ] Step 9: Recommend review and signoff before proceeding
```

### Step 1 - Source and Template Preflight

Begin every invocation with active discovery before drafting. A pilot run previously produced output that did not follow the team's PLC template and silently ignored existing Jama requirements; this step prevents both failure modes. Do not skip discovery even when the prompt already names a feature - the repo may carry a team template or requirement-system references the author did not mention.

Run discovery first, then report findings, then interview. Do not draft requirements until preflight is complete.

#### 1a. Search for templates

Look for a PLC template the team may have placed in the repo:

- `plc/templates/`, `.plc/templates/`, `plc/docs/`, and `docs/` for files whose names contain `srd`, `requirement`, `template`, or `feature-brief` (case-insensitive).
- The repo root for files matching `*template*.md` or `*template*.pdf`.

Capture each candidate path. If none are found, fall back to the bundled [feature_brief.md](assets/feature_brief.md) and state that explicitly to the author.

#### 1b. Search for existing SRD or requirements documents

Look for existing requirement artifacts before drafting:

- `plc/docs/`, `.plc/`, and `docs/` for files matching `*SRD*` or `*requirement*` (case-insensitive, both `.md` and `.pdf`).
- **The repo root (max-depth 1)** for files matching `SRD.md`, `SRD.pdf`, `*SRD*.md`, `*SRD*.pdf`, `requirements.md`, `requirements.pdf`, `*requirement*.md`, or `*requirement*.pdf`. A repo with `SRD.md` or `requirements.md` sitting next to `README.md` is a common layout and must not be missed. Restrict the root search to `.md` and `.pdf` so it does not pick up `requirements.txt` or other non-document files; if the team uses `.docx` for SRDs, add `requirements.docx` and `*requirement*.docx` to this list explicitly.
- `.plc/briefs/` for prior feature briefs that may carry reusable IDs or conventions.
- Top-level `README.md`, `CLAUDE.md`, and any `plc/docs/README*` for inline requirement tables or links to canonical documents.

Capture each match with its path.

#### 1c. Search for external requirement-system references

Even if no local SRD is present, the repo may reference an external system that holds the canonical requirements:

- Grep `README.md`, `CLAUDE.md`, `plc/docs/`, and top-level docs for the strings `Jama`, `jama.nvidia.com`, or `jamacloud.com`.
- Grep the same locations for requirement-ID patterns such as `REQ-\d+`, `SRD-\d+`, or any project-specific prefix that looks like a team convention.

Capture each reference (file path, line, surrounding context).

#### 1d. Report findings to the author

Before any interview question, tell the author exactly what preflight found, in this shape:

```text
Source preflight summary:
- Templates found: <paths, or "none - falling back to bundled feature_brief.md">
- Local SRD/requirements documents found: <paths, or "none">
- External requirement-system references found: <Jama URLs, requirement-ID conventions, or "none">
```

Reporting these findings up front is required by the issue acceptance criteria - the skill must say which template and source documents it used (or considered).

#### 1e. Interview the author

After the preflight report, ask:

1. Which discovered template should I use? Default is the bundled `feature_brief.md` if no team template was found.
2. Which discovered SRD or requirements document should I treat as the source of truth? Or is the source somewhere preflight did not find - a Jama project, an exported PDF, or pasted text?
3. What feature or change needs requirements?
4. What product, release, platform, users/stakeholders, and known constraints matter?

Handle the SRD source as follows:

- **Jama:** Ask for the Jama project URL and any relevant scope filter such as release, component, folder, item type, or baseline. Use the Jama MCP server to read existing requirements. If no Jama MCP server is available, state that and ask the author to export the SRD or requirements to PDF/Markdown.
- **PDF/Markdown SRD:** Tell the author to place the SRD in the codebase, preferably at `plc/docs/<SRD_file>.pdf` or `plc/docs/<SRD_file>.md`, then read it directly. Accept another local path if the author provides one.
- **No SRD source accessible:** If preflight surfaced Jama references or existing requirement-ID conventions but the author cannot or will not provide source input, do not silently continue. Capture a clear `Skip reason for source` to repeat in the final brief's `Advisory Notes` (for example, "Jama project FOO-123 referenced in `README.md:L42` but author has no access from this session; proceeding without external SRD"). Then continue with the ID rules in *Step 4*.

#### 1f. Exit criteria for this step

Before moving to Step 2 you must have:

- The preflight summary from 1d on record (for inclusion in the final brief).
- An explicit choice from the author of which template and which source to use, or a recorded skip reason if no source is accessible.

### Step 2 - Load Existing Requirements

If an SRD exists, extract the current requirement set before drafting:

- Capture each existing requirement's ID, title, description, type, and any visible status or trace links.
- Identify ID conventions used by existing requirements, but do not reserve or assign future IDs for Jama-managed requirements.
- Note existing requirements that may need modification instead of creating duplicates.
- Check for vocabulary, units, platform names, actors, and system names that must remain consistent.
- Do not modify Jama or source SRD documents unless the author explicitly asks for that action.

**Good-example reading for project-native style.** Templates and good examples have orthogonal jobs: the selected team template (Step 1) defines section contract and required fields; an accessible good-example SRD calibrates style, density, and heading depth. Read both whenever they are available — do **not** skip the good-example pass just because a template was found.

When existing requirements are accessible, use them as the project's style reference. Look at the document's actual shape — heading hierarchy and depth, requirement-row template (table vs prose-and-bullet), wording cadence (passive shall-statements vs active actor sentences), acceptance-criteria format (Given/When/Then vs Pass/Fail rows), expected level of detail per requirement, and how trace links are recorded. Apply that style on top of the template's section structure. Record the matched style in `Advisory Notes` (one line, e.g., "Style matched existing SRD: shall-statements in table; AC as Given/When/Then; short descriptions"). If template and good example conflict (template says one row shape, good example uses another), keep the template's required fields and choose the good example for density and wording — call the conflict out for the reviewer.

If the existing requirements look inconsistent with each other, pick the convention used most recently or most consistently and note which sample drove the choice.

### Step 3 - Decompose the Feature

Inspect the feature request, relevant code/docs/tests when useful, and the SRD context. Produce a short decomposition for author review before finalizing requirements:

- Scope and non-scope
- Actors, users, or consuming systems
- Main success flows and error/unwanted behavior flows
- Interfaces, data, configuration, telemetry, and platform implications
- Security, legal/standards, backward compatibility, virtualization, automatability, and KPI/performance considerations
- Existing requirements to modify and gaps requiring new requirements

Ask targeted follow-up questions for missing information that changes requirement wording or testability. If the author cannot answer, continue with explicit assumptions in `Advisory Notes`.

### Step 4 - Draft Requirements

Draft requirements using the PLC L1 requirement quality guidance in this skill:

- Write each requirement as one obligation using `shall`.
- Prefer PLC syntax patterns: ubiquitous, event-driven, unwanted behavior, state-driven, and optional feature.
- Make each requirement unambiguous, singular, verifiable, complete, and consistent.
- Include measurable limits, triggering conditions, states, actors, platforms, units, or verification criteria when needed.
- Add at least one acceptance criterion for every requirement. Write each acceptance criterion like a testable statement: expected condition/result, verification method, and enough setup or input detail to make the verification executable or reviewable.
- Split compound requirements. Do not hide multiple behaviors behind "and/or", open-ended lists, parentheses, or vague qualifiers.
- Assign a PLC requirement type. Consider all applicable requirement types, not only Functional.

When modifying an existing requirement, preserve its ID and draft the revised title/description/type. Use `Advisory Notes` to explain that the row is a proposed modification. For new requirements, choose IDs according to the source-of-truth rules below. Never create an ID that looks like it was assigned by Jama.

#### ID handling by source of truth

Preflight findings drive ID choice:

- **Jama selected, loaded, or surfaced as the requirements source of truth:** Jama assigns requirement IDs. For every new requirement, use a deterministic placeholder ID of the form `PENDING-JAMA-<slug>` instead of reserving the next visible Jama ID. Build `<slug>` from the proposed requirement name using lowercase ASCII letters and digits, replacing non-alphanumeric runs with `-`, trimming leading/trailing `-`, and appending `-2`, `-3`, etc. if needed for uniqueness in the brief. Never assign IDs such as `IPPST-REQ-1142`, `REQ-1142`, or any other Jama-looking project ID unless that exact ID came from an existing Jama item or the user returned it after creating the item in Jama.
- **Other source loaded and the next available IDs are known:** reserve the next IDs in the existing non-Jama convention. Do not invent IDs that may collide with existing entries.
- **Jama referenced but not accessible** (Step 1e skip reason recorded): treat new requirements as Jama-pending, not local drafts. Use `PENDING-JAMA-<slug>`, include the pending Jama fields below, and block downstream traceability until the user returns Jama-assigned IDs.
- **Non-Jama source referenced but not accessible** (Step 1e skip reason recorded): treat any new IDs as local drafts. Prefix them with `DRAFT-` (for example, `DRAFT-FR-1`, `DRAFT-NFR-1`) and state in `Advisory Notes` that these are local drafts that must be remapped against the canonical source before they enter the SRD. Never reuse an ID surfaced in preflight unless the author confirmed that requirement is being modified.
- **No source referenced anywhere:** use the simple `FR-1` / `NFR-1` convention and note in `Advisory Notes` that no canonical source was found, so these IDs are local to this brief.

This rule exists because a pilot user previously had requirements silently invented under IDs that overlapped with existing Jama IDs.

For every `PENDING-JAMA-*` row, prepare a paste-ready Jama creation block with these fields:

- Name
- Description
- Context
- Verification Method
- Requirement Type
- Target Release

Add each pending item to the brief's `Pending Jama ID Resolution` section. If this skill is running under `plc-v-model`, pass the same pending item list back to the orchestrator so it can store it in `.plc/v-model-state.json` under `plc-v-model.pending_requirements`.

After presenting or writing a brief that contains any `PENDING-JAMA-*` ID, pause and tell the user exactly:

```text
A new requirement is needed. Please create it in Jama with the details below and bring back the assigned ID — I'll insert it into the SRD and SADD once you have it.
```

Then display the pre-filled Jama fields for each pending requirement. Do not proceed to design, SADD references, code comments, implementation, evidence rows, or traceability for those pending requirements until the user returns the Jama-assigned IDs and the placeholders have been replaced.

When the user returns Jama IDs, replace each `PENDING-JAMA-*` placeholder throughout the feature brief, including requirement tables, acceptance criteria, advisory notes, and any already-existing design or traceability sections. Mark the corresponding pending item as resolved in the brief and, when running under `plc-v-model`, in `.plc/v-model-state.json`.

### Step 5 - Run Subagent Review Checklist

After drafting requirements and before presenting a final table, run the review checklist through a subagent when subagents are available and the active tool policy permits delegation.

Give the subagent:

- The feature decomposition
- Existing requirements or SRD excerpts used for clash/fit checks
- The draft requirements table
- The acceptance criteria mapped to requirement IDs
- Any assumptions or unresolved questions

Ask the subagent to return only findings and remediation suggestions. Do not ask the subagent to rewrite the full table unless the author explicitly requested that. Incorporate valid findings into the next draft and mention material unresolved review findings in `Advisory Notes`.

If subagents are unavailable, run the same checklist locally and state that the subagent review was skipped.

### Step 6 - Refine With the Author

Work iteratively:

- Show the feature decomposition and a first requirements table.
- Ask the author to confirm assumptions, requirement scope, and wording.
- Revise the requirements based on feedback.
- Re-check against existing requirements for duplicate, conflicting, overlapping, or out-of-scope statements.

If the author asks for a best-effort draft without another round trip, proceed and clearly list assumptions and unresolved questions in `Advisory Notes`.

### Step 7 - Write Final Markdown Output

Write the final markdown using the template the author selected in Step 1e. Store it using the Feature Brief Location rules above (`agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`).

Template choice drives output structure:

- **Team template selected.** Use its section structure as the output skeleton. Then ensure the following Agentic PLC fields are present, mapping them into the closest matching section if the team template names them differently: functional requirements, non-functional requirements, acceptance criteria (with verification method and verification idea per row), `Sources Used`, `Advisory Notes`, and the metadata header captured in [feature_brief.md](assets/feature_brief.md) (status, repo, branch, commit, feature request, brief path, template used, SRD source used). Record any mapping you had to do (which Agentic PLC field landed under which team-template section) in `Advisory Notes` so a downstream reader can find each field.
- **Bundled `feature_brief.md` selected** (preflight found no team template, or author chose the bundled one). Use it directly with no mapping notes.
- **Team template cannot accommodate a required Agentic PLC field** (for example, a strict canonical SRD form with no free-text section). Say that explicitly in `Advisory Notes`, write the missing field at the end of the document under a clearly-labelled `## Advisory Notes` heading, and call the deviation out for the reviewer.

Ensure every requirement listed in the functional and non-functional requirement tables has at least one row in the `Acceptance Criteria` table. The acceptance criteria table must include the requirement ID, the criterion, the verification method, and the verification idea or test procedure. Do not leave acceptance criteria implied only by the requirement text.

In `Advisory Notes`, include:

- A **Sources Used** block that lists the template path used (or "bundled `feature_brief.md`" if no team template was found), every SRD/requirements document consulted (with path or URL), and every Jama / external requirement-system reference surfaced in preflight. If a source was referenced but not accessible, include the skip reason from Step 1e here verbatim.
- If any IDs use the `PENDING-JAMA-` prefix from Step 4, include the paste-ready Jama fields and state that the workflow is blocked until the user returns Jama-assigned IDs.
- If any IDs use the `DRAFT-` prefix from Step 4, state explicitly that these are local draft IDs that must be remapped before the canonical non-Jama SRD is updated.
- Assumptions, unresolved questions, proposed modifications to existing IDs, and any requirement types intentionally marked not applicable.

Never claim PLC compliance or approval. State that the draft supports PLC L1 requirements authoring and recommend that the developer route it through the team's review/signoff workflow before proceeding.

### Step 8 - Optionally Persist Requirements

- If the SRD document is writable (like Markdown), ask the user if they want to go ahead and write the updated requirements into the documents.
- If they use Jama for requirements management and unresolved `PENDING-JAMA-*` IDs exist, do not write downstream SRD/SADD traceability yet. Present the Jama creation fields and wait for the user to return assigned IDs. Only write through Jama MCP if the user explicitly asks for that action and the available MCP supports safe item creation.

### Step 9 - Recommend Review and Signoff Before Proceeding

End every authoring run by recommending that the developer get the drafted or updated requirements reviewed and signed off before proceeding with implementation, Jama updates, SRD updates, or treating them as accepted requirements. Present this as a recommendation from the skill, not as a hard requirement. Mention likely reviewers when relevant, such as the requirements author, product owner, functional lead, engineering manager, QA, security, or impacted stakeholders.

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` for a completed draft with no material gaps, `WARN` when assumptions or gaps remain, `SKIP` with `skip_reason` when the skill could not run, and `ERROR` with `error_reason` for unexpected failures. Keep the event compact and omit artifact paths.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"requirements","check":"plc-requirements-authoring","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"requirements","check":"plc-requirements-authoring","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
