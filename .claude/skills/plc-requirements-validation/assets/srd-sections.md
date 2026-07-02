# SRD Template Sections — Semantic Reference

This file describes the expected areas in an SRD (Software Requirements Document) based on the
PLC-L1 BasicPLC template. Each entry is defined by **intent and content**, not exact heading names.
The SRD may be in any format (PDF, HTML, doc, md) and section names will vary.

When checking an SRD for template compliance, look for the **presence of content matching each
area's purpose and content indicators**. Use the example headings as hints, not as exact matches.

---

## Area 1: Document Control

- **Purpose:** Establish authorship, versioning, approval chain, and review history.
- **Required:** Yes
- **Missing severity:** Critical
- **Content indicators:**
  - Document title, author(s), revision date or number, document state (e.g., Draft, Approved, Released)
  - Approver list with names and dates
  - Reviewer list with names and dates (should include impacted BUs and internal customer/partner representatives)
  - Revision history showing dates, authors, and summaries of changes
- **Example headings:** "Document Control", "Documentation Control", "Revision History", "Approval History", "Change Log", "Version History"
- **What to flag if missing:** The SRD lacks traceability of authorship, reviews, and approval. This is required for PLC audit readiness.

---

## Area 2: Overview / Introduction

- **Purpose:** Explain why the project exists, what problem it solves, its main functions, primary stakeholders, and who is developing it.
- **Required:** Yes
- **Missing severity:** Critical
- **Content indicators:**
  - Project motivation or business justification
  - Problem statement
  - High-level description of main functions or capabilities
  - Identification of stakeholders (users, customers, internal teams)
  - Identification of the development team or organization
- **Example headings:** "Introduction", "Overview", "Project Overview", "Purpose", "Background", "Executive Summary", "Scope"
- **What to flag if missing:** The SRD does not establish the context or purpose of the project, making it impossible to evaluate whether requirements are complete.

---

## Area 3: Assumptions

- **Purpose:** Document assumptions used in the project definition and architecture that, if wrong, could invalidate requirements.
- **Required:** Yes
- **Missing severity:** Warning
- **Content indicators:**
  - Environmental assumptions (target deployment, infrastructure)
  - Platform or technology assumptions
  - Data or input assumptions
  - Assumptions about external systems or services
- **Example headings:** "Assumptions", "Project Assumptions", "Key Assumptions", "Assumptions and Preconditions"
- **What to flag if missing:** Undocumented assumptions can lead to hidden risks and misalignment between requirements and implementation.

---

## Area 4: Constraints

- **Purpose:** Document limitations the project faces during development — platforms, environments, legacy systems, tooling, etc.
- **Required:** Yes
- **Missing severity:** Warning
- **Content indicators:**
  - Platform or OS constraints
  - Performance or resource constraints
  - Development environment limitations
  - Legacy system compatibility requirements
  - Tooling or technology restrictions
- **Example headings:** "Constraints", "Project Constraints", "Limitations", "Restrictions", "Boundaries"
- **What to flag if missing:** Without documented constraints, the design may not account for real-world limitations.

---

## Area 5: Dependencies

- **Purpose:** Identify internal and external teams or systems the project depends on and what they must deliver.
- **Required:** Yes
- **Missing severity:** Warning
- **Content indicators:**
  - Table or list of teams, companies, or systems depended upon
  - What each dependency must deliver (APIs, libraries, infrastructure, data, etc.)
  - Internal vs. external dependency distinction
- **Example headings:** "Dependencies", "External Dependencies", "Internal Dependencies", "Third-Party Dependencies", "Dependency Matrix"
- **What to flag if missing:** Untracked dependencies are a top risk for schedule and integration failures.

---

## Area 6: Definitions, Acronyms, Abbreviations

- **Purpose:** Define project-specific or domain-specific terminology to ensure consistent understanding.
- **Required:** Optional
- **Missing severity:** Info
- **Content indicators:**
  - Glossary or table of terms and their definitions
  - Acronym expansions
- **Example headings:** "Definitions", "Glossary", "Acronyms", "Terminology", "Abbreviations", "Definitions and Acronyms"
- **What to flag if missing:** Domain-specific terms may be misunderstood without a glossary. Low risk but recommended for clarity.

---

## Area 7: References

- **Purpose:** Link to upstream documents, standards, prior SRDs, design docs, Jira/NVBug tickets, or external specifications.
- **Required:** Optional
- **Missing severity:** Info
- **Content indicators:**
  - List of related documents with titles, revision info, and links or locations
  - Standards references (e.g., NIST, OWASP)
  - Links to project plans, prior SRDs, architecture docs
- **Example headings:** "References", "Related Documents", "External References", "Applicable Documents", "Bibliography"
- **What to flag if missing:** Without references, reviewers cannot trace requirements to upstream decisions or standards.

