---
name: plc-test-plan-authoring
description: Draft, refine, and format a software test/verification plan for a feature in this codebase under NVIDIA Product Lifecycle (PLC) L1 SDLC expectations. Use when the user needs a test plan from existing requirements and design, when responding to prompts like "draft a test plan for xyz", "write the STP", "what tests do we need", "how do we verify this feature", or when called by `plc-v-model` after design approval. This skill depends on `plc-requirements-authoring` and `plc-design-authoring` having produced a feature brief with requirements, acceptance criteria, and design sections first.
---

# PLC Test Plan Authoring

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-test-plan-authoring","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-test-plan-authoring"}}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-skill-invocation","tool":"plc-test-plan-authoring","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-test-plan-authoring"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

Use this skill to source existing test artifacts, decompose verification needs from the feature brief, and write a PLC L1-quality test plan in the required markdown template.

This skill produces a *plan*; it does not execute tests. Test execution evidence is captured separately by `plc-evidence-ingestion`, which appends pointers (Jenkins URLs, manual signoff notes, perf reports, ScanSpect/nSpect run IDs) to `plc-evidence.jsonl` keyed against `FR-*` / `NFR-*` / `AC-*` IDs. Downstream skills (`plc-acceptance-validation`, `plc-traceability-matrix`) read those rows to classify each acceptance criterion and to fill the Last verified column of the audit matrix.

## Output Mode

Default to **concise** output. Cover every required PLC L1 field, but in the most compact form the field allows — short test-case rows, terse setup/action/expected lines, no boilerplate restatement of the requirements being verified. Coverage tables stay; long prose justifications go. Switch to verbose output only when the author asks (phrasings like *"expand this section"*, *"give me the full version"*, *"walk me through it"*, or when the plan is intended to graduate to a release STP that needs prose context for reviewers outside the team).

## Skill Resources

- [test-plan-template.md](assets/test-plan-template.md) is the bundled fallback template and the required-field contract. Use it as the output structure only when preflight (Step 2) finds no team STP template, or when the author explicitly chooses it. When a team template is selected, use its structure and treat `test-plan-template.md` as the checklist of required Agentic PLC fields the output must still cover.

## Dependency

Start only after `plc-requirements-authoring` and `plc-design-authoring` have produced a feature brief with requirements, acceptance criteria, and design sections. If the feature brief lacks any of these, ask the user where the feature brief is, or recommend running the prerequisite authoring skills first. Do not invent requirements, design components, or acceptance criteria.

Required input:

- Feature brief markdown path or content (default `agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`)
- Requirements (FR-*, NFR-*) and acceptance criteria (AC-*) from the brief
- Design components, interfaces, and traceability rows from the brief
- Feature scope and any user-provided verification constraints (target environments, available test infrastructure, time budget)

## Test Plan Location

Store every generated or updated test plan at the canonical work-product path for the current workspace.

- Derive `<feature-name>` from the feature brief metadata (filename or `Feature/task request`) using the same slug rules as `plc-requirements-authoring`: lowercase ASCII letters and digits, replacing runs of non-alphanumeric characters with one hyphen, then trimming leading or trailing hyphens.
- If the slug cannot be derived confidently from the brief, ask the author before writing.
- If `agentic_plc/workspace.json` exists, use workspace mode: `agentic_plc/work_products/<feature-name>/test-plan.md`. Create the feature directory if it does not already exist.
- Otherwise use the single-repo default: `.plc/briefs/<feature-name>-test-plan.md`. Create `.plc/briefs/` if it does not already exist.
- The feature brief should also be updated with a `Test plan path` pointer in its metadata table once the test plan is written.
- Use a different path only when the author explicitly requests a non-canonical path; report that deviation in `Advisory Notes`.

## Workspace Preflight

When `agentic_plc/workspace.json` is present, validate every `repos[].path` before drafting the test plan:

- `repos[].path` must be relative, must use `/` separators, and must not contain `..` or resolve outside the workspace root.
- Use `agentic_plc/workspace.json` as the source of truth for repo membership and paths. Use `.plc/profile.json` only as advisory brownfield metadata for artifact pointers, repo layout, nSpect/project pointers, and orientation hints.
- Consume the persisted `agentic_plc/work_products/<feature-name>/traversal-plan.json` snapshot. Refresh it only when scope, feature references, manifest content, repo availability, repo revision, or profile metadata changed.
- Inspect test roots, CI entry points, existing test reports, and code pointers across all accessible repos relevant to the feature.
- Generate repo-specific test cases only for relevant or explicitly selected repos, and keep one shared test plan under `agentic_plc/work_products/<feature-name>/`.
- Surface missing, inaccessible, skipped, warned, failed, or invalid repos in `agentic_plc/work_products/<feature-name>/traversal-summary.md`. Do not silently treat a partial workspace as fully inspected.

