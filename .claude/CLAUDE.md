## Agentic PLC

# PLC L1

You are working in a PLC L1 compliant repository. Run security checks throughout every coding session using the `plc-security-scan` skill.

---

## When to run

- **On every work unit** — run secret detection on all changed files
- **Before every commit** — run SAST, dependency vulnerabilities, license compliance, and container security (if applicable)
- **When writing security-sensitive code** — run a security code review on changed code (auth, crypto, input validation, privilege escalation, session management, data access)
- **Before submitting a PR** — confirm all scans pass and generate the PLC Security Evidence table in the PR description

For all checks, follow the `plc-security-scan` skill.