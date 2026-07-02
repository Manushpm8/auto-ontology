# PLC Requirements Validation Report Template

Use this template when generating the validation report. Replace all `<placeholder>` values
with actual content. Remove any finding groups that have zero findings.

---

```markdown
# PLC Requirements Validation Report

**Generated:** <date and time>
**SRD Source:** <SRD document location or path>
**SADD Source:** <SADD document location or path>
**Project Type:** <e.g., GPU Driver, Cloud Service, General — affects conditional checks>

---

## Summary

| Finding Group | Count | Critical | Warning | Info |
|---|---|---|---|---|
| A: SRD Template Compliance | <n> | <n> | <n> | <n> |
| B: SRD Requirements Not Covered in SADD | <n> | <n> | <n> | <n> |
| C: SADD Design Without SRD Traceability | <n> | <n> | <n> | <n> |
| **Total** | **<n>** | **<n>** | **<n>** | **<n>** |

---

## Group A: SRD Template Compliance

Checks whether the SRD contains all expected content areas per the PLC-L1 template.
Matching is semantic — section names do not need to match exactly.

### [<SEVERITY>] A-<n>: <Short title>

**Expected area:** <Name of the expected SRD area from srd-sections.md>
**Status:** Missing / Incomplete / Present but insufficient
**Details:** <Describe what is missing or incomplete>
**Remediation:** <Specific suggestion for what to add or fix>

<!-- Repeat for each finding -->

---

## Group B: SRD Requirements Not Covered in SADD

Checks whether each SRD requirement has corresponding architecture/design coverage in the SADD.

### [<SEVERITY>] B-<n>: <Requirement ID or topic> — No SADD coverage

**SRD requirement:** <Summary of the requirement — ID, title, description>
**Expected in SADD:** <What kind of design/architecture coverage is expected>
**Remediation:** <Suggest adding a SADD section or design element covering this requirement>

<!-- Repeat for each finding -->

---

## Group C: SADD Design Without SRD Traceability

Checks whether each SADD design element traces back to an SRD requirement.

### [<SEVERITY>] C-<n>: SADD "<component or section>" — No SRD requirement found

**SADD design element:** <Summary of the design element or component>
**Expected in SRD:** <What kind of requirement should justify this design>
**Remediation:** <Suggest adding a corresponding SRD requirement, or confirm this is intentional and document the rationale>

<!-- Repeat for each finding -->

---

## Recommended Next Steps

1. Address all **Critical** findings before proceeding to design or code review.
2. Review **Warning** findings with project leads and security PIC.
3. **Info** findings are recommendations — address at your discretion.
4. After fixing findings, re-run this validation to confirm resolution.

---

## Disclaimer

This report is **advisory only**. It does NOT satisfy PLC requirements and does NOT replace
deterministic release validation (qualified scanners, evidence generation, policy enforcement
via LaunchAPI). Your work must still go through the governed PLC release process.
```

---

## Severity Definitions

Use these severity levels consistently:

| Severity | Meaning | Action |
|----------|---------|--------|
| **Critical** | Core PLC content is missing or a required requirement has no design coverage. Blocks PLC readiness. | Must fix before proceeding. |
| **Warning** | Important content is missing or a notable gap exists. Does not block but increases risk. | Should fix; review with leads. |
| **Info** | Recommended content is absent or a minor gap exists. Low risk. | Fix at discretion. |

## Severity Assignment Guidelines

**Group A (SRD Template Compliance):**
- Critical: Missing Overview, Use Cases, Requirements Table, or Document Control
- Warning: Missing Assumptions, Constraints, Dependencies, Requirement Type Coverage, Test Automatability
- Info: Missing Definitions/Glossary, References

**Group B (SRD → SADD):**
- Critical: Functional or Security requirement with no SADD coverage
- Warning: Non-functional, KPI, or Platform requirement with no SADD coverage
- Info: Low-priority or nice-to-have requirement with no SADD coverage

**Group C (SADD → SRD):**
- Warning: Major design component or architectural decision with no SRD traceability
- Info: Minor utility component or infrastructure detail with no explicit SRD requirement