## Markdown Write Safety

All skill-authored Markdown writes must be UTF-8-safe. Prefer shell-independent Python writes on every OS: `Path(path).write_text(content, encoding="utf-8")` or `path.open("w", encoding="utf-8")`. Do not branch on OS when Python is available; explicit Python encoding is the portable default. If you are about to write through PowerShell, first check `$PSVersionTable.PSVersion`. On Windows PowerShell 5.x, do not use bare `Set-Content` for Markdown because it uses the active code page. Use `[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)`. In PowerShell 7+, `Set-Content -Encoding utf8NoBOM` is acceptable. Do not rely on WSL as the fallback for Windows agent sessions.

## Superpowers Integration

If Superpowers skills are present in the session, use them as workflow discipline for the Agentic PLC flow; do not treat them as competing instructions.

- Use Superpowers `brainstorming` to drive interviews about verification scope, layer selection, environment constraints, and rejected verification approaches.
- Use Superpowers `writing-plans` as the execution driver for planning the test plan authoring work before drafting test cases.
- Keep the PLC-specific instructions in this skill as the content standard: STP sourcing, test case quality rules, verification method classification, traceability to acceptance criteria, review checklist, test plan template, and advisory review/signoff language still apply.
- Follow active tool and session policy. If Superpowers are unavailable or disallowed, continue with this skill normally and do not block on them.

## PLC L1 Test Plan Quality Guidance

Apply this guidance when decomposing verification needs and drafting test cases.

### Required Characteristics

Use these checks on every test case:

| Characteristic | Requirement |
|---|---|
| Unambiguous | One reviewer should not need to ask "what does pass mean here?". Setup, action, and expected result are concrete. |
| Executable | A QA engineer or automation script can follow the steps without additional design decisions. |
| Traceable | Cites at least one requirement ID (FR-*, NFR-*) or acceptance criterion ID (AC-*) it verifies. |
| Isolated | Does not depend on side effects of unrelated test cases. Setup is explicit. |
| Deterministic | Given the same setup and input, expected result is the same on every run. Flaky-by-design tests are flagged, not hidden. |

### PLC Verification Methods

Every test case declares one verification method from this list. These match the methods accepted in `plc-requirements-authoring` acceptance criteria.

| Method | Use for |
|---|---|
| Automated test | Behavior covered by unit, integration, e2e, contract, or property-based code. CI-runnable. |
| Manual test | Behavior verified by a human running a defined procedure (UI, hardware-in-loop, edge cases that resist automation). |
| Inspection | Behavior verified by reading code, config, or documentation against a checklist (e.g., presence of a header, log format). |
| Analysis | Behavior verified by reasoning over data, traces, models, or static analysis output (e.g., complexity bound, schema diff). |
| Demonstration | Behavior verified by exercising the system in a representative scenario where the observable outcome is sufficient evidence. |

### PLC Test Layers

Every test plan considers the following layers and either includes test cases for each or explicitly marks the layer N/A with a reason. Do not silently drop a layer.

