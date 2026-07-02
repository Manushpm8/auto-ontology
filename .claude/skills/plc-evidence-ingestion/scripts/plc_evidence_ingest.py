#!/usr/bin/env python3
"""Append a normalized verification-evidence row to plc-evidence.jsonl.

Stdlib-only. Mirrors source/plc-security-scan/scripts/plc_security_scan.py
conventions (base_record, append_evidence, sort_keys + separators).

Usage:
    plc_evidence_ingest.py <check> --ref <id> [pointer args] [status/decision] [optional fields]

See source/plc-evidence-ingestion/assets/evidence-types.md for per-check
required fields. The CLI rejects unknown checks, unknown status/decision
values, and orphan refs unless --allow-orphan is set.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[2] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

import plc_workspace

CHECKS = (
    "jenkins-build",
    "perf-report",
    "accuracy-report",
    "manual-signoff",
    "scanspect-run",
    "nspect-result",
    "external-test",
    "other",
)

STATUS_VALUES = (
    "green", "red", "aborted",
    "pass", "fail", "partial",
    "clean", "findings",
    "warn", "critical",
    "error", "skip", "blocked",
)
DECISION_VALUES = ("accepted", "rejected", "pending")

# Per-check required fields beyond the always-required (pillar, check, tool,
# ref, branch, commit, timestamp, status|decision).
POINTER_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "jenkins-build": ("url",),
    "perf-report": ("url_or_path",),
    "accuracy-report": ("url_or_path",),
    "manual-signoff": (),  # pointer optional; reviewer required (handled below)
    "scanspect-run": ("external_id_or_url",),
    "nspect-result": ("external_id_or_url",),
    "external-test": ("any_pointer",),  # url, path, or external_id — local pytest/jest runs use path per plc-v-model
    "other": ("any_pointer",),
}

STATUS_OR_DECISION: dict[str, str] = {
    "jenkins-build": "status",
    "perf-report": "status",
    "accuracy-report": "status",
    "manual-signoff": "decision",
    "scanspect-run": "status",
    "nspect-result": "status",
    "external-test": "status",
    "other": "either",
}

REF_RE = re.compile(r"^(FR|NFR|AC)-\d+$")
AC_RE = re.compile(r"^AC-\d+$")
TC_RE = re.compile(r"^TC-\d+$")
URL_RE = re.compile(r"^https?://")
EMBEDDED_CREDS_RE = re.compile(r"^https?://[^/@]+:[^/@]+@")
HIGH_ENTROPY_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")
TOKEN_PARAM_RE = re.compile(r"((?:token|key|secret|password|api[-_]?key)=)[^&\s]+", re.IGNORECASE)


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def p4_output(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ("p4", "-ztag", *args),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def parse_p4_ztag(output: str | None) -> dict[str, str]:
    data: dict[str, str] = {}
    if not output:
        return data
    for line in output.splitlines():
        if not line.startswith("... "):
            continue
        body = line[4:]
        key, _, value = body.partition(" ")
        if key:
            data[key] = value
    return data


def p4_info(cwd: Path) -> dict[str, str]:
    return parse_p4_ztag(p4_output(cwd, "info"))


def p4_env_info(anchor: Path | None = None) -> dict[str, str]:
    data = p4_env_values()
    root = os.environ.get("P4ROOT", "").strip()
    if root:
        root_path = Path(root)
        if root_path.is_absolute():
            resolved_root = root_path.resolve()
            if resolved_root.is_dir():
                if anchor is None:
                    data["clientRoot"] = str(resolved_root)
                else:
                    resolved_anchor = anchor.resolve()
                    try:
                        resolved_anchor.relative_to(resolved_root)
                    except ValueError:
                        if resolved_anchor == resolved_root:
                            data["clientRoot"] = str(resolved_root)
                    else:
                        data["clientRoot"] = str(resolved_root)
    if data.get("clientName") and data.get("clientRoot"):
        return data
    return {}


def p4_env_values() -> dict[str, str]:
    client = os.environ.get("P4CLIENT", "").strip()
    port = os.environ.get("P4PORT", "").strip()
    user = os.environ.get("P4USER", "").strip()
    data: dict[str, str] = {}
    if client:
        data["clientName"] = client
    if port:
        data["serverAddress"] = port
        data["p4Port"] = port
    if user:
        data["userName"] = user
    return data


def p4_workspace_metadata(repo_root: Path) -> dict:
    info = p4_info(repo_root)
    env = p4_env_values()
    env_workspace = p4_env_info(repo_root)
    warnings: list[str] = []
    if info.get("clientName"):
        metadata = {
            "scm": "p4",
            "p4_client": info.get("clientName"),
            "p4_root": info.get("clientRoot"),
            "p4_server": info.get("serverAddress") or env.get("serverAddress"),
            "p4_port": env.get("p4Port") or info.get("serverAddress"),
            "p4_user": info.get("userName") or env.get("userName"),
            "p4_metadata_source": "mixed" if env else "p4-info",
        }
        for env_name, key in (
            ("P4CLIENT", "clientName"),
            ("P4ROOT", "clientRoot"),
            ("P4PORT", "serverAddress"),
            ("P4USER", "userName"),
        ):
            env_value = os.environ.get(env_name, "").strip()
            info_value = info.get(key, "")
            if env_value and info_value and env_value != info_value:
                warnings.append(f"{env_name} differs from p4 info {key}; using p4 info")
        if warnings:
            metadata["p4_metadata_warnings"] = warnings
        return {key: value for key, value in metadata.items() if value not in (None, "")}
    if env_workspace.get("clientName") and env_workspace.get("clientRoot"):
        metadata = {
            "scm": "p4",
            "p4_client": env_workspace.get("clientName"),
            "p4_root": env_workspace.get("clientRoot"),
            "p4_server": env_workspace.get("serverAddress"),
            "p4_port": env_workspace.get("p4Port"),
            "p4_user": env_workspace.get("userName"),
            "p4_metadata_source": "env",
        }
        return {key: value for key, value in metadata.items() if value not in (None, "")}
    return {}


def detect_repo_root(explicit: str | None) -> Path:
    if explicit:
        requested = Path(explicit).resolve()
        root = p4_info(requested).get("clientRoot")
        if not root:
            root = p4_env_info(requested).get("clientRoot")
        return Path(root).resolve() if root and not git_output(requested, "rev-parse", "--show-toplevel") else requested
    cwd = Path.cwd().resolve()
    output = git_output(cwd, "rev-parse", "--show-toplevel")
    if output:
        return Path(output).resolve()
    root = p4_info(cwd).get("clientRoot")
    if not root:
        root = p4_env_info(cwd).get("clientRoot")
    return Path(root).resolve() if root else cwd


def current_branch_commit(repo_root: Path) -> tuple[str, str, dict]:
    commit = git_output(repo_root, "rev-parse", "HEAD") or ""
    branch = git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD") or ""
    metadata: dict[str, str] = {"scm": "git"} if commit else {}
    if branch in ("HEAD", "", None):
        if commit:
            short = commit[:7]
            branch = f"detached/{short}"
        else:
            info = p4_workspace_metadata(repo_root)
            client = info.get("p4_client")
            if client:
                branch = f"p4/{client}"
                commit = f"p4/{client}"
                metadata = info
            else:
                branch = "detached/unknown"
    return branch, commit, metadata


def discover_workspace_manifest(repo_root: Path) -> tuple[Path | None, dict]:
    context = plc_workspace.discover_workspace(repo_root)
    return context.get("workspace_root"), context.get("manifest", {})


def matching_workspace_repo(workspace_root: Path | None, manifest: dict, repo_root: Path) -> dict:
    if workspace_root is None:
        return {}
    context = plc_workspace.discover_workspace(workspace_root)
    return plc_workspace.matching_repo(context, repo_root)


def arg_or_env(value: str | None, env_name: str) -> str | None:
    if value:
        return value.strip()
    env_value = os.environ.get(env_name, "").strip()
    return env_value or None


def workspace_record(args: argparse.Namespace, repo_root: Path, commit: str) -> tuple[dict, str | None]:
    context = plc_workspace.discover_workspace(repo_root)
    workspace_root = context.get("workspace_root")
    manifest = context.get("manifest", {})
    repo_entry = plc_workspace.matching_repo(context, repo_root)

    workspace_id = arg_or_env(args.workspace_id, "AGENTIC_PLC_WORKSPACE_ID")
    if not workspace_id and isinstance(manifest.get("workspace_id"), str):
        workspace_id = manifest["workspace_id"].strip() or None

    workspace_root_value = plc_workspace.stable_workspace_root(
        arg_or_env(args.workspace_root, "AGENTIC_PLC_WORKSPACE_ROOT"),
        workspace_root is not None and not workspace_id,
    )

    repo_id = arg_or_env(args.repo_id, "AGENTIC_PLC_REPO_ID")
    if not repo_id and isinstance(repo_entry.get("repo_id"), str):
        repo_id = repo_entry["repo_id"].strip() or None

    repo_url = arg_or_env(args.repo_url, "AGENTIC_PLC_REPO_URL")
    if not repo_url and isinstance(repo_entry.get("url"), str):
        repo_url = repo_entry["url"].strip() or None
    if not repo_url:
        repo_url = plc_workspace.git_remote_url(repo_root)
    if repo_url:
        sanitized = plc_workspace.strip_url_credentials(repo_url)
        if sanitized != repo_url:
            print("WARN: stripped credentials/token from --repo-url", file=sys.stderr)
        repo_url = sanitized

    repo_revision = arg_or_env(args.repo_revision, "AGENTIC_PLC_REPO_REVISION") or commit

    workspace_mode = any(
        [
            args.workspace_root,
            args.workspace_id,
            args.repo_id,
            args.repo_url,
            args.repo_revision,
            os.environ.get("AGENTIC_PLC_WORKSPACE_ROOT"),
            os.environ.get("AGENTIC_PLC_WORKSPACE_ID"),
            os.environ.get("AGENTIC_PLC_REPO_ID"),
            os.environ.get("AGENTIC_PLC_REPO_URL"),
            os.environ.get("AGENTIC_PLC_REPO_REVISION"),
            workspace_root is not None,
        ]
    )
    if not workspace_mode:
        return {}, None

    context_errors = context.get("errors") or []
    if context_errors and workspace_root is not None:
        return {}, "workspace manifest validation failed: " + "; ".join(str(e) for e in context_errors)

    missing: list[str] = []
    if not (workspace_id or workspace_root_value):
        missing.append("workspace_id or workspace_root")
    if not (repo_id or repo_url):
        missing.append("repo_id or repo_url")
    if not repo_revision:
        missing.append("repo_revision")
    if missing:
        return {}, "workspace attribution requires " + ", ".join(missing)

    record: dict = {}
    if workspace_id:
        record["workspace_id"] = workspace_id
    if workspace_root_value:
        record["workspace_root"] = workspace_root_value
    if repo_id:
        record["repo_id"] = repo_id
    if repo_url:
        record["repo_url"] = repo_url
    if repo_revision:
        record["repo_revision"] = repo_revision
    return record, None


def looks_like_secret(value: str) -> bool:
    """Heuristic: a contiguous high-entropy token alone in a field probably is one."""
    if not value:
        return False
    matches = HIGH_ENTROPY_TOKEN_RE.findall(value)
    return any(len(m) >= 32 and " " not in m for m in matches)


def validate_path(path_str: str, repo_root: Path) -> str:
    p = Path(path_str)
    if p.is_absolute() or any(part == ".." for part in p.parts):
        raise SystemExit(f"--path must be repo-relative with no .. segments: {path_str}")
    resolved = (repo_root / p).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        raise SystemExit(f"--path resolves outside repo: {path_str}")
    return p.as_posix()


def validate_external_id(value: str) -> str:
    value = value.strip()
    if not value or " " in value or "/" in value or "\\" in value:
        raise SystemExit(f"--external-id must be non-empty with no whitespace or slashes: {value!r}")
    return value


def find_brief(repo_root: Path, feature_brief: str | None) -> Path | None:
    if feature_brief:
        candidate = (repo_root / feature_brief).resolve()
        if not candidate.is_file():
            raise SystemExit(f"--feature-brief not found: {feature_brief}")
        return candidate
    work_products_dir = plc_workspace.workspace_work_products_dir(repo_root)
    if work_products_dir and work_products_dir.is_dir():
        briefs = sorted(work_products_dir.glob("*/brief.md"))
        if not briefs:
            briefs = sorted(work_products_dir.glob("*/*-brief.md"))
        if len(briefs) == 1:
            return briefs[0]
        if len(briefs) > 1:
            return None  # ambiguous; caller decides whether to require --feature-brief
    briefs_dir = repo_root / ".plc" / "briefs"
    if briefs_dir.is_dir():
        briefs = sorted(briefs_dir.glob("*-brief.md"))
        if len(briefs) == 1:
            return briefs[0]
        if len(briefs) > 1:
            return None  # ambiguous; caller decides whether to require --feature-brief
    return None


def ref_in_brief(ref: str, brief_path: Path) -> bool:
    text = brief_path.read_text(encoding="utf-8", errors="replace")
    # Word-boundary match so FR-1 does not also match FR-10.
    pattern = re.compile(rf"\b{re.escape(ref)}\b")
    return pattern.search(text) is not None


def tc_in_test_plan(tc_id: str, ref: str, test_plan_path: Path) -> bool:
    text = test_plan_path.read_text(encoding="utf-8", errors="replace")
    # The TC row must exist; its Verifies cell must contain the ref ID.
    tc_pattern = re.compile(rf"\|\s*{re.escape(tc_id)}\s*\|")
    if not tc_pattern.search(text):
        return False
    for line in text.splitlines():
        if tc_pattern.search(line) and re.search(rf"\b{re.escape(ref)}\b", line):
            return True
    return False


def evidence_log_path(repo_root: Path, evidence_log: str | None, feature_brief: Path | None) -> Path:
    if evidence_log:
        return (repo_root / evidence_log).resolve()
    if feature_brief is not None:
        if feature_brief.name == "brief.md":
            return (feature_brief.parent / "evidence.jsonl").resolve()
        stem = feature_brief.stem  # e.g. low-level-router-timeout-retry-brief
        feature = stem[: -len("-brief")] if stem.endswith("-brief") else stem
        return (feature_brief.parent / f"{feature}-evidence.jsonl").resolve()
    return (repo_root / "plc-evidence.jsonl").resolve()


def append_evidence(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    plc_workspace.record_evidence_phase_outcome(path, record)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append a verification-evidence row to plc-evidence.jsonl.",
    )
    parser.add_argument("check", choices=CHECKS, help="Evidence type (see evidence-types.md).")
    parser.add_argument("--ref", required=True, help="Req ID (FR-* / NFR-*) or AC ID (AC-*) the evidence supports.")
    parser.add_argument("--tool", help="Specific tool name. Defaults to the check's canonical tool.")
    parser.add_argument("--url", help="Pointer URL (http/https).")
    parser.add_argument("--path", help="Repo-relative artifact path.")
    parser.add_argument("--external-id", help="Vendor/system identifier (SS-12345, NS-789, TR-4421, ...).")
    parser.add_argument("--status", choices=STATUS_VALUES, help="Run/scan status.")
    parser.add_argument("--decision", choices=DECISION_VALUES, help="Review decision.")
    parser.add_argument("--reviewer", help="Corp username of the human reviewer.")
    parser.add_argument("--notes", help="One-line note, <=280 chars.")
    parser.add_argument("--tc-id", help="Test plan TC ID this evidence pertains to.")
    parser.add_argument("--ac-id", help="AC ID when ref names a parent Req but the evidence also covers a specific AC.")
    parser.add_argument("--finding-id", help="Finding identifier when the evidence points at a repo finding.")
    parser.add_argument("--workspace-root", help="Parent workspace root that links multiple repos.")
    parser.add_argument("--workspace-id", help="Stable workspace identifier that links multiple repos.")
    parser.add_argument("--repo-id", help="Workspace repo identifier for the repo this record points into.")
    parser.add_argument("--repo-url", help="Repository URL for the repo this record points into.")
    parser.add_argument("--repo-revision", help="Commit SHA or revision for the repo this record points into.")
    parser.add_argument("--feature-brief", help="Path to the feature brief (defaults to the only .plc/briefs/*-brief.md when unambiguous).")
    parser.add_argument("--test-plan", help="Path to the test plan (defaults to <feature>-test-plan.md beside the brief).")
    parser.add_argument("--evidence-log", help="Path to the evidence log (defaults to per-feature or repo-root plc-evidence.jsonl).")
    parser.add_argument("--repo-root", help="Repo root override (defaults to Git toplevel, then Perforce client root).")
    parser.add_argument("--allow-orphan", action="store_true", help="Allow ref that does not appear in the feature brief (sets ref_unverified:true).")
    parser.add_argument("--worktree-digest", help="Optional sha256 over relevant evidence content.")
    parser.add_argument("--producer", choices=("local", "external"), default="local", help="Who produced this row. 'local' means the agentic-plc skills running in this repo wrote it; 'external' means an upstream pipeline (e.g., the DeepStream bug-fixer) wrote it on our behalf.")
    parser.add_argument("--producer-id", help="Pipeline identifier (e.g., 'agentic-plc-bugfix', 'deepstream-bugfix-agent'). Required when --producer external. Defaults to 'agentic-plc' when --producer local.")
    parser.add_argument("--producer-context", help="Repo and commit context of the producer (e.g., '<repo>@<sha>'). Required when --producer external. Defaults to '<branch>@<commit>' from the current worktree when --producer local.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not REF_RE.match(args.ref):
        print(f"ERROR: --ref must match ^(FR|NFR|AC)-\\d+$, got {args.ref!r}", file=sys.stderr)
        return 2
    if args.tc_id and not TC_RE.match(args.tc_id):
        print(f"ERROR: --tc-id must match ^TC-\\d+$, got {args.tc_id!r}", file=sys.stderr)
        return 2
    if args.ac_id and not AC_RE.match(args.ac_id):
        print(f"ERROR: --ac-id must match ^AC-\\d+$, got {args.ac_id!r}", file=sys.stderr)
        return 2
    if args.finding_id:
        try:
            args.finding_id = validate_external_id(args.finding_id)
        except SystemExit as exc:
            print(f"ERROR: --finding-id must be non-empty with no whitespace or slashes: {args.finding_id!r}", file=sys.stderr)
            return int(exc.code) if isinstance(exc.code, int) else 2

    requirement = STATUS_OR_DECISION[args.check]
    if requirement == "status" and not args.status:
        print(f"ERROR: {args.check} requires --status (one of {', '.join(STATUS_VALUES)})", file=sys.stderr)
        return 2
    if requirement == "decision" and not args.decision:
        print(f"ERROR: {args.check} requires --decision (one of {', '.join(DECISION_VALUES)})", file=sys.stderr)
        return 2
    if requirement == "either" and not (args.status or args.decision):
        print(f"ERROR: {args.check} requires --status or --decision", file=sys.stderr)
        return 2
    if args.status and args.decision:
        print("ERROR: --status and --decision are mutually exclusive", file=sys.stderr)
        return 2

    if args.check == "manual-signoff" and not args.reviewer:
        print("ERROR: manual-signoff requires --reviewer", file=sys.stderr)
        return 2

    if args.producer == "external":
        if not args.producer_id:
            print("ERROR: --producer external requires --producer-id", file=sys.stderr)
            return 2
        if not args.producer_context:
            print("ERROR: --producer external requires --producer-context", file=sys.stderr)
            return 2

    repo_root = detect_repo_root(args.repo_root)

    url = plc_workspace.strip_url_credentials(args.url) if args.url else None
    if url and not URL_RE.match(url):
        print(f"ERROR: --url must start with http:// or https://, got {args.url!r}", file=sys.stderr)
        return 2
    if url and url != args.url:
        print("WARN: stripped credentials/token from --url", file=sys.stderr)

    path_value = validate_path(args.path, repo_root) if args.path else None
    external_id = validate_external_id(args.external_id) if args.external_id else None

    pointer_rule = POINTER_REQUIREMENTS[args.check]
    pointer_present = any([url, path_value, external_id])
    for rule in pointer_rule:
        if rule == "url" and not url:
            print(f"ERROR: {args.check} requires --url", file=sys.stderr); return 2
        if rule == "url_or_path" and not (url or path_value):
            print(f"ERROR: {args.check} requires --url or --path", file=sys.stderr); return 2
        if rule == "external_id_or_url" and not (external_id or url):
            print(f"ERROR: {args.check} requires --external-id or --url", file=sys.stderr); return 2
        if rule == "url_or_external_id" and not (url or external_id):
            print(f"ERROR: {args.check} requires --url or --external-id", file=sys.stderr); return 2
        if rule == "any_pointer" and not pointer_present:
            print(f"ERROR: {args.check} requires --url, --path, or --external-id", file=sys.stderr); return 2

    notes = args.notes
    if notes:
        if len(notes) > 280:
            print(f"ERROR: --notes exceeds 280 chars ({len(notes)})", file=sys.stderr)
            return 2
        if looks_like_secret(notes):
            print("ERROR: --notes contains a high-entropy token that looks like a secret. Strip it and re-run, or use --allow-orphan style override (none provided for notes; rewrite --notes).", file=sys.stderr)
            return 2

    brief = find_brief(repo_root, args.feature_brief)
    ref_unverified = False
    if brief is None:
        if not args.allow_orphan:
            print("ERROR: could not locate a feature brief automatically; pass --feature-brief or --allow-orphan", file=sys.stderr)
            return 2
        ref_unverified = True
    else:
        if not ref_in_brief(args.ref, brief):
            if not args.allow_orphan:
                print(f"ERROR: --ref {args.ref} not found in {brief}; pass --allow-orphan to write anyway", file=sys.stderr)
                return 2
            ref_unverified = True

    if args.tc_id and brief is not None:
        test_plan = Path(args.test_plan).resolve() if args.test_plan else None
        if test_plan is None:
            if brief.name == "brief.md":
                candidate = brief.parent / "test-plan.md"
            else:
                stem = brief.stem
                feature = stem[: -len("-brief")] if stem.endswith("-brief") else stem
                candidate = brief.parent / f"{feature}-test-plan.md"
            if candidate.is_file():
                test_plan = candidate
        if test_plan and test_plan.is_file():
            if not tc_in_test_plan(args.tc_id, args.ref, test_plan):
                print(f"WARN: --tc-id {args.tc_id} does not appear in {test_plan} with --ref {args.ref} in its Verifies field", file=sys.stderr)

    branch, commit, scm = current_branch_commit(repo_root)
    if not commit:
        print("ERROR: could not resolve Git HEAD or Perforce client; is this a Git or P4 workspace?", file=sys.stderr)
        return 2
    workspace_fields, workspace_error = workspace_record(args, repo_root, commit)
    if workspace_error:
        print(f"ERROR: {workspace_error}", file=sys.stderr)
        return 2

    default_tool = {
        "jenkins-build": "jenkins",
        "perf-report": "internal-perf-rig",
        "accuracy-report": "internal-accuracy-rig",
        "manual-signoff": "manual",
        "scanspect-run": "scanspect",
        "nspect-result": "nspect",
        "external-test": "external-test",
        "other": "other",
    }[args.check]
    tool = args.tool or default_tool

    record: dict = {
        "pillar": "VERIFICATION",
        "check": args.check,
        "tool": tool,
        "ref": args.ref,
        "branch": branch,
        "commit": commit,
        "timestamp": utc_now(),
        "producer": args.producer,
        "producer_id": args.producer_id or "agentic-plc",
        "producer_context": args.producer_context or f"{branch}@{commit}",
    }
    record.update(workspace_fields)
    record.update(scm)
    if args.status:
        record["status"] = args.status
    if args.decision:
        record["decision"] = args.decision
    if url:
        record["url"] = url
    if path_value:
        record["path"] = path_value
    if external_id:
        record["external_id"] = external_id
    if args.reviewer:
        record["reviewer"] = args.reviewer.strip()
    if notes:
        record["notes"] = notes
    if args.tc_id:
        record["tc_id"] = args.tc_id
    if args.ac_id:
        record["ac_id"] = args.ac_id
    if args.finding_id:
        record["finding_id"] = args.finding_id
    if args.worktree_digest:
        record["worktree_digest"] = args.worktree_digest
    if ref_unverified:
        record["ref_unverified"] = True

    log = evidence_log_path(repo_root, args.evidence_log, brief)
    append_evidence(log, record)

    print(f"appended {args.check}/{tool} ref={args.ref} -> {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