---

## Area 8: Use Cases / User Stories

- **Purpose:** Describe who the users are, what tasks they perform, and why — providing the behavioral context for requirements.
- **Required:** Yes
- **Missing severity:** Critical
- **Content indicators:**
  - User/actor identification
  - Use case descriptions or user stories ("As a ... I want ... so that ...")
  - Task flows or interaction descriptions
  - Table or structured list of use cases with names and descriptions
- **Example headings:** "Use Cases", "User Stories", "Usage Scenarios", "User Scenarios", "Functional Scenarios", "Actors and Use Cases"
- **What to flag if missing:** Without use cases, requirements lack behavioral context and testability. This is a core PLC-L1 requirement.

---

## Area 9: Requirements Table

- **Purpose:** Capture the full set of software requirements in a structured, traceable format.
- **Required:** Yes
- **Missing severity:** Critical
- **Content indicators:**
  - Structured table or list with unique IDs for each requirement (e.g., REQ-1, REQ-2)
  - Each requirement has: ID, Title, Description, Type
  - Enhanced format (mandatory for GPU Driver): adds QA Applicable, Use Case Scenario, Verification Criteria, Tools Required
  - Requirements are categorized by type (Functional, Non-Functional, Interface, Security, etc.)
- **Example headings:** "Requirements", "Software Requirements", "Feature Requirements", "Requirement Specifications", "Functional Requirements", "System Requirements"
- **What to flag if missing:** The requirements table is the core deliverable of the SRD. Without it, the document fails its fundamental purpose.

---

## Area 10: Requirement Type Coverage

- **Purpose:** Ensure the SRD considered all applicable PLC requirement types, not just functional requirements.
- **Required:** Yes
- **Missing severity:** Warning
- **Content indicators:** The SRD should address (or explicitly mark as not applicable) the following 13 PLC requirement types:

| # | Requirement Type | Description |
|---|-----------------|-------------|
| 1 | Functional | Intended behavior — services, tasks, or functions the system performs |
| 2 | System (Non-Functional) | Scalability, capacity, recoverability, maintainability, serviceability, manageability, data integrity, usability |
| 3 | Interface | User interface, control interface, data interface, system-to-system interfaces |
| 4 | Safety | Safety constraints and safety-critical requirements from PLC-L3 work products |
| 5 | KPI | Capacity, throughput, response time, utilization, MTBF, availability, memory usage, scalability |
| 6 | Platform | All hardware and software that will be supported |
| 7 | Security | Encryption, logging, communications restrictions, data integrity checks, access control |
| 8 | Legal and Standards | Standards to support, legal constraints specific to the project |
| 9 | Telemetry | Instrumentation — remote accessibility, automatic field reports |
| 10 | Backward Compatibility | Support for legacy systems |
| 11 | Virtualization | Emulation or simulation requirements |
| 12 | Automatability | Full automation enablement via scripting or white-box (API) testing |
| 13 | Other Non-Functional | Any remaining non-functional requirements dictating how the system operates |

- **Example headings:** These types usually appear as values in the "Type" column of the requirements table, or as sub-sections grouping requirements.
- **What to flag if missing:** If a requirement type is not represented in the requirements table and not marked N/A, flag it as a gap. Security, Functional, and Platform types are especially important to cover.

---

## Area 11: Test Automatability

- **Purpose:** Ensure features can be automated for testing, with security considerations for test hooks and APIs.
- **Required:** Conditional — mandatory for GPU Driver releases, recommended for others
- **Missing severity:** Warning (Critical for GPU Driver projects)
- **Content indicators:**
  - Security implications of test hooks (not expanding attack surface, no backdoors)
  - Security best practices for automation (use standard interfaces, preserve trust relationships, validate inputs)
  - Test automatability requirements checklist (API definitions, HW config info, valid data sets, logging)
  - Distinction between "Must Have" and "Nice to Have" automatability requirements
  - Coverage of Open Box and Closed Box Automation test domains
- **Example headings:** "Test Automatability", "Testability", "Automation Requirements", "Test Requirements", "QA Automation", "Test Hooks"
- **What to flag if missing:** Without test automatability coverage, QA cannot plan automation and the feature may be untestable at scale.

---

## Sources

- [PLC-L1 BasicPLC SRD Template](https://nvidia.atlassian.net/wiki/spaces/PLCH/pages/2584970602)
- [PLC Guidelines: Requirement Types](https://nvidia.atlassian.net/wiki/spaces/PLCH/pages/2584971477)
- [PLC Requirements Review Criteria](https://gitlab-master.nvidia.com/arc-eng/dac/nv-security-dac/-/blob/main/docs/plc%20docs/plc-criteria/plc-requirements-review.md)