| Layer | Purpose |
|---|---|
| Unit | Verify single components, functions, or classes in isolation. |
| Integration | Verify interactions between components, modules, or services within the change scope. |
| System / e2e | Verify the feature behaves correctly in a representative deployment (the system from the user's vantage point). |
| Security | Verify security-design implications: input bounds, auth checks, secret handling, privilege boundaries, secure defaults. |
| Performance / Accuracy | Verify KPI requirements: latency, throughput, accuracy, regression deltas. Required when any FR/NFR cites a measurable threshold. |
| Manual / Exploratory | Verify behavior that resists automation, plus targeted exploratory passes for high-risk areas. |
| Compatibility / Platform | Verify behavior across supported platforms, OS versions, configurations, or backward-compatibility surfaces named in requirements. |
| Regression | Verify previously passing behavior in adjacent areas still passes after this change. |

### Test Case Quality Rules

- Each test case has an ID of the form `TC-<n>` that is unique within the test plan.
- Cite at least one requirement ID or acceptance criterion ID per test case in a `Verifies` field. A test case that verifies nothing should not exist; either link it or remove it.
- Cite the design component the test exercises when known, taken from the brief's Traceability table.
- Setup, action, and expected fields are concrete enough that an unfamiliar engineer can execute the case.
- For automated tests, name the test file path and the test function or describe-it name where they will live or already live (e.g., `tests/test_rate_limit.py::test_burst`). For inherited tests, cite the existing path.
- For manual tests, include the role expected to execute it (QA, security PIC, reviewer) and the time budget if known.
- For performance/accuracy cases, name the metric, the baseline, the threshold, and how the metric is measured.
- If a test case is deferred (cannot be implemented in this work unit), state the reason and the follow-up owner in `Notes`.

### Right-Sizing

Test cases must prove coverage, not pad it. The *Layer Coverage*, *Requirement Coverage*, and *Acceptance Criterion Coverage* tables are what prove a plan reaches every layer / Req / AC — the per-layer Test Cases tables do not need a separate row for every `(layer × requirement)` cell.

- **Prefer parametrized cases over near-duplicates.** When the same setup-action-expected shape can verify N variations (different inputs, different entry points, different error paths), write one parametrized `TC-<n>` whose `Setup` field lists the parameter rows and whose `Verifies` field lists every Req/AC ID it covers. Two near-identical TCs with the same assertion under a different name is a smell.
- **One TC may legitimately cover many Req IDs.** A coverage table cell like `AC-3 → TC-7` is satisfied just as well by a parametrized row as by a dedicated test case. Citing five AC IDs in one TC's `Verifies` field is fine when those ACs share a single assertion shape.
- **Layer coverage is per-layer, not per-(layer, requirement).** A layer is "Covered" when it contains at least one test case that exercises that layer's purpose — not when every requirement has its own row in that layer's table. Add a per-layer row only when the layer's purpose surfaces a check the unit-layer cases cannot.
- **Right-sizing heuristic.** For a small feature (≤ ~100 LoC, ≤ ~5 FRs, ≤ ~10 ACs), aim for 5–10 total test cases across all in-scope layers. For a medium feature (≤ ~500 LoC, ≤ ~15 FRs), 10–25. Beyond that, scale at your judgement — but every additional case must add an assertion that no earlier case already covers, not just a new cell in the layer × requirement grid.

The point is signal density. A 30-line CLI verified by 20 test cases means the reviewer can't tell which cases catch real regressions and which are restatements. Consolidate first, then expand only when the *Requirement Coverage* or *Layer Coverage* tables surface a real gap.

Avoid:

- Vague expected results: `works`, `looks good`, `behaves correctly`, `no errors`. Replace with the concrete observable outcome.
- "Should not crash" as the only assertion. Add the positive behavior assertion too.
- Test cases that verify the same thing under different names. Merge or remove duplicates.
- One TC per `(layer, requirement)` cell when a single parametrized TC could prove the same coverage. The Layer Coverage table — not the per-layer Test Cases table row count — is what proves a layer is covered.
- Test cases that depend on the order other test cases ran.
- Implicit setup ("assume the test database is in a known state"). Make the setup explicit or reference the fixture that creates it.

### Review Checklist

For each individual test case:

- Is the verification method correct for what the case checks?
- Does it cite at least one Req ID or AC ID it verifies?
- Are setup, action, and expected concrete and reproducible?
- For automated tests, is the test file path named?
- For performance/accuracy, are metric, threshold, and measurement named?

For the test plan as a whole:

- Does every acceptance criterion (AC-*) have at least one test case that cites it?
- Does every functional requirement (FR-*) have at least one test case that cites it directly or through an AC?
- Does every non-functional requirement (NFR-*) have at least one test case, or a recorded reason it is verified outside this plan (e.g., release-gate performance run)?
- Is every PLC test layer either included or explicitly marked N/A with reason?
- Is the plan right-sized for the feature? For a small feature (≤ ~100 LoC, ≤ ~5 FRs, ≤ ~10 ACs) the total test-case count should land in the 5–10 range. If the count is materially higher, identify near-duplicates that should be consolidated into parametrized cases before sign-off.
- Are entry and exit criteria defined?
- Is out-of-scope verification listed so reviewers know what this plan does *not* cover?
- Are environment, fixtures, data sets, and tools listed concretely?

Use a subagent for this review checklist when subagents are available and the active tool policy permits delegation. Treat this as an independent PLC test plan quality review, not as a second authoring pass. If subagents are unavailable, run the checklist locally and note that the subagent review was skipped.

Suggested subagent prompt:

```text
Review this draft PLC L1 test plan against the PLC test plan quality checklist. Check individual test cases for ambiguity, executability, traceability to a Req ID or AC ID, concrete setup/action/expected, named test file paths for automated cases, and named metric/threshold/measurement for performance cases. Check the plan as a whole for AC and Req coverage, layer coverage with explicit N/A reasons, entry/exit criteria, listed out-of-scope verification, and named environment/fixtures/data sets/tools. Also check right-sizing: flag near-duplicate cases that should be consolidated into one parametrized TC, and flag plans whose total test-case count is materially higher than the sizing heuristic (5–10 cases for a ≤ ~100 LoC / ≤ ~5 FRs feature; 10–25 for ≤ ~500 LoC). Return findings only, grouped by test case ID or plan-level issue, with concise remediation suggestions.
```

## Workflow

Track this checklist during the work:

```markdown
Test Plan Authoring Progress:
- [ ] Step 1: Confirm requirements + design feature brief is available
- [ ] Step 2: Source and template preflight
- [ ] Step 3: Load existing test artifacts in this repo
- [ ] Step 4: Decompose verification needs and review with the author
- [ ] Step 5: Draft test cases by layer
- [ ] Step 6: Run subagent review checklist
- [ ] Step 7: Refine with the author
- [ ] Step 8: Write final markdown output
- [ ] Step 9: Optionally persist test plan to external source
- [ ] Step 10: Update the feature brief metadata
- [ ] Step 11: Recommend review and signoff before proceeding
```

### Step 1 - Confirm Requirements + Design Feature Brief Is Available

Before drafting, confirm that the canonical feature brief for the active mode (`agentic_plc/work_products/<feature-name>/brief.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-brief.md`) exists and contains:

- Functional requirements (FR-*)
- Non-functional requirements (NFR-*)
- Acceptance criteria (AC-*) with verification method per row
- Design delta with affected components and interfaces
- Traceability rows mapping requirements to design and (where present) code/tests

If the brief lacks any of these, do not draft a plan. Ask the user for the correct brief path. If no brief exists, tell the user to run `plc-requirements-authoring` and then `plc-design-authoring` first.

Read and retain:

- Requirement IDs, AC IDs, and existing verification methods declared in the brief
- Design components, interfaces, security implications, and KPI claims
- Open questions, assumptions, and proposed modifications

Also check whether a canonical test plan already exists at the active-mode path (`agentic_plc/work_products/<feature-name>/test-plan.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-test-plan.md`). If it does, this is a **re-run** on an existing plan, not a fresh draft. Read the existing plan end-to-end before continuing and retain:

- The highest TC-* ID currently in use (Step 5 reserves IDs from here, not 1).
- Existing test cases by ID, layer, and `Verifies` field — so a re-run preserves them by default and only changes what the author asks to change.
- The existing *Requirement Coverage*, *Acceptance Criterion Coverage*, and *Layer Coverage* tables — so the re-run can show the author what the deltas are before overwriting.
- Any `Advisory Notes` entries that named deferred TCs or recorded source-skip reasons — these must survive the re-run unless the author asks to remove them.

### Step 2 - Source and Template Preflight

Run active discovery before drafting. A previous pilot run produced a test plan that did not follow the team's STP template and silently ignored an existing test plan; this step prevents both failure modes.

Run discovery first, then report findings, then interview.

#### 2a. Search for templates

Look for an STP/test-plan template the team may have placed in the repo:

- `plc/templates/`, `.plc/templates/`, `plc/docs/`, and `docs/` for files whose names contain `stp`, `test-plan`, `verification`, or `template` (case-insensitive).
- The repo root for files matching `*template*.md` or `*template*.pdf`.

Capture each candidate path. If none are found, fall back to the bundled [test-plan-template.md](assets/test-plan-template.md) and state that explicitly to the author.

#### 2b. Search for existing test plans or STP documents

Look for existing test plan artifacts before drafting:

- `plc/docs/`, `.plc/`, and `docs/` for files matching `*STP*`, `*test-plan*`, `*test_plan*`, or `*verification*` (case-insensitive, both `.md` and `.pdf`).
- **The repo root (max-depth 1)** for files matching `STP.md`, `STP.pdf`, `*STP*.md`, `*STP*.pdf`, `test-plan.md`, `test-plan.pdf`, `test_plan.md`, or `*verification*.md`. A repo with `STP.md` sitting next to `README.md` is a common layout and must not be missed. If the team uses `.docx` for STPs, add `STP.docx` and `*test-plan*.docx` to this list explicitly.
- `.plc/briefs/` for prior test plans on adjacent features that may carry reusable conventions. **Treat the current feature's canonical test-plan path as a re-run signal, not an adjacent-feature hint** — it is handled per Step 1's re-run rules and Step 5's ID reservation rules.
- Top-level `README.md`, `CLAUDE.md`, and any `plc/docs/README*` for inline references or links to canonical test plans.

Capture each match with its path.

#### 2c. Search for external test-system references

The repo may reference an external system that holds canonical test artifacts:

- Grep `README.md`, `CLAUDE.md`, `plc/docs/`, and top-level docs for the strings `Jama`, `TestRail`, `nSpect`, `ScanSpect`, `Jenkins`, or test-management URLs.
- Grep the same locations for test-ID patterns such as `TC-\d+`, `TEST-\d+`, or any project-specific prefix that looks like a team convention.

Capture each reference (file path, line, surrounding context).

#### 2d. Report findings to the author

Before any interview question, tell the author exactly what preflight found, in this shape:

```text
Test plan source preflight summary:
- Templates found: <paths, or "none - falling back to bundled test-plan-template.md">
- Local STP/test plan documents found: <paths, or "none">
- External test-system references found: <Jama URLs, TestRail/Jenkins links, ID conventions, or "none">
```

Reporting these findings up front is required - the skill must say which template and source documents it used (or considered).

#### 2e. Interview the author

After the preflight report, ask:

1. Which discovered template should I use? Default is the bundled `test-plan-template.md` if no team template was found.
2. Which discovered STP or test plan should I treat as the source of truth? Or is the source somewhere preflight did not find?
3. Which verification layers are in scope for this feature, and which are intentionally N/A? Default: include all PLC test layers; mark a layer N/A only with a stated reason.
4. What test environments, fixtures, data sets, or tools are available for this work unit (CI runner, GPU, sandbox, staging, hardware-in-loop, accuracy benchmark)?
5. Are there time-budget or resource constraints that affect verification scope (e.g., perf run takes 90 min, manual signoff requires QA scheduling)?

Handle the STP source as follows:

- **Local Markdown or PDF:** Read it directly if Markdown; for PDF, extract the section structure and the existing test-case IDs so new cases don't collide.
- **External system (TestRail, Jenkins, Jama, etc.):** If a relevant MCP server is configured, use it to fetch the existing cases. Otherwise ask the author to export the relevant section.
- **No STP source accessible:** If preflight surfaced references but the author cannot or will not provide source input, do not silently continue. Capture a clear `Skip reason for source` to repeat in the final plan's `Advisory Notes` (for example, "TestRail project FOO referenced in `README.md:L42` but author has no access from this session; proceeding without external STP"). Then continue with the ID rules in *Step 5*.

#### 2f. Exit criteria for this step

Before moving to Step 3 you must have:

- The preflight summary from 2d on record (for inclusion in the final plan).
- An explicit choice from the author of which template, which source, which layers, and which environment context to use; or recorded skip reasons.

### Step 3 - Load Existing Test Artifacts In This Repo

Even when no STP exists, the repo itself reveals conventions:

- Test directory layout (`tests/`, `__tests__/`, `spec/`, `e2e/`, language-idiomatic locations).
- Framework in use (pytest, unittest, jest, mocha, go test, cargo test, etc.) and any wrappers (`make test`, `nox`, `tox`, `npm test`).
- Existing test file naming conventions (`test_*.py`, `*_test.go`, `*.spec.ts`).
- Existing test IDs or naming schemes the team uses inside test names (`test_REQ_1_*`, `test_AC1_*`).
- Fixture, factory, mock, and stub conventions.
- CI configuration (`.gitlab-ci.yml`, `.github/workflows/`, `Jenkinsfile`) - which jobs run which test layers.
- The PLC security scan evidence schema (`plc-evidence.jsonl`) - so security cases align with the existing structure.

Use what you find to make every drafted test case point at a realistic file path and follow the existing framework's idioms. Do not invent a test framework or convention the repo does not already use.

**Good-example reading for project-native style.** Templates and good examples have orthogonal jobs: the selected STP template (Step 2) defines section contract and required fields; an accessible good-example test plan calibrates style, density, and heading depth. Read both whenever they are available — do **not** skip the good-example pass just because a template was found.

When an existing test plan (`.plc/briefs/<other-feature>-test-plan.md` or a team STP/STR document) is accessible, use it as the project's style reference. Look at the document's actual shape — heading hierarchy and depth, test-case row template (table vs prose-and-bullet), setup/action/expected format, how layers are sectioned, how trace links are written (`Verifies: AC-1` vs `Verifies: FR-1, AC-1`), how performance thresholds are recorded, and the level of detail the team treats as canonical per case. Apply that style on top of the template's section structure. Record the matched style in the plan's `Advisory Notes` (one line, e.g., "Style matched existing test plan: cases as table; Verifies field comma-separated; perf thresholds inline"). If template and good example conflict, keep the template's required fields and choose the good example for density and wording — call the conflict out for the reviewer.

If existing plans look inconsistent with each other, pick the most-recently-updated or most-consistent convention and note which sample drove the choice.

### Step 4 - Decompose Verification Needs

Inspect the feature brief and the codebase observations from Step 3. Produce a short decomposition for author review before drafting cases:

- For each FR-*, list the verification methods that apply and the layers where the method belongs (e.g., FR-1 → automated test at unit and integration layers).
- For each NFR-*, do the same; flag NFRs that require special environments (perf rig, security review) or release-gate verification (BlackDuck/OSRB, ScanSpect performance bench) and mark them as out-of-scope-locally with a recorded reason.
- For each AC-*, identify which case(s) will verify it. Every AC must end up with at least one TC reference; an unmappable AC is a coverage gap, not a quietly skipped row.
- Identify regression risks (adjacent areas the change could break) and propose targeted regression cases.
- List required environments, fixtures, data sets, tools, and external services.
- Flag layers that are N/A and the reason (e.g., "no UI changed - manual exploratory N/A").

Present the decomposition to the author and confirm before drafting full test cases. Ask targeted follow-ups for ambiguous coverage or missing environment information.

### Step 5 - Draft Test Cases By Layer

Draft test cases using the PLC L1 test plan quality guidance in this skill. Group cases by layer in the same order as the *PLC Test Layers* table so a reviewer can see coverage by layer.

For each case, fill the required fields per *Test Case Quality Rules*:

- `ID`: TC-<n> unique within this plan.
- `Layer`: one of the PLC Test Layers.
- `Verifies`: comma-separated list of Req IDs and/or AC IDs.
- `Design ref`: design components or interfaces exercised, from the brief Traceability table when known.
- `Method`: one of the PLC Verification Methods.
- `Setup`, `Action`, `Expected`: concrete fields.
- `Test artifact`: test file path + function/describe name for automated; role + procedure for manual; checklist or analysis target for inspection/analysis/demonstration.
- `Environment`: required infra, fixtures, data sets, tools.
- `Notes`: deferral reasons, dependencies on infrastructure not yet available, links to baseline references.

#### ID handling when source is missing or partial

Preflight findings drive ID choice:

- **Re-run on an existing canonical test plan** (Step 1 detected the active-mode test-plan path): the local plan is the source of truth for ID allocation. Reserve the next IDs from the highest TC-* already in that plan and preserve every existing TC ID verbatim. Do not renumber on rewrite. If an external STP source is *also* loaded, the local plan still wins for the local namespace; reconcile against the external source per Step 8's re-run rules.
- **Source loaded and the next available test-case IDs are known**: reserve the next IDs in the existing convention. Do not invent IDs that may collide with existing cases.
- **Source referenced but not accessible** (Step 2e skip reason recorded): treat any new IDs as local drafts. Prefix them with `DRAFT-` (for example, `DRAFT-TC-1`, `DRAFT-TC-2`) and state in `Advisory Notes` that these are local drafts that must be remapped against the canonical source before they enter TestRail or the STP. Never reuse an ID surfaced in preflight unless the author confirmed that case is being modified.
- **No source referenced anywhere**: use the simple `TC-1`, `TC-2` convention and note in `Advisory Notes` that no canonical source was found, so these IDs are local to this plan.

#### Coverage assertions in the plan

After drafting cases, write the coverage view explicitly into the plan:

- A `Requirement Coverage` table mapping each FR-* / NFR-* to the TC-IDs that verify it. Requirements with zero TC-IDs are coverage gaps and must be listed in `Advisory Notes`.
- An `Acceptance Criterion Coverage` table mapping each AC-* to the TC-IDs that verify it. ACs with zero TC-IDs are coverage gaps and must be listed in `Advisory Notes`.
- A `Layer Coverage` table marking each PLC Test Layer as `Covered` / `N/A` / `Deferred` with reason.

These tables are not optional. They are the primary value of this plan: reviewers, PLC PICs, and downstream validation work consume them to confirm that every requirement and acceptance criterion has at least one verification target. In particular, `plc-acceptance-validation` reads the *Acceptance Criterion Coverage* table verbatim to map each `AC-*` to its `TC-*` pointers when classifying acceptance status, and `plc-traceability-matrix` reads the *Requirement Coverage* table to populate the Test column of the audit-quality Req ↔ Design ↔ Code ↔ Test ↔ Evidence matrix. Treat the table shapes (header columns, ID conventions) as a stable contract — downstream tooling and skills will key off `FR-*`, `NFR-*`, `AC-*`, and `TC-*` IDs.

### Step 6 - Run Subagent Review Checklist

After drafting test cases and coverage tables, and before presenting a final plan, run the review checklist through a subagent when subagents are available and the active tool policy permits delegation.

Give the subagent:

- The feature brief (requirements + design + acceptance criteria)
- The draft test plan
- The Requirement / AC / Layer coverage tables
- Any deferred coverage or open questions

Ask the subagent to return only findings and remediation suggestions. Do not ask it to rewrite the full plan unless the author explicitly requested that. Incorporate valid findings into the next draft and mention material unresolved review findings in `Advisory Notes`.

If subagents are unavailable, run the same checklist locally and state that the subagent review was skipped.

### Step 7 - Refine With the Author

Work iteratively:

- Show the decomposition, the test case table grouped by layer, and the three coverage tables.
- Ask the author to confirm verification methods, environment assumptions, deferrals, and any author-only context (institutional knowledge about flaky areas, retired tests, vendor constraints).
- Revise based on feedback.
- Re-check coverage after revision; a removed test case may re-open an AC coverage gap that needs a replacement case or an explicit deferral entry.

If the author asks for a best-effort draft without another round trip, proceed and clearly list assumptions and unresolved questions in `Advisory Notes`.

### Step 8 - Write Final Markdown Output

Write the final markdown using the template the author selected in Step 2e. Store it using the *Test Plan Location* rules above (`agentic_plc/work_products/<feature-name>/test-plan.md` in workspace mode, otherwise `.plc/briefs/<feature-name>-test-plan.md`).

If a canonical test plan already exists at that path (Step 1 detected it), this step is an **update**, not a replace:

- Preserve every existing TC ID and its `Verifies` mapping unless the author has explicitly asked to modify or remove it. The point of the re-run is to extend or refine the plan, not to renumber existing cases.
- Refresh the *Requirement Coverage*, *Acceptance Criterion Coverage*, and *Layer Coverage* tables to reflect the merged set (existing + new + modified TCs). Removed TCs must be reflected here too — a deleted TC that left an AC uncovered must surface as a coverage gap in `Advisory Notes`.
- Update existing per-layer Test Cases tables in place — append new rows, edit modified rows, and only delete a row when the author has explicitly asked for that case to go. Do not duplicate headings or fork the plan into multiple sections for the same layer.
- Record the re-run in `Advisory Notes`: list every TC that was **added**, **modified**, or **removed**, with a one-line reason per row. A reviewer should be able to read just `Advisory Notes` and understand what changed between this run and the prior run.
- Preserve prior `Advisory Notes` entries (deferred TCs, source-skip reasons, etc.) unless the underlying condition has been resolved — in which case the entry can be removed and a closing note added.

These re-run rules cover the canonical mirror only. If Step 2e selected a writable team STP or an external test-management system as the source of truth, the same diff (added / modified / removed TCs) must also be propagated to that source in Step 9 so the two locations stay in sync.

Template choice drives output structure:

- **Team STP template selected.** Use its section structure as the output skeleton. Then ensure the following Agentic PLC fields are present, mapping them into the closest matching section if the team template names them differently: per-layer test cases with the required fields, `Requirement Coverage`, `Acceptance Criterion Coverage`, `Layer Coverage`, `Entry Criteria`, `Exit Criteria`, `Out-of-Scope Verification`, `Environments And Fixtures`, `Sources Used`, `Advisory Notes`, and the metadata header captured in [test-plan-template.md](assets/test-plan-template.md) (status, repo, branch, commit, feature brief path, test plan path, template used, STP source used). Record any mapping you had to do (which Agentic PLC field landed under which team-template section) in `Advisory Notes` so a downstream reader can find each field.
- **Bundled `test-plan-template.md` selected** (preflight found no team template, or author chose the bundled one). Use it directly with no mapping notes.
- **Team template cannot accommodate a required Agentic PLC field** (for example, a strict canonical STP form with no free-text section). Say that explicitly in `Advisory Notes`, write the missing field at the end of the document under a clearly-labelled `## Advisory Notes` heading, and call the deviation out for the reviewer.

In `Advisory Notes`, include:

- A **Sources Used** block that lists the template path used (or "bundled `test-plan-template.md`" if no team template was found), every STP/test plan document consulted (with path or URL), and every external test-system reference surfaced in preflight. If a source was referenced but not accessible, include the skip reason from Step 2e here verbatim.
- If any IDs use the `DRAFT-` prefix from Step 5, state explicitly that these are local draft IDs that must be remapped before the canonical STP or test-management system is updated.
- Any FR / NFR / AC with zero TC coverage, with the reason and the proposed follow-up (file a follow-up task, defer to release-gate verification, accept gap with reviewer approval).
- Any test layer marked `N/A` with the reason.
- Assumptions, unresolved questions, environment dependencies that the work unit cannot satisfy, and any verification intentionally deferred to a later sprint or to a release gate.

Never claim PLC compliance or approval. State that the draft supports PLC L1 test plan authoring and recommend that the developer route it through the team's review/signoff workflow before proceeding with implementation or treating it as accepted verification scope.

### Step 9 - Optionally Persist Test Plan to External Source

If Step 2e identified a writable team STP or an external test-management system as the source of truth, ask the author whether to update that source now. The canonical mirror was already written in Step 8; this step propagates the new and updated TCs back to the team's preferred location so the two stay in sync.

Handle each source type as follows:

- **Local Markdown STP** (e.g., `plc/docs/<feature>-stp.md`): offer to edit the document directly. Apply the same update-don't-replace semantics as Step 8 — preserve existing TC IDs, update tables in place rather than fork sections, and record the diff (added / modified / removed TCs) in the team STP's `Advisory Notes` block (or equivalent change-log section if the team template names it differently).
- **Local PDF STP**: do not edit the PDF directly. Offer the author a paste-ready markdown block containing the new and modified TCs plus the refreshed coverage tables, sized so it can be dropped into the canonical STP by hand.
- **TestRail / Jama / qTest / other test-management system**: if a relevant MCP server is configured, ask before writing changes through it, and only proceed if the MCP supports safe updates. Otherwise output the new and modified TCs as a paste-ready block keyed by the external system's expected schema (e.g., TestRail's section / case-id / steps / expected-result fields).
- **Source was referenced in Step 2e but not accessible** (skip reason recorded): do not attempt to write. Repeat the skip reason in `Advisory Notes` and remind the author that the canonical mirror is the only authoritative output of this run.

