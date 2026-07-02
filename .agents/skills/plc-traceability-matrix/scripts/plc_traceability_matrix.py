#!/usr/bin/env python3
"""Traceability-matrix CLI for automation and report generation.

Stdlib-only. Reads the FR/NFR tables and optional `## Traceability` table
from a feature brief, joins per-requirement evidence rows from the
evidence log, and emits Markdown or JSON.

Use --mode compact for the small flat table used by orchestrators and
external pipeline integrations. Use --mode full for the full advisory
report shape with gaps, coverage summary, sources used, and the required
disclaimer.

Usage:
    plc_traceability_matrix.py \\
        --feature-brief .plc/briefs/<feature>-brief.md \\
        --evidence-log  .plc/briefs/<feature>-evidence.jsonl \\
        [--scope-fr FR-024] \\
        [--mode compact|full] \\
        [--output stdout] \\
        [--format markdown]

Exit codes:
    0  - matrix emitted (zero or more rows; caller inspects payload)
    2  - input error (brief missing, invalid args)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[2] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

import plc_workspace

FR_NFR_RE = re.compile(r"^(FR|NFR)-\d+$")

PASS_STATUSES = {"pass", "green", "clean"}
PASS_DECISIONS = {"accepted"}

PLC_REQUIREMENT_TYPES = (
    "Functional",
    "System (Non-Functional)",
    "Interface",
    "Safety",
    "KPI",
    "Platform",
    "Security",
    "Legal and Standards",
    "Telemetry",
    "Backward Compatibility",
    "Virtualization",
    "Automatability",
    "Other Non-Functional",
)

DISCLAIMER = """## Disclaimer

