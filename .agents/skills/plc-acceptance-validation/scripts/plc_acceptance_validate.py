#!/usr/bin/env python3
"""Acceptance-validation CLI for automation and report generation.

Stdlib-only. Reads the `## Acceptance Criteria` table from a feature brief,
filters by --scope-fr when supplied, resolves each AC against the evidence
log, and emits JSON or Markdown.

Use --mode compact for the small machine-readable verdict used by
orchestrators and external pipeline integrations. Use --mode full for the
full advisory report shape with summary counts, per-AC sections, findings,
scope notes, and the required disclaimer.

Usage:
    plc_acceptance_validate.py \\
        --feature-brief .plc/briefs/<feature>-brief.md \\
        --evidence-log  .plc/briefs/<feature>-evidence.jsonl \\
        [--scope-fr FR-024] \\
        [--mode compact|full] \\
        [--output stdout] \\
        [--format json]

Exit codes:
    0  - verdict computed and emitted (verdict value may be PASS / FAIL /
         UNVERIFIED / MIXED / EMPTY — caller inspects the JSON)
    2  - input error (brief missing, evidence log unreadable, invalid args)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REF_RE = re.compile(r"^(FR|NFR|AC)-\d+$")
FR_NFR_RE = re.compile(r"^(FR|NFR)-\d+$")
AC_RE = re.compile(r"^AC-\d+$")

# Status vocabulary mapped to verdict categories. Same vocabulary used by
# plc-acceptance-validation/SKILL.md → *Status Vocabulary*.
PASS_STATUSES = {"pass", "green", "clean"}
FAIL_STATUSES = {"fail", "red", "findings", "critical", "error", "blocked"}
PASS_DECISIONS = {"accepted"}
FAIL_DECISIONS = {"rejected"}
# PENDING decisions and SKIP / WARN / PARTIAL statuses do not satisfy
# acceptance; we leave the AC UNVERIFIED in that case.

# Test-shaped check types — these are the rows that primarily prove an AC.
# Scan-shaped checks (scanspect-run, nspect-result, other) corroborate but
# are secondary citations. When both kinds are present, we cite the most
# recent definitive test-shaped row so the verdict's evidence pointer
# matches what the AC's `Verification method` calls for.
TEST_SHAPED_CHECKS = frozenset({
    "external-test",
    "jenkins-build",
    "perf-report",
    "accuracy-report",
    "manual-signoff",
})

DISCLAIMER = """## Disclaimer