If the team STP and the canonical mirror have diverged — for example, the team edited the STP between this run and the prior re-run — surface the diff in `Advisory Notes` and ask the author which side wins. Do not silently overwrite either side; a conflict that the skill resolves on its own is the same failure mode the re-run rules in Step 8 were written to prevent.

If no external source was selected in Step 2e, skip this step entirely. The canonical mirror at the active-mode test-plan path is the single output of this run.

### Step 10 - Update the Feature Brief Metadata

After writing the test plan (and optionally persisting it to the external source in Step 9), update the canonical feature brief at the active-mode brief path:

- Add or update a `Test plan path` row in the metadata table with the path to the test plan written in Step 8.
- Do not duplicate the full test plan content into the brief. The brief stores a pointer, not a copy.
- If the brief contains an older Test Implications section that is now superseded by the test plan, leave the section in place but add a one-line note referring readers to the test plan for the authoritative case list. Do not delete the existing section in a way that would obscure history.

### Step 11 - Recommend Review and Signoff Before Proceeding

End every authoring run by recommending that the developer get the drafted test plan reviewed and signed off before proceeding with implementation, test infrastructure work, or treating it as accepted verification scope. Present this as a recommendation from the skill, not as a hard requirement. Mention likely reviewers when relevant, such as the feature owner, QA lead, security PIC, SRE/operations for environment-dependent cases, and the requirements author for AC coverage confirmation.

Never claim PLC compliance or approval. State that the draft supports PLC L1 test plan authoring and still needs the team's normal review/signoff workflow if they choose to use one.

## Completion Event

At the end of the run, record a completion event when `aplc` is available. Use `PASS` for a completed test plan with no material gaps, `WARN` when AC coverage gaps or assumptions remain, `SKIP` with `skip_reason` when required inputs are unavailable, and `ERROR` with `error_reason` for unexpected failures. Keep the event compact and omit artifact paths.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"validation","check":"plc-test-plan-authoring","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"validation","check":"plc-test-plan-authoring","tool":"agentic-plc-skill","status":"PASS","runtime_decision":"allow","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