This matrix is **advisory only**. It surfaces traceability gaps between
requirements, design, code, tests, and recorded evidence so reviewers can
spot missing or stale links early. It does NOT satisfy PLC requirements,
replace formal audit-trail attestation, or replace deterministic release
validation (qualified scanners, evidence generation, policy enforcement
via LaunchAPI, TAVA, nSpect, ScanSpect, SonarQube, Coverity, BlackDuck,
OSRB). A `COVERED` row is "current for workflow reuse" — not release-gate
clearance. Your work must still go through the governed PLC release
process."""


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


def _resolve_existing_path(repo_root: Path, workspace_context: dict, path_arg: str) -> Path | None:
    raw = Path(path_arg)
    if raw.is_absolute():
        candidate = raw.resolve()
        return candidate if candidate.exists() else None
    candidates = [(repo_root / raw).resolve()]
    workspace_root = workspace_context.get("workspace_root")
    if isinstance(workspace_root, Path):
        candidates.append((workspace_root / raw).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def display_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def resolve_brief(repo_root: Path, workspace_context: dict, brief_arg: str) -> Path:
    candidate = _resolve_existing_path(repo_root, workspace_context, brief_arg)
    if candidate is None or not candidate.is_file():
        raise SystemExit(f"ERROR: --feature-brief not found: {brief_arg}")
    return candidate


def resolve_evidence_log(repo_root: Path, workspace_context: dict, evidence_arg: str | None, brief: Path) -> Path:
    if evidence_arg:
        candidate = _resolve_existing_path(repo_root, workspace_context, evidence_arg)
        if candidate is not None:
            return candidate
        raw = Path(evidence_arg)
        workspace_root = workspace_context.get("workspace_root")
        if not raw.is_absolute() and isinstance(workspace_root, Path):
            return (workspace_root / raw).resolve()
        return (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    stem = brief.stem
    feature = stem[: -len("-brief")] if stem.endswith("-brief") else stem
    if brief.name == "brief.md":
        return (brief.parent / "evidence.jsonl").resolve()
    per_feature = brief.parent / f"{feature}-evidence.jsonl"
    if per_feature.is_file():
        return per_feature
    return (repo_root / "plc-evidence.jsonl").resolve()


def parse_table_section(brief_text: str, section_heading: str) -> list[dict]:
    """Parse rows from a Markdown table under the given `## ` heading."""
    lines = brief_text.splitlines()
    in_section = False
    headers: list[str] = []
    rows: list[dict] = []
    section_lower = section_heading.lower()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower().startswith(f"## {section_lower}")
            headers = []
            continue
        if not in_section:
            continue
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not headers:
            headers = [c.lower() for c in cells]
            continue
        if all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def parse_requirements(brief_text: str) -> list[dict]:
    """Parse FR and NFR tables, returning a unified list of req rows.

    Each row: {req_id, text, category}.
    """
    out: list[dict] = []
    for section in ("Proposed Functional Requirements", "Proposed Non-Functional Requirements"):
        for row in parse_table_section(brief_text, section):
            req_id = row.get("id") or row.get("req id") or row.get("requirement id")
            if not req_id or not FR_NFR_RE.match(req_id):
                continue
            text = row.get("requirement") or row.get("text") or ""
            category = row.get("category") or ("Functional" if req_id.startswith("FR-") else "Other Non-Functional")
            out.append({"req_id": req_id, "text": text, "category": category})
    return out


def parse_traceability_table(brief_text: str) -> dict[str, dict]:
    """Parse the optional `## Traceability` table.

    Returns a dict keyed by requirement ID (FR-*/NFR-*) with values
    {repo, revision, design, code, test_evidence}.
    """
    out: dict[str, dict] = {}
    for row in parse_table_section(brief_text, "Traceability"):
        req_id = row.get("requirement") or row.get("requirement id") or row.get("req id")
        if not req_id or not FR_NFR_RE.match(req_id):
            continue
        out[req_id] = {
            "repo": (
                row.get("repo")
                or row.get("repo id")
                or row.get("repo_id")
                or row.get("repository")
                or ""
            ).strip(),
            "revision": (
                row.get("revision")
                or row.get("repo revision")
                or row.get("repo_revision")
                or row.get("commit")
                or ""
            ).strip(),
            "design": (row.get("design element") or row.get("design") or "").strip(),
            "code": (row.get("code/files") or row.get("code") or "").strip(),
            "test_evidence": (row.get("test/evidence") or row.get("test") or "").strip(),
        }
    return out


def parse_ac_parent_map(brief_text: str) -> dict[str, str]:
    """Parse AC -> parent requirement links from the Acceptance Criteria table."""
    out: dict[str, str] = {}
    for row in parse_table_section(brief_text, "Acceptance Criteria"):
        ac_id = row.get("id") or row.get("ac id") or row.get("ac_id")
        req_id = (
            row.get("requirement id")
            or row.get("requirement_id")
            or row.get("req id")
            or row.get("req_id")
            or row.get("linked requirement")
        )
        if ac_id and ac_id.startswith("AC-") and req_id and FR_NFR_RE.match(req_id):
            out[ac_id] = req_id
    return out


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
                continue
    return out


def is_ancestor_of_head(repo_root: Path, commit_sha: str) -> bool:
    """Return True if commit_sha is reachable from HEAD."""
    if not commit_sha:
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
        return True
    result = subprocess.run(
        ("git", "-C", str(repo_root), "merge-base", "--is-ancestor", commit_sha, "HEAD"),
        capture_output=True,
    )
    return result.returncode == 0


def _row_repo_key(row: dict) -> str:
    return (
        str(row.get("repo_id") or "")
        or str(row.get("repo") or "")
        or str(row.get("repo_url") or "")
    )


def _evidence_revision(row: dict) -> str:
    return str(row.get("repo_revision") or row.get("commit") or "")


def repo_root_for_pointer(pointer: str, row: dict | None, workspace_context: dict) -> Path | None:
    row = row or {}
    repo_key = pointer or _row_repo_key(row)
    repo_url = str(row.get("repo_url") or "")
    entry = plc_workspace.repo_by_id_or_url(workspace_context, repo=repo_key, repo_url=repo_url)
    root = entry.get("root") if entry else None
    return root if isinstance(root, Path) and root.is_dir() else None


def evidence_repo_is_accessible(row: dict, workspace_context: dict) -> bool:
    """Reject repo-attributed workspace evidence for repos unavailable now."""
    if not isinstance(workspace_context.get("workspace_root"), Path):
        return True
    repo_key = _row_repo_key(row)
    repo_url = str(row.get("repo_url") or "")
    if not repo_key and not repo_url:
        return True
    entry = plc_workspace.repo_by_id_or_url(workspace_context, repo=repo_key, repo_url=repo_url)
    if not entry:
        return False
    root = entry.get("root")
    return entry.get("status") == "accessible" and isinstance(root, Path) and root.is_dir()


def evidence_matches_req(row: dict, req_id: str, ac_parent: dict[str, str]) -> bool:
    """Return True if an evidence row resolves to the given requirement."""
    if row.get("ref") == req_id:
        return True
    ac_id = row.get("ac_id")
    if ac_id and ac_parent.get(ac_id) == req_id:
        return True
    ref = row.get("ref")
    return bool(ref and ac_parent.get(ref) == req_id)


def last_verified_for_ref(req_id: str, rows: list[dict], repo_root: Path,
                          ac_parent: dict[str, str], workspace_context: dict,
                          repo_hint: str = "") -> tuple[str, bool, dict | None]:
    """Find the most recent satisfied evidence row for a requirement.

    Returns (display_string, is_stale, row). display_string is
    "<short_sha>  <YYYY-MM-DD>" or "<...> (stale)" or "" if no row.
    """
    indexed = [
        (i, r) for i, r in enumerate(rows)
        if evidence_matches_req(r, req_id, ac_parent)
        and evidence_repo_is_accessible(r, workspace_context)
        and (r.get("status", "").lower() in PASS_STATUSES or r.get("decision", "").lower() in PASS_DECISIONS)
    ]
    if not indexed:
        return "", False, None
    # Most recent timestamp first; for ties, later file position wins.
    indexed.sort(key=lambda pair: (pair[1].get("timestamp", ""), pair[0]), reverse=True)
    row = indexed[0][1]
    sha = _evidence_revision(row)
    short = sha[:7] if sha else "unknown"
    date = row.get("timestamp", "").split("T")[0]
    freshness_root = repo_root_for_pointer(repo_hint, row, workspace_context) or repo_root
    stale = bool(sha) and not is_ancestor_of_head(freshness_root, sha)
    producer = row.get("producer")
    annotation = ""
    if producer == "external":
        annotation = f" (external: {row.get('producer_id', 'unknown')})"
    display = f"{short}  {date}".strip()
    if stale:
        display = f"{display} (stale)"
    display = f"{display}{annotation}"
    return display, stale, row


def compute_status(design: str, code: str, test_evidence: str, last_verified_display: str, stale: bool) -> str:
    if not code:
        return "GAP"
    if not design or not test_evidence:
        return "PARTIAL"
    if not last_verified_display:
        return "PARTIAL"
    if stale:
        return "PARTIAL"
    return "COVERED"


def build_matrix_rows(brief_text: str, evidence_rows: list[dict], repo_root: Path,
                       scope_fr: str | None, workspace_context: dict | None = None) -> list[dict]:
    reqs = parse_requirements(brief_text)
    if scope_fr:
        reqs = [r for r in reqs if r["req_id"] == scope_fr]
    trace = parse_traceability_table(brief_text)
    ac_parent = parse_ac_parent_map(brief_text)
    workspace_context = workspace_context or {}

    out: list[dict] = []
    for req in reqs:
        trace_row = trace.get(req["req_id"], {})
        repo = trace_row.get("repo", "")
        revision = trace_row.get("revision", "")
        design = trace_row.get("design", "")
        code = trace_row.get("code", "")
        test_evidence = trace_row.get("test_evidence", "")
        last_verified_display, stale, evidence_row = last_verified_for_ref(
            req["req_id"], evidence_rows, repo_root, ac_parent, workspace_context, repo
        )
        if evidence_row:
            repo = repo or _row_repo_key(evidence_row)
            revision = revision or _evidence_revision(evidence_row)
        status = compute_status(design, code, test_evidence, last_verified_display, stale)
        out.append({
            "req_id": req["req_id"],
            "req_text": req["text"],
            "category": req["category"],
            "repo": repo,
            "revision": revision,
            "design": design,
            "code": code,
            "test_id": test_evidence,
            "last_verified": last_verified_display,
            "status": status,
        })
    return out


def format_markdown(rows: list[dict], scope_fr: str | None) -> str:
    if not rows:
        scope_note = f" (scope: {scope_fr})" if scope_fr else ""
        return f"_No requirements in scope{scope_note}._"
    lines = [
        "| Req ID | Req text | Repo | Revision | Design ref | Code | Test ID | Last verified | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['req_id']} | {r['req_text']} | {r.get('repo') or '—'} | "
            f"{r.get('revision') or '—'} | {r['design'] or '—'} | "
            f"{r['code'] or '—'} | {r['test_id'] or '—'} | "
            f"{r['last_verified'] or '—'} | {r['status']} |"
        )
    return "\n".join(lines)


def format_json(rows: list[dict], scope_fr: str | None, brief_path: str,
                evidence_log: str) -> str:
    payload = {
        "scope_fr": scope_fr,
        "feature_brief": brief_path,
        "evidence_log": evidence_log,
        "rows": rows,
    }
    return json.dumps(payload, sort_keys=True, indent=2)


def _requirement_type(row: dict) -> str:
    if row.get("req_id", "").startswith("FR-"):
        return "Functional"
    category = (row.get("category") or "").strip().lower()
    mapping = {
        "functional": "Functional",
        "system": "System (Non-Functional)",
        "non-functional": "System (Non-Functional)",
        "performance": "KPI",
        "kpi": "KPI",
        "interface": "Interface",
        "safety": "Safety",
        "platform": "Platform",
        "security": "Security",
        "legal": "Legal and Standards",
        "standards": "Legal and Standards",
        "telemetry": "Telemetry",
        "backward compatibility": "Backward Compatibility",
        "compatibility": "Backward Compatibility",
        "virtualization": "Virtualization",
        "automatability": "Automatability",
    }
    return mapping.get(category, "Other Non-Functional")


def _missing_columns(row: dict) -> list[str]:
    missing = []
    if not row.get("code"):
        missing.append("Code")
    if not row.get("design"):
        missing.append("Design ref")
    if not row.get("test_id"):
        missing.append("Test ID")
    last_verified = row.get("last_verified") or ""
    if not last_verified:
        missing.append("Last verified")
    elif "(stale)" in last_verified:
        missing.append("Last verified is stale")
    return missing


def add_full_report_fields(rows: list[dict], scope_fr: str | None, brief_path: str,
                           evidence_log: str) -> dict:
    coverage = {
        req_type: {"type": req_type, "COVERED": 0, "PARTIAL": 0, "GAP": 0}
        for req_type in PLC_REQUIREMENT_TYPES
    }
    gaps = []
    for row in rows:
        req_type = _requirement_type(row)
        coverage.setdefault(req_type, {"type": req_type, "COVERED": 0, "PARTIAL": 0, "GAP": 0})
        coverage[req_type][row["status"]] = coverage[req_type].get(row["status"], 0) + 1
        if row["status"] in {"PARTIAL", "GAP"}:
            gaps.append({
                "req_id": row["req_id"],
                "status": row["status"],
                "missing": _missing_columns(row),
                "next_step": "fill the missing traceability pointer and rerun verification evidence",
            })
    return {
        "scope_fr": scope_fr,
        "feature_brief": brief_path,
        "evidence_log": evidence_log,
        "rows": rows,
        "coverage_summary": list(coverage.values()),
        "gaps": gaps,
        "sources_used": [brief_path, evidence_log],
    }


def format_full_markdown(rows: list[dict], scope_fr: str | None, brief_path: str,
                         evidence_log: str) -> str:
    payload = add_full_report_fields(rows, scope_fr, brief_path, evidence_log)
    lines = ["# Traceability Matrix", ""]
    if scope_fr:
        lines.extend([f"Scope: {scope_fr}", ""])

    lines.append(format_markdown(rows, scope_fr))

    lines.extend(["", "## Gaps", ""])
    if not payload["gaps"]:
        lines.append("_No PARTIAL or GAP rows._")
    else:
        lines.extend(["| Req ID | Status | Missing / stale columns | Suggested next step |", "|---|---|---|---|"])
        for gap in payload["gaps"]:
            missing = ", ".join(gap["missing"]) if gap["missing"] else "review row"
            lines.append(f"| {gap['req_id']} | {gap['status']} | {missing} | {gap['next_step']} |")

    lines.extend(["", "## Coverage Summary", ""])
    lines.extend(["| Type | Covered | Partial | Gap |", "|---|---:|---:|---:|"])
    for item in payload["coverage_summary"]:
        if item["COVERED"] or item["PARTIAL"] or item["GAP"]:
            lines.append(
                f"| {item['type']} | {item['COVERED']} | "
                f"{item['PARTIAL']} | {item['GAP']} |"
            )

    lines.extend([
        "",
        "## Sources Used",
        "",
        f"- Feature brief: {brief_path}",
        f"- Evidence log: {evidence_log}",
        "",
        DISCLAIMER,
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Traceability-matrix CLI. Reads FR/NFR rows from a feature brief, "
            "joins per-requirement evidence rows from the evidence log, and "
            "emits either a compact matrix or a full advisory report."
        ),
    )
    parser.add_argument(
        "--feature-brief", required=True,
        help="Path to the feature brief.",
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
            "Filter the matrix to a single FR/NFR ID. Omit to emit every row."
        ),
    )
    parser.add_argument(
        "--mode", choices=("compact", "full"), default="compact",
        help=(
            "Matrix mode. Use 'compact' for the small flat table, or 'full' "
            "for the advisory report."
        ),
    )
    parser.add_argument(
        "--output", choices=("stdout", "file"), default="stdout",
        help="Where to write the matrix. Default: stdout.",
    )
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown",
        help="Output format. Default: markdown.",
    )
    parser.add_argument(
        "--output-file",
        help=(
            "When --output file, write to this path. Defaults to "
            "<brief-dir>/<feature>-traceability.<ext>."
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
    workspace_context = plc_workspace.discover_workspace(repo_root)
    if workspace_context.get("errors"):
        print(
            "ERROR: workspace manifest validation failed: "
            + "; ".join(str(e) for e in workspace_context.get("errors", [])),
            file=sys.stderr,
        )
        return 2

    brief = resolve_brief(repo_root, workspace_context, args.feature_brief)
    brief_text = brief.read_text(encoding="utf-8", errors="replace")
    evidence_log = resolve_evidence_log(repo_root, workspace_context, args.evidence_log, brief)
    evidence_rows = read_evidence_rows(evidence_log)

    rows = build_matrix_rows(brief_text, evidence_rows, repo_root, args.scope_fr, workspace_context)

    display_root = workspace_context.get("workspace_root") if isinstance(workspace_context.get("workspace_root"), Path) else repo_root
    brief_rel = display_relative(brief, display_root)
    log_rel = display_relative(evidence_log, display_root)

    if mode == "full":
        if args.format == "markdown":
            rendered = format_full_markdown(rows, args.scope_fr, brief_rel, log_rel)
        else:
            rendered = json.dumps(
                add_full_report_fields(rows, args.scope_fr, brief_rel, log_rel),
                sort_keys=True,
                indent=2,
            )
    elif args.format == "markdown":
        rendered = format_markdown(rows, args.scope_fr)
    else:
        rendered = format_json(rows, args.scope_fr, brief_rel, log_rel)

    matrix_artifact: Path | None = None
    if args.output == "stdout":
        print(rendered)
    else:
        if args.output_file:
            out_path = Path(args.output_file)
            if not out_path.is_absolute():
                out_path = (repo_root / out_path).resolve()
        else:
            ext = "md" if args.format == "markdown" else "json"
            stem = brief.stem
            if brief.name == "brief.md":
                out_path = brief.parent / f"traceability.{ext}"
            else:
                feature = stem[: -len("-brief")] if stem.endswith("-brief") else stem
                out_path = brief.parent / f"{feature}-traceability.{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        matrix_artifact = out_path
        print(f"wrote {out_path}", file=sys.stderr)

    if isinstance(workspace_context.get("workspace_root"), Path):
        non_covered = [row for row in rows if row.get("status") != "COVERED"]
        status = "PASS" if not non_covered else "WARN"
        reason = f"Generated traceability matrix with {len(rows)} row(s)"
        if non_covered:
            reason += f"; {len(non_covered)} row(s) are partial or gap"
        plc_workspace.record_phase_outcome(
            workspace_context,
            brief.parent,
            phase="traceability_matrix",
            action="generate_matrix",
            status=status,
            reason=reason,
            repo_root=repo_root,
            artifact=matrix_artifact,
            invocation_type="cli",
            working_dir=repo_root,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