This report is **advisory only**. It validates that each acceptance criterion has
a cited verification pointer in this repo and records the pointer's resolved
outcome. It does NOT satisfy PLC requirements, replace formal acceptance review,
or replace deterministic release validation (qualified scanners, evidence
generation, policy enforcement via LaunchAPI, TAVA, nSpect, ScanSpect, SonarQube,
Coverity, BlackDuck, OSRB). A PASS here is "current for workflow reuse" — not
release-gate clearance. Your work must still go through the governed PLC release
process."""


def _is_pass(row: dict) -> bool:
    return (
        row.get("status", "").lower() in PASS_STATUSES
        or row.get("decision", "").lower() in PASS_DECISIONS
    )


def _is_fail(row: dict) -> bool:
    return (
        row.get("status", "").lower() in FAIL_STATUSES
        or row.get("decision", "").lower() in FAIL_DECISIONS
    )


def git_output(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ("git", "-C", str(repo_root), *args),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.strip() or None


def detect_repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd().resolve()
    output = git_output(cwd, "rev-parse", "--show-toplevel")
    return Path(output).resolve() if output else cwd


def resolve_brief(repo_root: Path, brief_arg: str) -> Path:
    candidate = (repo_root / brief_arg).resolve() if not Path(brief_arg).is_absolute() else Path(brief_arg).resolve()
    if not candidate.is_file():
        raise SystemExit(f"ERROR: --feature-brief not found: {brief_arg}")
    return candidate


def resolve_evidence_log(repo_root: Path, evidence_arg: str | None, brief: Path) -> Path:
    if evidence_arg:
        candidate = (repo_root / evidence_arg).resolve() if not Path(evidence_arg).is_absolute() else Path(evidence_arg).resolve()
        return candidate
    # Default: <brief-dir>/<feature>-evidence.jsonl
    stem = brief.stem
    feature = stem[: -len("-brief")] if stem.endswith("-brief") else stem
    per_feature = brief.parent / f"{feature}-evidence.jsonl"
    if per_feature.is_file():
        return per_feature
    return (repo_root / "plc-evidence.jsonl").resolve()


def parse_ac_table(brief_text: str) -> list[dict]:
    """Parse rows from the `## Acceptance Criteria` table.

    Returns a list of dicts with keys: ac_id, req_id, criterion, method, idea.
    Tolerant of column-order variants: matches by inferred role from the header.
    """
    lines = brief_text.splitlines()
    in_section = False
    in_table = False
    headers: list[str] = []
    rows: list[dict] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower().startswith("## acceptance criteria")
            in_table = False
            headers = []
            continue
        if not in_section:
            continue
        if not stripped.startswith("|"):
            # Blank line or prose ends the table once it has started.
            if in_table and not stripped:
                in_section = False
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not headers:
            headers = [c.lower() for c in cells]
            continue
        # Skip the alignment separator row "|---|---|..."
        if all(set(c) <= {"-", ":", " "} for c in cells):
            in_table = True
            continue
        if len(cells) != len(headers):
            # Malformed row; skip.
            continue
        row = dict(zip(headers, cells))
        ac_id = row.get("id") or row.get("ac id") or row.get("ac_id")
        req_id = (
            row.get("requirement id")
            or row.get("requirement_id")
            or row.get("req id")
            or row.get("req_id")
            or row.get("linked requirement")
        )
        criterion = row.get("criterion") or row.get("acceptance criterion") or ""
        method = (
            row.get("verification method")
            or row.get("method")
            or ""
        )
        idea = row.get("verification idea") or row.get("idea") or ""
        if not ac_id or not AC_RE.match(ac_id):
            continue
        rows.append({
            "ac_id": ac_id,
            "req_id": req_id or "",
            "criterion": criterion,
            "method": method,
            "idea": idea,
        })
    return rows


def read_evidence_rows(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    out: list[dict] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Mirrors plc-traceability-matrix tolerance: skip malformed lines.
                continue
    return out


def evidence_for_ac(ac_id: str, rows: list[dict]) -> list[dict]:
    """Return evidence rows matching an AC. Match on row.ref == ac_id OR row.ac_id == ac_id.

    Sorted most-recent-first by (timestamp, file position). The file-position
    tiebreaker matters when two rows share a second-precision timestamp (e.g.,
    a bug-fix flow writes a failing repro row and a passing post-fix row
    within the same second); the later-written row is treated as more recent.
    """
    indexed = [(i, r) for i, r in enumerate(rows) if r.get("ref") == ac_id or r.get("ac_id") == ac_id]
    indexed.sort(key=lambda pair: (pair[1].get("timestamp", ""), pair[0]), reverse=True)
    return [r for _, r in indexed]


def classify_ac_compact(ac: dict, evidence_rows: list[dict]) -> dict:
    """Classify a single AC against its evidence rows.

    Compact mode answers "did the AC pass?" for automation.
    Test-shaped evidence rows (external-test, jenkins-build, perf-report,
    accuracy-report, manual-signoff) are the *primary* citation for AC
    acceptance — the AC's `Verification method` typically calls for a test.
    Scan-shaped rows (scanspect-run, nspect-result, other) corroborate but
    are not primary acceptance evidence.

    The rule:

      1. Among test-shaped rows, take the most recent definitive one
         (most-recent-wins within test rows). If it records pass → PASS,
         if it records fail → FAIL, cited at that row.
      2. If no test-shaped row is definitive, fall back to scan-shaped rows
         and apply the same most-recent-wins rule.
      3. Otherwise UNVERIFIED.

    This differs from the agent-driven full workflow described in
    SKILL.md, which uses conservative "any FAIL ever recorded" semantics
    for audit. See SKILL.md -> *Bundled CLI - Compact and Full Modes*.

    Returns a dict with keys: ac_id, req_id, status, evidence (or null), notes.
    """
    method = (ac.get("method") or "").strip().lower()
    if method in ("n/a", "na", "not applicable"):
        return {
            "ac_id": ac["ac_id"],
            "req_id": ac["req_id"],
            "status": "N/A",
            "evidence": None,
            "notes": "verification method is N/A",
        }
    matching = evidence_for_ac(ac["ac_id"], evidence_rows)
    if not matching:
        return {
            "ac_id": ac["ac_id"],
            "req_id": ac["req_id"],
            "status": "UNVERIFIED",
            "evidence": None,
            "notes": "no evidence row references this AC",
        }

    test_rows = [r for r in matching if r.get("check") in TEST_SHAPED_CHECKS]
    scan_rows = [r for r in matching if r.get("check") not in TEST_SHAPED_CHECKS]

    # First, look at test-shaped rows: most-recent-wins on definitive outcomes.
    for row in test_rows:
        if _is_pass(row):
            return {
                "ac_id": ac["ac_id"],
                "req_id": ac["req_id"],
                "status": "PASS",
                "evidence": _evidence_summary(row),
                "notes": "",
            }
        if _is_fail(row):
            return {
                "ac_id": ac["ac_id"],
                "req_id": ac["req_id"],
                "status": "FAIL",
                "evidence": _evidence_summary(row),
                "notes": "most recent test row records failure",
            }

    # Fall back to scan-shaped rows.
    for row in scan_rows:
        if _is_pass(row):
            return {
                "ac_id": ac["ac_id"],
                "req_id": ac["req_id"],
                "status": "PASS",
                "evidence": _evidence_summary(row),
                "notes": "no test-shaped evidence; cited scan row",
            }
        if _is_fail(row):
            return {
                "ac_id": ac["ac_id"],
                "req_id": ac["req_id"],
                "status": "FAIL",
                "evidence": _evidence_summary(row),
                "notes": "no test-shaped evidence; cited scan row records failure",
            }

    return {
        "ac_id": ac["ac_id"],
        "req_id": ac["req_id"],
        "status": "UNVERIFIED",
        "evidence": _evidence_summary(matching[0]),
        "notes": "evidence rows exist but none record a satisfied outcome",
    }


def classify_ac_full(ac: dict, evidence_rows: list[dict]) -> dict:
    """Classify a single AC for the full advisory report.

    Full mode uses conservative audit semantics: any resolved failing outcome
    keeps the AC at FAIL even if another row passes. A passing row can only
    produce PASS when no failure exists. Missing evidence is MISSING instead of
    compact mode's UNVERIFIED because the report distinguishes "no pointer" from
    "pointer exists but no outcome."
    """
    method = (ac.get("method") or "").strip().lower()
    if method in ("n/a", "na", "not applicable"):
        return {
            "ac_id": ac["ac_id"],
            "req_id": ac["req_id"],
            "status": "N/A",
            "evidence": None,
            "notes": "verification method is N/A",
            "severity": "Info",
        }

    matching = evidence_for_ac(ac["ac_id"], evidence_rows)
    if not matching:
        return {
            "ac_id": ac["ac_id"],
            "req_id": ac["req_id"],
            "status": "MISSING",
            "evidence": None,
            "notes": "no test case, evidence row, or signoff was located for this AC",
            "severity": _severity_for_status(ac, "MISSING"),
        }

    for row in matching:
        if _is_fail(row):
            return {
                "ac_id": ac["ac_id"],
                "req_id": ac["req_id"],
                "status": "FAIL",
                "evidence": _evidence_summary(row),
                "notes": "at least one evidence row records an unsatisfied outcome",
                "severity": _severity_for_status(ac, "FAIL"),
            }

    for row in matching:
        if _is_pass(row):
            return {
                "ac_id": ac["ac_id"],
                "req_id": ac["req_id"],
                "status": "PASS",
                "evidence": _evidence_summary(row),
                "notes": "",
                "severity": "Info",
            }

    return {
        "ac_id": ac["ac_id"],
        "req_id": ac["req_id"],
        "status": "UNVERIFIED",
        "evidence": _evidence_summary(matching[0]),
        "notes": "evidence pointers exist but none record a definitive outcome",
        "severity": _severity_for_status(ac, "UNVERIFIED"),
    }


def _severity_for_status(ac: dict, status: str) -> str:
    req_id = ac.get("req_id", "")
    if status in {"FAIL", "MISSING"} and req_id.startswith("FR-"):
        return "Critical"
    if status in {"FAIL", "MISSING", "UNVERIFIED"}:
        return "Warning"
    return "Info"


def _evidence_summary(row: dict) -> str:
    pieces = []
    if row.get("check"):
        pieces.append(row["check"])
    if row.get("tool"):
        pieces.append(row["tool"])
    timestamp = row.get("timestamp", "")
    if timestamp:
        pieces.append(timestamp.split("T")[0])
    producer = row.get("producer")
    if producer == "external":
        producer_id = row.get("producer_id", "unknown")
        pieces.append(f"(external: {producer_id})")
    return "/".join(p for p in pieces if p) if pieces else "row"


def compute_verdict(ac_results: list[dict]) -> str:
    """Combine per-AC statuses into an overall verdict."""
    if not ac_results:
        return "EMPTY"
    statuses = {r["status"] for r in ac_results}
    if "FAIL" in statuses:
        return "FAIL"
    if statuses == {"PASS"} or statuses == {"PASS", "N/A"} or statuses == {"N/A"}:
        return "PASS"
    if "UNVERIFIED" in statuses or "MISSING" in statuses:
        return "UNVERIFIED"
    return "MIXED"


def add_full_report_fields(payload: dict) -> dict:
    counts = {status: 0 for status in ("PASS", "FAIL", "UNVERIFIED", "MISSING", "N/A")}
    severity_counts = {severity: 0 for severity in ("Critical", "Warning", "Info")}
    findings = []
    for result in payload["ac_results"]:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        severity = result.get("severity", "Info")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        if result["status"] not in {"PASS", "N/A"}:
            findings.append({
                "severity": severity,
                "ac_id": result["ac_id"],
                "status": result["status"],
                "action": result.get("notes") or "review this acceptance criterion",
            })
    findings.sort(key=lambda f: {"Critical": 0, "Warning": 1, "Info": 2}.get(f["severity"], 3))
    enriched = dict(payload)
    enriched["summary_counts"] = counts
    enriched["severity_counts"] = severity_counts
    enriched["findings"] = findings
    return enriched


def format_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, indent=2)


def format_markdown(payload: dict) -> str:
    lines = [
        f"# Acceptance Validation — verdict: {payload['verdict']}",
        "",
    ]
    if payload.get("scope_fr"):
        lines.append(f"Scope: {payload['scope_fr']}")
        lines.append("")
    if not payload["ac_results"]:
        lines.append("_No acceptance criteria in scope._")
        return "\n".join(lines)
    lines.append("| AC ID | Req | Status | Evidence | Notes |")
    lines.append("|---|---|---|---|---|")
    for r in payload["ac_results"]:
        lines.append(
            f"| {r['ac_id']} | {r.get('req_id') or '—'} | {r['status']} | "
            f"{r.get('evidence') or '—'} | {r.get('notes') or ''} |"
        )
    return "\n".join(lines)


def format_full_markdown(payload: dict) -> str:
    payload = add_full_report_fields(payload)
    lines = [
        f"# Acceptance Validation — verdict: {payload['verdict']}",
        "",
    ]
    if payload.get("scope_fr"):
        lines.extend([f"Scope: {payload['scope_fr']}", ""])

    lines.extend([
        "## Summary",
        "",
        "| Status | Count |",
        "|---|---:|",
    ])
    for status in ("PASS", "FAIL", "UNVERIFIED", "MISSING", "N/A"):
        lines.append(f"| {status} | {payload['summary_counts'].get(status, 0)} |")

    lines.extend(["", "## Acceptance Criteria", ""])
    if not payload["ac_results"]:
        lines.append("_No acceptance criteria in scope._")
    for result in payload["ac_results"]:
        lines.extend([
            f"### {result['ac_id']} — {result['status']}",
            "",
            f"- Requirement: {result.get('req_id') or '—'}",
            f"- Severity: {result.get('severity', 'Info')}",
            f"- Evidence: {result.get('evidence') or '—'}",
            f"- Notes: {result.get('notes') or '—'}",
            "",
        ])

    lines.extend(["## Findings", ""])
    if not payload["findings"]:
        lines.append("_No non-passing findings._")
    else:
        lines.extend(["| Severity | AC ID | Status | Action |", "|---|---|---|---|"])
        for finding in payload["findings"]:
            lines.append(
                f"| {finding['severity']} | {finding['ac_id']} | "
                f"{finding['status']} | {finding['action']} |"
            )

    lines.extend([
        "",
        "## Scope and Caveats",
        "",
        f"- Feature brief: {payload['feature_brief']}",
        f"- Evidence log: {payload['evidence_log']}",
        "- This CLI resolves acceptance outcomes from the feature brief and evidence log.",
        "",
        DISCLAIMER,
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Acceptance-validation CLI. Reads ACs from a feature brief, "
            "resolves them against the evidence log, and emits either a "
            "compact verdict or a full advisory report."
        ),
    )
    parser.add_argument(
        "--feature-brief", required=True,
        help="Path to the feature brief (e.g., .plc/briefs/<feature>-brief.md).",
    )
    parser.add_argument(
        "--evidence-log",
        help=(
            "Path to the evidence log. Defaults to the per-feature log beside "
            "the brief, falling back to repo-root plc-evidence.jsonl."
        ),
    )
    parser.add_argument(
        "--scope-fr",
        help=(
            "Filter ACs to those linked to this FR/NFR ID (e.g., FR-024). "
            "Omit to validate every AC in the brief."
        ),
    )
    parser.add_argument(
        "--mode", choices=("compact", "full"), default="compact",
        help=(
            "Validation mode. Use 'compact' for the small machine-readable "
            "verdict, or 'full' for the advisory report."
        ),
    )
    parser.add_argument(
        "--output", choices=("stdout", "file"), default="stdout",
        help="Where to write the verdict. Default: stdout.",
    )
    parser.add_argument(
        "--format", choices=("json", "markdown"), default="json",
        help="Output format. Default: json (machine-readable).",
    )
    parser.add_argument(
        "--output-file",
        help=(
            "When --output file, write to this path. Defaults to "
            "<brief-dir>/<feature>-acceptance-validation.<ext>."
        ),
    )
    parser.add_argument("--repo-root", help="Repo root override (defaults to git rev-parse --show-toplevel).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = args.mode

    if args.scope_fr and not FR_NFR_RE.match(args.scope_fr):
        print(f"ERROR: --scope-fr must match ^(FR|NFR)-\\d+$, got {args.scope_fr!r}", file=sys.stderr)
        return 2

    repo_root = detect_repo_root(args.repo_root)
    brief = resolve_brief(repo_root, args.feature_brief)
    brief_text = brief.read_text(encoding="utf-8", errors="replace")
    evidence_log = resolve_evidence_log(repo_root, args.evidence_log, brief)
    evidence_rows = read_evidence_rows(evidence_log)

    acs = parse_ac_table(brief_text)
    if args.scope_fr:
        acs = [a for a in acs if a["req_id"] == args.scope_fr]

    classifier = classify_ac_full if mode == "full" else classify_ac_compact
    ac_results = [classifier(a, evidence_rows) for a in acs]
    verdict = compute_verdict(ac_results)

    payload = {
        "mode": mode,
        "verdict": verdict,
        "scope_fr": args.scope_fr,
        "feature_brief": str(brief.relative_to(repo_root)) if str(brief).startswith(str(repo_root)) else str(brief),
        "evidence_log": str(evidence_log.relative_to(repo_root)) if str(evidence_log).startswith(str(repo_root)) else str(evidence_log),
        "ac_results": ac_results,
    }

    if mode == "full":
        rendered = format_json(add_full_report_fields(payload)) if args.format == "json" else format_full_markdown(payload)
    else:
        rendered = format_json(payload) if args.format == "json" else format_markdown(payload)

    if args.output == "stdout":
        print(rendered)
    else:
        if args.output_file:
            out_path = Path(args.output_file)
            if not out_path.is_absolute():
                out_path = (repo_root / out_path).resolve()
        else:
            ext = "json" if args.format == "json" else "md"
            stem = brief.stem
            feature = stem[: -len("-brief")] if stem.endswith("-brief") else stem
            out_path = brief.parent / f"{feature}-acceptance-validation.{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
