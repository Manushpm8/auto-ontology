#!/usr/bin/env python3
"""Run local Agentic PLC security checks and append PLC evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[2] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

import plc_workspace


DEFAULT_ALWAYS_EXCLUDE_DIRS = (
    r"\.venv|venv|node_modules|coverage|\.tox|\.mypy_cache|\.pytest_cache|\.plc|\.plc-scan|plc-evidence\.jsonl"
)
DEFAULT_REVIEW_EXCLUDE_DIRS = r"dist|build|vendor|third_party|generated"
# `.env`, `.env.*`, and `.envrc` files carry shell-style key=value secrets
# rather than source-code constructs Semgrep rules target; SAST gets only false
# positives from them. They stay in scope for the secret scanner (Pulse). Set
# PLC_INCLUDE_ENV_FILES_IN_SAST=1 only when the user explicitly asks Semgrep
# to look at env-file syntax.
ENV_FILE_PATTERN = re.compile(r"(?:^|/)(?:\.env(?:\..*)?|\.envrc)$")
EMBEDDED_CREDS_RE = re.compile(r"^https?://[^/@]+:[^/@]+@")
TOKEN_PARAM_RE = re.compile(r"((?:token|key|secret|password|api[-_]?key)=)[^&\s]+", re.IGNORECASE)
# Issue #10 ask 1 (review-8 follow-up): a lockfile under any of these path
# prefixes belongs to a colocated standalone third-party app — nSpect /
# BlackDuck typically do not treat it as a parent-project dependency. Tag
# the context colocated_app=True regardless of whether the path was in the
# SAST review-exclude set; the colocated concept is about
# release-gate ownership, not about source-review noise.
COLOCATED_APP_PATH_PATTERN = re.compile(
    r"(?:^|/)(?:vendor|third_party|external|submodules)/"
)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 10
DEFAULT_SEMGREP_TIMEOUT_SECONDS = 600
SEMGREP_VERSION = "1.157.0"
PIP_AUDIT_VERSION = "2.9.0"
PIP_LICENSES_VERSION = "5.5.5"
PROJECT_LOCAL_SENTINEL = "project-local"
PYTHON_SCANNER_PACKAGES = (
    f"semgrep=={SEMGREP_VERSION}",
    f"pip-audit=={PIP_AUDIT_VERSION}",
    f"pip-licenses=={PIP_LICENSES_VERSION}",
)
_PYTHON_VENV_BOOTSTRAP_CACHE: dict[Path, tuple[bool, str | None]] = {}


@dataclass
class Scope:
    files: list[str]
    unfiltered: list[str]
    always_excluded: list[str]
    review_excluded: list[str]
    base_ref: str | None
    rescope_reason: str | None
    ignored_paths: list[str]


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    timeout_seconds: int | None = None


@dataclass
class ScanTree:
    root: Path
    temp: tempfile.TemporaryDirectory[str]
    copied_files: list[str]
    skipped_symlinks: list[str]
    missing_files: list[str]


@dataclass(frozen=True)
class DependencyContext:
    ecosystem: str
    manager: str
    directory: str
    manifest: str | None
    lockfile: str | None
    scanner: str
    check_command: tuple[str, ...] | None
    scan_command: tuple[str, ...]
    stale_reason: str | None
    version_too_old_reason: str | None = None
    tool_source: str | None = None
    scan_env: dict[str, str] | None = None
    unavailable_reason: str | None = None
    # True when the context came from a path the SAST review-exclusion drops
    # (vendor/, third_party/, etc.). nSpect/BlackDuck typically do not register
    # these as parent-project dependencies; the local advisory pass is the only
    # CVE signal they get. Surfaced on the per-context evidence record so PR
    # reviewers can see which findings are not covered by the release gate.
    colocated_app: bool = False


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = timeout_text(exc.stdout)
        stderr = timeout_text(exc.stderr)
        timeout_message = f"command timed out after {timeout}s: {' '.join(command)}"
        stderr = f"{stderr}\n{timeout_message}".strip()
        return CommandResult(124, stdout, stderr, timed_out=True, timeout_seconds=timeout)
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def git_output(repo_root: Path, *args: str) -> str | None:
    result = run_command(["git", *args], cwd=repo_root)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def p4_output(cwd: Path, *args: str) -> str | None:
    result = run_command(["p4", "-ztag", *args], cwd=cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


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


def parse_p4_ztag_records(output: str | None) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    if not output:
        return records
    for line in output.splitlines():
        if not line.startswith("... "):
            continue
        body = line[4:]
        key, _, value = body.partition(" ")
        if key == "depotFile" and current:
            records.append(current)
            current = {}
        if key:
            current[key] = value
    if current:
        records.append(current)
    return records


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


def p4_client_root(cwd: Path) -> Path | None:
    root = p4_info(cwd).get("clientRoot")
    if not root:
        root = p4_env_info(cwd).get("clientRoot")
        if not root:
            return None
    return Path(root).resolve()


def detect_repo_root(explicit: str | None) -> Path:
    if explicit:
        requested = Path(explicit).resolve()
        if requested.is_dir():
            toplevel = run_command(
                ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
                cwd=requested,
            )
            if toplevel.returncode == 0 and toplevel.stdout.strip():
                resolved = Path(toplevel.stdout.strip()).resolve()
                if resolved != requested:
                    print(
                        f"NOTE: --repo-root {requested} is inside git toplevel {resolved}; using toplevel",
                        file=sys.stderr,
                    )
                return resolved
            p4_root = p4_client_root(requested)
            if p4_root and p4_root != requested:
                print(
                    f"NOTE: --repo-root {requested} is inside Perforce client root {p4_root}; using client root",
                    file=sys.stderr,
                )
                return p4_root
        return requested
    result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=Path.cwd())
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    p4_root = p4_client_root(Path.cwd())
    if p4_root:
        return p4_root
    return Path.cwd().resolve()


def is_git_repo(repo_root: Path) -> bool:
    return git_output(repo_root, "rev-parse", "--is-inside-work-tree") == "true"


def is_p4_workspace(repo_root: Path) -> bool:
    return bool(p4_workspace_metadata(repo_root).get("p4_client"))


def p4_client_name(repo_root: Path) -> str:
    return str(p4_workspace_metadata(repo_root).get("p4_client") or "unknown")


def current_branch(repo_root: Path) -> str:
    branch = git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = current_commit(repo_root)
    if not branch:
        if is_p4_workspace(repo_root):
            return f"p4/{p4_client_name(repo_root)}"
        return "unknown"
    if branch == "HEAD":
        return f"detached/{commit}"
    return branch


def current_commit(repo_root: Path) -> str:
    commit = git_output(repo_root, "rev-parse", "HEAD")
    if commit:
        return commit
    if is_p4_workspace(repo_root):
        return f"p4/{p4_client_name(repo_root)}"
    return "unknown"


def scm_metadata(repo_root: Path) -> dict:
    if is_git_repo(repo_root):
        return {"scm": "git"}
    info = p4_workspace_metadata(repo_root)
    if info:
        return info
    return {"scm": "none"}


def discover_workspace_manifest(repo_root: Path) -> tuple[Path | None, dict]:
    context = plc_workspace.discover_workspace(repo_root)
    return context.get("workspace_root"), context.get("manifest", {})


def matching_workspace_repo(workspace_root: Path | None, manifest: dict, repo_root: Path) -> dict:
    if workspace_root is None:
        return {}
    context = plc_workspace.discover_workspace(workspace_root)
    return plc_workspace.matching_repo(context, repo_root)


def env_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def workspace_attribution(repo_root: Path, commit: str) -> dict:
    context = plc_workspace.discover_workspace(repo_root)
    workspace_root = context.get("workspace_root")
    manifest = context.get("manifest", {})
    repo_entry = plc_workspace.matching_repo(context, repo_root)

    workspace_id = env_value("AGENTIC_PLC_WORKSPACE_ID")
    if not workspace_id and isinstance(manifest.get("workspace_id"), str):
        workspace_id = manifest["workspace_id"].strip() or None

    workspace_root_value = plc_workspace.stable_workspace_root(
        env_value("AGENTIC_PLC_WORKSPACE_ROOT"),
        workspace_root is not None and not workspace_id,
    )

    repo_id = env_value("AGENTIC_PLC_REPO_ID")
    if not repo_id and isinstance(repo_entry.get("repo_id"), str):
        repo_id = repo_entry["repo_id"].strip() or None

    repo_url = env_value("AGENTIC_PLC_REPO_URL")
    if not repo_url and isinstance(repo_entry.get("url"), str):
        repo_url = repo_entry["url"].strip() or None
    if not repo_url:
        repo_url = plc_workspace.git_remote_url(repo_root)
    if repo_url:
        repo_url = plc_workspace.strip_url_credentials(repo_url)

    repo_revision = env_value("AGENTIC_PLC_REPO_REVISION") or commit

    workspace_mode = any(
        [
            workspace_root is not None,
            env_value("AGENTIC_PLC_WORKSPACE_ID"),
            env_value("AGENTIC_PLC_WORKSPACE_ROOT"),
            env_value("AGENTIC_PLC_REPO_ID"),
            env_value("AGENTIC_PLC_REPO_URL"),
            env_value("AGENTIC_PLC_REPO_REVISION"),
        ]
    )
    if not workspace_mode:
        return {}

    record: dict = {}
    context_errors = context.get("errors") or []
    if context_errors and workspace_root is not None:
        record["workspace_validation_errors"] = [str(e) for e in context_errors]
    if workspace_id:
        record["workspace_id"] = workspace_id
    if workspace_root_value:
        record["workspace_root"] = workspace_root_value
    if repo_id:
        record["repo_id"] = repo_id
    if repo_url:
        record["repo_url"] = repo_url
    if repo_revision and repo_revision != "unknown":
        record["repo_revision"] = repo_revision
    return record


def resolve_base_ref(repo_root: Path) -> str | None:
    env_base = os.environ.get("BASE_REF", "").strip()
    if env_base:
        return env_base

    ci_mr_target = os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "").strip()
    if ci_mr_target:
        candidate_remote = f"origin/{ci_mr_target}"
        if (
            run_command(
                ["git", "rev-parse", "--verify", "--quiet", candidate_remote],
                cwd=repo_root,
            ).returncode
            == 0
        ):
            print(
                f"NOTE: using CI_MERGE_REQUEST_TARGET_BRANCH_NAME -> {candidate_remote} as base ref",
                file=sys.stderr,
            )
            return candidate_remote
        if (
            run_command(
                ["git", "rev-parse", "--verify", "--quiet", ci_mr_target],
                cwd=repo_root,
            ).returncode
            == 0
        ):
            print(
                f"NOTE: using CI_MERGE_REQUEST_TARGET_BRANCH_NAME -> {ci_mr_target} as base ref",
                file=sys.stderr,
            )
            return ci_mr_target
        print(
            f"NOTE: CI_MERGE_REQUEST_TARGET_BRANCH_NAME '{ci_mr_target}' not resolvable; falling back",
            file=sys.stderr,
        )

    upstream = git_output(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        return upstream

    origin_head = git_output(repo_root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if origin_head:
        return origin_head.removeprefix("refs/remotes/")

    for candidate in ("origin/main", "origin/master", "main", "master"):
        if run_command(["git", "rev-parse", "--verify", "--quiet", candidate], cwd=repo_root).returncode == 0:
            return candidate
    return None


def git_diff_names(repo_root: Path, *args: str) -> list[str]:
    output = git_output(repo_root, "diff", "--name-only", "--diff-filter=ACMR", *args)
    if not output:
        return []
    return [line for line in output.splitlines() if line]


def git_untracked_names(repo_root: Path) -> list[str]:
    output = git_output(repo_root, "ls-files", "--others", "--exclude-standard")
    if not output:
        return []
    return [line for line in output.splitlines() if line]


def p4_opened_records(repo_root: Path) -> list[dict[str, str]]:
    return parse_p4_ztag_records(p4_output(repo_root, "opened"))


def p4_opened_names(repo_root: Path) -> list[str]:
    paths: list[str] = []
    for record in p4_opened_records(repo_root):
        action = record.get("action", "").lower()
        if action in {"delete", "move/delete", "purge"}:
            continue
        client_file = record.get("clientFile")
        if not client_file:
            continue
        path = Path(client_file).resolve()
        try:
            paths.append(path.relative_to(repo_root.resolve()).as_posix())
        except ValueError:
            continue
    return sorted(set(paths))


def normalize_explicit_paths(repo_root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    ignored: list[str] = []
    for raw in paths:
        path = Path(raw)
        absolute = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
        try:
            normalized.append(absolute.relative_to(repo_root).as_posix())
        except ValueError:
            print(f"WARN: ignoring --path {raw}: not under repo root {repo_root}", file=sys.stderr)
            ignored.append(raw)
    return sorted(set(normalized)), ignored


@lru_cache(maxsize=16)
def compile_dir_pattern(value: str) -> re.Pattern[str]:
    return re.compile(rf"(^|/)({value})(/|$)")


def safe_compile_dir_pattern(env_name: str, default: str) -> re.Pattern[str]:
    value = os.environ.get(env_name, default)
    try:
        return compile_dir_pattern(value)
    except re.error as exc:
        print(
            f"WARN: invalid regex in {env_name}={value!r}: {exc}; falling back to default",
            file=sys.stderr,
        )
        return compile_dir_pattern(default)


def matches_dir(pattern: re.Pattern[str], relative_path: str) -> bool:
    return pattern.search(relative_path) is not None


def build_scope(repo_root: Path, explicit_paths: list[str]) -> Scope:
    if explicit_paths:
        unfiltered, ignored_paths = normalize_explicit_paths(repo_root, explicit_paths)
        return Scope(
            files=unfiltered,
            unfiltered=unfiltered,
            always_excluded=[],
            review_excluded=[],
            base_ref=None,
            rescope_reason="using user-provided file list",
            ignored_paths=ignored_paths,
        )
    elif is_git_repo(repo_root):
        base_ref = resolve_base_ref(repo_root)
        rescope_reason = None
        changed: set[str] = set()
        merge_base_resolved = False
        base_diff_empty = False
        if base_ref:
            merge_base = git_output(repo_root, "merge-base", "HEAD", base_ref)
            if merge_base:
                merge_base_resolved = True
                base_diff = git_diff_names(repo_root, f"{merge_base}...HEAD")
                if not base_diff:
                    base_diff_empty = True
                changed.update(base_diff)
            else:
                rescope_reason = f"base ref '{base_ref}' not reachable; using local worktree changes only"
        else:
            rescope_reason = "no upstream/default base ref found; using local worktree changes only"
        cached_changes = git_diff_names(repo_root, "--cached")
        worktree_changes = git_diff_names(repo_root)
        untracked = git_untracked_names(repo_root)
        changed.update(cached_changes)
        changed.update(worktree_changes)
        changed.update(untracked)
        unfiltered = sorted(changed)
        if (
            merge_base_resolved
            and base_diff_empty
            and not cached_changes
            and not worktree_changes
            and not untracked
            and rescope_reason is None
        ):
            rescope_reason = f"no changes vs base ref '{base_ref}'; nothing to scan"
    elif is_p4_workspace(repo_root):
        base_ref = "p4-opened"
        opened_output = p4_output(repo_root, "opened")
        records = parse_p4_ztag_records(opened_output)
        unfiltered = []
        for record in records:
            action = record.get("action", "").lower()
            if action in {"delete", "move/delete", "purge"}:
                continue
            client_file = record.get("clientFile")
            if not client_file:
                continue
            path = Path(client_file).resolve()
            try:
                unfiltered.append(path.relative_to(repo_root.resolve()).as_posix())
            except ValueError:
                continue
        unfiltered = sorted(set(unfiltered))
        if opened_output is None and scm_metadata(repo_root).get("p4_metadata_source") == "env":
            rescope_reason = "Perforce opened files unavailable; cannot query opened files"
        elif not records:
            rescope_reason = "no files opened in Perforce client; nothing to scan"
        elif not unfiltered:
            rescope_reason = "Perforce opened files are deleted or outside repo root; nothing to scan"
        else:
            rescope_reason = "using Perforce opened files"
    else:
        base_ref = None
        rescope_reason = "not a git or Perforce repository; using user-provided file list only"
        unfiltered = []

    always_pattern = safe_compile_dir_pattern("PLC_ALWAYS_EXCLUDE_DIRS", DEFAULT_ALWAYS_EXCLUDE_DIRS)
    review_pattern = safe_compile_dir_pattern("PLC_REVIEW_EXCLUDE_DIRS", DEFAULT_REVIEW_EXCLUDE_DIRS)
    always_excluded = [path for path in unfiltered if matches_dir(always_pattern, path)]
    candidates = [path for path in unfiltered if not matches_dir(always_pattern, path)]
    review_excluded = [path for path in candidates if matches_dir(review_pattern, path)]
    include_review = env_bool("PLC_INCLUDE_GENERATED_VENDOR", False)
    files = candidates if include_review else [path for path in candidates if not matches_dir(review_pattern, path)]
    return Scope(files, unfiltered, always_excluded, review_excluded, base_ref, rescope_reason, ignored_paths=[])


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def append_evidence(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    plc_workspace.record_evidence_phase_outcome(path, record)


def atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` via tempfile + Path.replace().

    Issue #10 (review-10 finding 6): `.plc/security/` is intentionally
    reviewable, so an interrupted write must not leave a truncated
    artifact replacing a previous good one. Write to a sibling tempfile
    in the same directory (so the rename is atomic on the same
    filesystem), then os-level replace into place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        tmp_path.replace(path)
    except Exception:
        # Best-effort tempfile cleanup if the write/replace fails.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def repo_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def status_path(line: str) -> str:
    if len(line) < 4:
        return ""
    path = line[3:]
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path


def relative_path_or_none(repo_root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None


def ignored_worktree_artifact(path: str, evidence_path: Path, repo_root: Path) -> bool:
    normalized = path.replace("\\", "/")
    ignored = {".plc", ".plc-scan", "plc-evidence.jsonl"}
    if normalized in ignored or normalized.startswith(".plc/") or normalized.startswith(".plc-scan/"):
        return True
    evidence_relative = relative_path_or_none(repo_root, evidence_path)
    return evidence_relative is not None and normalized == evidence_relative


def relevant_status_lines(repo_root: Path, evidence_path: Path) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    if is_git_repo(repo_root):
        status = run_command(
            ["git", "-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_root,
        )
        if status.returncode != 0:
            return []
        for line in status.stdout.splitlines():
            path = status_path(line)
            if path and not ignored_worktree_artifact(path, evidence_path, repo_root):
                lines.append((line, path))
        return lines
    if is_p4_workspace(repo_root):
        for record in p4_opened_records(repo_root):
            action = record.get("action", "")
            client_file = record.get("clientFile")
            if not client_file:
                continue
            path = relative_path_or_none(repo_root, Path(client_file))
            if path and not ignored_worktree_artifact(path, evidence_path, repo_root):
                change = record.get("change", "default")
                lines.append((f"p4 {action} {change} {path}", path))
    return lines


def worktree_digest(repo_root: Path, evidence_path: Path) -> str | None:
    if not is_git_repo(repo_root) and not is_p4_workspace(repo_root):
        return None
    digest = hashlib.sha256()
    for line, path in relevant_status_lines(repo_root, evidence_path):
        digest.update(line.encode("utf-8", errors="replace"))
        source = repo_root / path
        try:
            if source.is_symlink():
                digest.update(os.readlink(source).encode("utf-8", errors="replace"))
            elif source.is_file():
                digest.update(source.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return digest.hexdigest()


def base_record(repo_root: Path, check: str, tool: str, evidence_path: Path) -> dict:
    commit = current_commit(repo_root)
    record = {
        "pillar": "CODING",
        "check": check,
        "tool": tool,
        "branch": current_branch(repo_root),
        "commit": commit,
        "timestamp": utc_now(),
    }
    record.update(workspace_attribution(repo_root, commit))
    record.update(scm_metadata(repo_root))
    digest = worktree_digest(repo_root, evidence_path)
    if digest is not None:
        record["worktree_digest"] = digest
    return record


def make_scan_tree(repo_root: Path, files: list[str]) -> ScanTree:
    parent = Path(os.environ.get("PLC_SCAN_TMP_PARENT", tempfile.gettempdir())).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.TemporaryDirectory(prefix="plc-semgrep.", dir=parent)
    scan_root = Path(temp.name)
    copied_files: list[str] = []
    skipped_symlinks: list[str] = []
    missing_files: list[str] = []
    for relative in files:
        source = repo_root / relative
        if source.is_symlink():
            resolved = source.resolve()
            try:
                resolved.relative_to(repo_root)
            except ValueError:
                skipped_symlinks.append(relative)
                print(f"WARN: skipping symlink outside repo in scan scope: {relative}", file=sys.stderr)
                continue
            if not resolved.is_file():
                skipped_symlinks.append(relative)
                print(f"WARN: skipping broken/non-file symlink in scan scope: {relative}", file=sys.stderr)
                continue
            target = scan_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, target)
            copied_files.append(relative)
            continue
        if not source.is_file():
            missing_files.append(relative)
            print(f"WARN: skipping missing/non-file scan path: {relative}", file=sys.stderr)
            continue
        target = scan_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied_files.append(relative)
    return ScanTree(scan_root, temp, copied_files, skipped_symlinks, missing_files)


def extract_offline_rules(archive: Path, temp_root: Path) -> Path | None:
    if not archive.is_file():
        return None
    target = temp_root / "offline-rules"
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with tarfile.open(archive, "r:gz") as rules_archive:
        for member in rules_archive.getmembers():
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"unsafe Semgrep rules archive member type: {member.name}")
            destination = (target / member.name).resolve()
            if target_root not in (destination, *destination.parents):
                raise RuntimeError(f"unsafe Semgrep rules archive member: {member.name}")
            # Python 3.11 does not support TarFile.extract(filter=...); these
            # member-type and destination checks are the security boundary.
            rules_archive.extract(member, target)
    rules_dir = target / "semgrep-rules"
    return rules_dir if rules_dir.is_dir() else target


def parse_semgrep_json(text: str) -> dict:
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"results": [], "errors": [{"message": "semgrep returned invalid JSON"}]}


def sanitize_semgrep_data(data: dict) -> dict:
    """Project Semgrep JSON to fields safe to persist on disk.

    Drops `extra.lines`, `extra.metavars`, and `extra.message` because community
    rule messages can interpolate matched metavariables and `lines`/`metavars`
    carry source excerpts — a rule firing on a hardcoded credential can
    therefore re-embed the credential into the artifact. The on-disk projection
    keeps only `path`, `start_line`,
    `end_line`, `check_id`, and `severity`; `file:line check_id` is enough for
    actionability in the PR summary.
    """
    safe_results = []
    for finding in data.get("results", []) or []:
        if not isinstance(finding, dict):
            continue
        start = finding.get("start") if isinstance(finding.get("start"), dict) else {}
        end = finding.get("end") if isinstance(finding.get("end"), dict) else {}
        extra = finding.get("extra") if isinstance(finding.get("extra"), dict) else {}
        safe_results.append(
            {
                "path": finding.get("path"),
                "start_line": start.get("line"),
                "end_line": end.get("line"),
                "check_id": finding.get("check_id"),
                "severity": extra.get("severity"),
            }
        )
    return {"version": data.get("version"), "results": safe_results}


def run_semgrep(
    *,
    semgrep_command: list[str],
    semgrep_env: dict[str, str] | None,
    config: str,
    scan_root: Path,
    timeout_seconds: int,
    severity_threshold: str | None = "ERROR",
) -> tuple[CommandResult, dict]:
    """Run Semgrep and parse its JSON output in memory only.

    Semgrep emits raw JSON (which may carry `extra.lines`, `extra.metavars`,
    and rule-interpolated `extra.message` content) to stdout. We do NOT pass
    `--json-output` because that would write the raw stream to disk — even a
    short-lived tempfile outside the repo would put raw secret material on
    disk briefly. Instead we read stdout into a Python string, parse it, and
    return the in-memory data. The caller writes only the sanitized
    projection to the artifact path.

    `severity_threshold` selects the lowest severity to RUN — `None` means
    no `--severity` flag and Semgrep runs rules at all severities. Issue
    #10 (review-10 finding 3): the bundled Rust ruleset only declares
    INFO/WARNING severities, so `--severity=ERROR` filters all Rust rules
    out. When `.rs` files are in scope the caller passes `None` so Rust
    findings actually surface.
    """
    command = [
        *semgrep_command,
        "scan",
        "--json",
        "--config",
        config,
    ]
    if severity_threshold is not None:
        command.extend(["--severity", severity_threshold])
    command.extend([
        "--metrics",
        "off",
        "--disable-version-check",
        ".",
    ])
    result = run_command(command, cwd=scan_root, env=semgrep_env, timeout=timeout_seconds)
    raw_data = parse_semgrep_json(result.stdout)
    return result, raw_data


RUST_SOURCE_EXTENSIONS = (".rs",)


def scope_has_rust_sources(scope_files: list[str]) -> bool:
    """True if any reviewable file is Rust source.

    Used to relax the SAST severity threshold so the bundled Rust rules
    (which are INFO/WARNING only) actually surface findings.
    """
    return any(p.lower().endswith(RUST_SOURCE_EXTENSIONS) for p in scope_files)


def write_sast_stub(
    artifact_path: Path,
    *,
    result: str,
    reason: str | None = None,
    extra: dict | None = None,
) -> None:
    """Write a JSON skip/error/n-a stub to the SAST artifact path."""
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    stub: dict = {
        "check": "sast",
        "tool": "semgrep",
        "result": result,
        "timestamp": utc_now(),
    }
    if reason is not None:
        stub["reason"] = reason
    if extra:
        stub.update(extra)
    atomic_write_text(artifact_path, json.dumps(stub, indent=2, sort_keys=True) + "\n")


def write_sast_findings(artifact_path: Path, raw_data: dict) -> None:
    """Write the sanitized Semgrep projection to the artifact path."""
    safe_data = sanitize_semgrep_data(raw_data)
    atomic_write_text(
        artifact_path, json.dumps(safe_data, indent=2, sort_keys=True) + "\n"
    )


def networkish_failure(result: CommandResult, extra_text: str = "") -> bool:
    text = f"{result.stdout}\n{result.stderr}\n{extra_text}".lower()
    needles = (
        "connection",
        "timeout",
        "timed out",
        "dns",
        "network",
        "unreachable",
        "certificate",
        "proxy",
        "resolve host",
        "could not resolve",
        "name or service",
        "no route",
        "temporary failure in name resolution",
        "refused",
        "connection refused",
        "ssl_error_syscall",
    )
    return any(needle in text for needle in needles)


def command_name(command: tuple[str, ...] | list[str]) -> str:
    return Path(command[0]).name if command else "unknown"


def plc_tools_dir(repo_root: Path) -> Path:
    return repo_root / ".plc" / "tools"


def ensure_plc_tool_dirs(repo_root: Path) -> Path:
    tools = plc_tools_dir(repo_root)
    for child in ("uv-cache", "bin", "cache"):
        (tools / child).mkdir(parents=True, exist_ok=True)
    return tools


def uv_cache_env(repo_root: Path) -> dict[str, str]:
    cache_dir = ensure_plc_tool_dirs(repo_root) / "uv-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(cache_dir)
    return env


def python_venv_executable(repo_root: Path, executable: str) -> list[str] | None:
    tools = plc_tools_dir(repo_root)
    for candidate in (
        tools / "python-venv" / "bin" / executable,
        tools / "python-venv" / "Scripts" / f"{executable}.exe",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    return None


def python_venv_python(repo_root: Path) -> Path | None:
    tools = plc_tools_dir(repo_root)
    for candidate in (
        tools / "python-venv" / "bin" / "python",
        tools / "python-venv" / "Scripts" / "python.exe",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def bootstrap_python_venv(repo_root: Path) -> tuple[bool, str | None]:
    repo_root = repo_root.resolve()
    cached = _PYTHON_VENV_BOOTSTRAP_CACHE.get(repo_root)
    if cached is not None:
        return cached

    tools = ensure_plc_tool_dirs(repo_root)
    venv_dir = tools / "python-venv"
    create = run_command(
        [sys.executable, "-m", "venv", str(venv_dir)],
        cwd=repo_root,
        timeout=DEFAULT_SEMGREP_TIMEOUT_SECONDS,
    )
    if create.timed_out:
        result = (False, f"python venv bootstrap timed out after {create.timeout_seconds}s")
        _PYTHON_VENV_BOOTSTRAP_CACHE[repo_root] = result
        return result
    if create.returncode != 0:
        reason = "python venv bootstrap failed"
        detail = (create.stderr or create.stdout).strip()
        if detail:
            reason = f"{reason}: {detail.splitlines()[-1]}"
        result = (False, reason)
        _PYTHON_VENV_BOOTSTRAP_CACHE[repo_root] = result
        return result

    venv_python = python_venv_python(repo_root)
    if venv_python is None:
        result = (False, "python venv bootstrap failed: venv python unavailable")
        _PYTHON_VENV_BOOTSTRAP_CACHE[repo_root] = result
        return result

    install = run_command(
        [str(venv_python), "-m", "pip", "install", *PYTHON_SCANNER_PACKAGES],
        cwd=repo_root,
        timeout=DEFAULT_SEMGREP_TIMEOUT_SECONDS,
    )
    if install.timed_out:
        result = (False, f"python scanner install timed out after {install.timeout_seconds}s")
        _PYTHON_VENV_BOOTSTRAP_CACHE[repo_root] = result
        return result
    if install.returncode != 0:
        reason = "python scanner install failed"
        detail = (install.stderr or install.stdout).strip()
        if detail:
            reason = f"{reason}: {detail.splitlines()[-1]}"
        result = (False, reason)
        _PYTHON_VENV_BOOTSTRAP_CACHE[repo_root] = result
        return result

    result = (True, None)
    _PYTHON_VENV_BOOTSTRAP_CACHE[repo_root] = result
    return result


def project_local_python_tool(
    repo_root: Path,
    executable: str,
    package: str,
    version: str,
) -> tuple[list[str] | None, str, dict[str, str] | None, str | None]:
    if shutil.which("uvx"):
        return (
            ["uvx", "--from", f"{package}=={version}", executable],
            "project-local-uvx",
            uv_cache_env(repo_root),
            None,
        )

    venv_command = python_venv_executable(repo_root, executable)
    if venv_command is not None:
        return venv_command, "project-local-venv", None, None

    bootstrap_ok, bootstrap_reason = bootstrap_python_venv(repo_root)
    if bootstrap_ok:
        venv_command = python_venv_executable(repo_root, executable)
        if venv_command is not None:
            return venv_command, "project-local-venv", None, None

    if shutil.which(executable):
        return [executable], "path-fallback", None, None

    reason = f"project-local {executable} unavailable"
    if bootstrap_reason:
        reason = f"{reason}; {bootstrap_reason}"
    return None, "project-local", None, reason


def project_local_osv_scanner(repo_root: Path) -> tuple[list[str] | None, str, str | None]:
    tools = ensure_plc_tool_dirs(repo_root)
    for candidate in (
        tools / "bin" / "osv-scanner",
        tools / "bin" / "osv-scanner.exe",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)], "project-local-bin", None

    if shutil.which("osv-scanner"):
        return ["osv-scanner"], "path-fallback", None

    return None, "project-local-bin", "osv-scanner unavailable under .plc/tools/bin or PATH"


def project_local_semgrep(
    repo_root: Path,
    requested: str,
) -> tuple[list[str] | None, str, dict[str, str] | None, str]:
    if requested != PROJECT_LOCAL_SENTINEL:
        if shutil.which(requested):
            return [requested], "explicit", None, "semgrep"
        return None, "explicit", None, "semgrep not installed"

    ensure_plc_tool_dirs(repo_root)
    if shutil.which("uvx"):
        return (
            ["uvx", "--from", f"semgrep=={SEMGREP_VERSION}", "semgrep"],
            "project-local-uvx",
            uv_cache_env(repo_root),
            "semgrep",
        )
    venv_command = python_venv_executable(repo_root, "semgrep")
    if venv_command is not None:
        return venv_command, "project-local-venv", None, "semgrep"
    bootstrap_ok, bootstrap_reason = bootstrap_python_venv(repo_root)
    if bootstrap_ok:
        venv_command = python_venv_executable(repo_root, "semgrep")
        if venv_command is not None:
            return venv_command, "project-local-venv", None, "semgrep"
    if shutil.which("semgrep"):
        return ["semgrep"], "path-fallback", None, "semgrep"
    reason = "project-local Semgrep unavailable"
    if bootstrap_reason:
        reason = f"{reason}; {bootstrap_reason}"
    return None, "project-local", None, reason


def command_available(command: tuple[str, ...] | list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if os.sep in executable:
        return Path(executable).is_file() and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None


def unsupported_lock_check(result: CommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    needles = (
        "unknown option",
        "unrecognized option",
        "unknown command",
        "unrecognized command",
        "unexpected argument",
        "no such option",
        "invalid option",
        "not supported",
    )
    return any(needle in text for needle in needles)


def is_requirements_file(name: str) -> bool:
    return re.fullmatch(r"requirements(?:[-_.A-Za-z0-9]*)?\.txt", name) is not None


def context_file(directory: Path, name: str) -> str | None:
    path = directory / name
    return name if path.is_file() else None


def scanner_command(command: list[str] | None, *args: str) -> tuple[str, ...]:
    if command is None:
        return ()
    return tuple([*command, *args])


def artifact_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "unknown"


def dependency_artifact_path(artifact_dir: Path, context: DependencyContext) -> Path:
    directory_slug = "root" if context.directory == "." else artifact_slug(context.directory)
    dependency_file = context.lockfile or context.manifest or "unknown"
    return artifact_dir / f"dependency-{directory_slug}-{context.manager}-{artifact_slug(dependency_file)}.json"


def dependency_contexts(
    repo_root: Path,
    scope_files: list[str],
    colocated_files: list[str] | None = None,
) -> list[DependencyContext]:
    contexts: dict[tuple[str, str, str, str], DependencyContext] = {}

    def add(context: DependencyContext) -> None:
        key = (
            context.ecosystem,
            context.manager,
            context.directory,
            context.lockfile or context.manifest or "",
        )
        # If the same lockfile shows up in both the reviewable scope and the
        # review-excluded set (unlikely but possible), prefer the reviewable
        # entry — colocated_app=False is the more conservative classification.
        if key in contexts and contexts[key].colocated_app and not context.colocated_app:
            contexts[key] = context
        elif key not in contexts:
            contexts[key] = context

    file_lists: list[tuple[list[str], bool]] = [(scope_files, False)]
    if colocated_files:
        file_lists.append((colocated_files, True))
    for files, colocated in file_lists:
        for relative in files:
            _add_context_for_file(repo_root, relative, colocated, add)

    return sorted(
        contexts.values(),
        key=lambda item: (item.directory, item.ecosystem, item.manager, item.lockfile or ""),
    )


def _add_context_for_file(repo_root, relative, colocated, add):
    for relative_value in (relative,):
        # Single-iteration wrapper so the per-file dispatch body below keeps
        # its original branch structure unchanged; only the DependencyContext
        # kwargs now carry colocated_app.
        relative = relative_value
        # Upgrade `colocated` to True if the path itself sits under a
        # documented colocated-app prefix (vendor/, third_party/, external/,
        # submodules/), independent of whether the SAST review-exclude
        # regex happens to include those names today. This is the
        # transparency mechanism for "may not be covered by the parent
        # release gate" — review-exclusion is a separate concern.
        if not colocated and COLOCATED_APP_PATH_PATTERN.search(f"/{relative}"):
            colocated = True
        path = repo_root / relative
        directory = path.parent
        directory_relative = directory.relative_to(repo_root).as_posix() if directory != repo_root else "."
        name = path.name

        if name == "uv.lock" or (name == "pyproject.toml" and (directory / "uv.lock").is_file()):
            osv_command, tool_source, unavailable_reason = project_local_osv_scanner(repo_root)
            add(
                DependencyContext(
                    ecosystem="python",
                    manager="uv",
                    directory=directory_relative,
                    manifest=context_file(directory, "pyproject.toml"),
                    lockfile="uv.lock",
                    scanner="osv-scanner",
                    check_command=("uv", "lock", "--check"),
                    scan_command=scanner_command(osv_command, "--lockfile", "uv.lock", "--format", "json"),
                    stale_reason="uv.lock inconsistent with pyproject.toml; run uv lock",
                    version_too_old_reason="uv-version-too-old for lockfile consistency check",
                    tool_source=tool_source,
                    unavailable_reason=unavailable_reason,
                    colocated_app=colocated,
                )
            )
        elif name == "poetry.lock" or (name == "pyproject.toml" and (directory / "poetry.lock").is_file()):
            osv_command, tool_source, unavailable_reason = project_local_osv_scanner(repo_root)
            add(
                DependencyContext(
                    ecosystem="python",
                    manager="poetry",
                    directory=directory_relative,
                    manifest=context_file(directory, "pyproject.toml"),
                    lockfile="poetry.lock",
                    scanner="osv-scanner",
                    check_command=("poetry", "check", "--lock"),
                    scan_command=scanner_command(osv_command, "--lockfile", "poetry.lock", "--format", "json"),
                    stale_reason="poetry.lock inconsistent with pyproject.toml; run poetry lock",
                    version_too_old_reason="poetry-version-too-old for lockfile consistency check",
                    tool_source=tool_source,
                    unavailable_reason=unavailable_reason,
                    colocated_app=colocated,
                )
            )
        elif is_requirements_file(name):
            pip_audit_command, tool_source, scan_env, unavailable_reason = project_local_python_tool(
                repo_root,
                "pip-audit",
                "pip-audit",
                PIP_AUDIT_VERSION,
            )
            add(
                DependencyContext(
                    ecosystem="python",
                    manager="requirements",
                    directory=directory_relative,
                    manifest=name,
                    lockfile=None,
                    scanner="pip-audit",
                    check_command=None,
                    scan_command=scanner_command(pip_audit_command, "-r", name, "-f", "json"),
                    stale_reason=None,
                    tool_source=tool_source,
                    scan_env=scan_env,
                    unavailable_reason=unavailable_reason,
                    colocated_app=colocated,
                )
            )
        elif name in ("package-lock.json", "package.json") and (directory / "package-lock.json").is_file():
            osv_command, tool_source, unavailable_reason = project_local_osv_scanner(repo_root)
            add(
                DependencyContext(
                    ecosystem="node",
                    manager="npm",
                    directory=directory_relative,
                    manifest=context_file(directory, "package.json"),
                    lockfile="package-lock.json",
                    scanner="osv-scanner",
                    check_command=None,
                    scan_command=scanner_command(osv_command, "--lockfile", "package-lock.json", "--format", "json"),
                    stale_reason=None,
                    tool_source=tool_source,
                    unavailable_reason=unavailable_reason,
                    colocated_app=colocated,
                )
            )
        elif name in ("yarn.lock", "package.json") and (directory / "yarn.lock").is_file():
            osv_command, tool_source, unavailable_reason = project_local_osv_scanner(repo_root)
            add(
                DependencyContext(
                    ecosystem="node",
                    manager="yarn",
                    directory=directory_relative,
                    manifest=context_file(directory, "package.json"),
                    lockfile="yarn.lock",
                    scanner="osv-scanner",
                    check_command=None,
                    scan_command=scanner_command(osv_command, "--lockfile", "yarn.lock", "--format", "json"),
                    stale_reason=None,
                    tool_source=tool_source,
                    unavailable_reason=unavailable_reason,
                    colocated_app=colocated,
                )
            )
        elif name in ("pnpm-lock.yaml", "package.json") and (directory / "pnpm-lock.yaml").is_file():
            osv_command, tool_source, unavailable_reason = project_local_osv_scanner(repo_root)
            add(
                DependencyContext(
                    ecosystem="node",
                    manager="pnpm",
                    directory=directory_relative,
                    manifest=context_file(directory, "package.json"),
                    lockfile="pnpm-lock.yaml",
                    scanner="osv-scanner",
                    check_command=None,
                    scan_command=scanner_command(osv_command, "--lockfile", "pnpm-lock.yaml", "--format", "json"),
                    stale_reason=None,
                    tool_source=tool_source,
                    unavailable_reason=unavailable_reason,
                    colocated_app=colocated,
                )
            )


def dependency_context_record(context: DependencyContext) -> dict:
    data = {
        "ecosystem": context.ecosystem,
        "manager": context.manager,
        "directory": context.directory,
        "scanner": context.scanner,
    }
    if context.manifest:
        data["manifest"] = context.manifest
    if context.lockfile:
        data["lockfile"] = context.lockfile
    if context.tool_source:
        data["tool_source"] = context.tool_source
    if context.colocated_app:
        data["colocated_app"] = True
    return data


# File extensions that signal "this work unit changed source code in an
# ecosystem that normally has a manifest" — used by run_dependency_vuln to
# detect the no-lockfile / no-manifest case and turn it into a WARN row
# instead of silently recording the change as N/A. Issue #10 ask 2.
SOURCE_EXTENSIONS_WITH_DEPS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".rb",
    ".go",
    ".rs",
)


def dep_carrying_source_paths(scope_files: list[str]) -> list[str]:
    """Return scope files in a source language that normally has a manifest.

    Used by `run_dependency_vuln` to detect "source changed but no
    manifest/lockfile is in scope" — issue #10 ask 2's no-lockfile
    coverage gap. Markdown, configs, and the like do not count.
    """
    return [
        path for path in scope_files
        if path.lower().endswith(SOURCE_EXTENSIONS_WITH_DEPS)
    ]


def scope_has_dep_carrying_source(scope_files: list[str]) -> bool:
    """Backward-compatible boolean: any file in a dep-bearing ecosystem?"""
    return bool(dep_carrying_source_paths(scope_files))


def orphan_pyproject_paths(repo_root: Path, scope_files: list[str]) -> list[str]:
    """Return scope files that are pyproject.toml with no sibling lockfile.

    `_add_context_for_file` only creates a Python dependency context for
    `uv.lock`/`poetry.lock`/`requirements*.txt`. A `pyproject.toml` change
    in a project that has no resolver lock would fall through to N/A even
    though the project has declared dependencies. Issue #10 ask 2: this
    must produce a WARN that names the gap.
    """
    orphans: list[str] = []
    for relative in scope_files:
        if Path(relative).name != "pyproject.toml":
            continue
        directory = (repo_root / relative).parent
        if (directory / "uv.lock").is_file():
            continue
        if (directory / "poetry.lock").is_file():
            continue
        orphans.append(relative)
    return orphans


def is_colocated_path(relative: str) -> bool:
    """True if a repo-relative path sits under a colocated-app prefix."""
    return bool(COLOCATED_APP_PATH_PATTERN.search(f"/{relative}"))


def load_json_output(text: str) -> object | None:
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def add_finding_key(keys: set[str], ecosystem: str, package: object, version: object, advisory: object) -> None:
    package_text = str(package or "").strip()
    advisory_text = str(advisory or "").strip()
    if not package_text and not advisory_text:
        return
    version_text = str(version or "").strip()
    keys.add(f"{ecosystem}:{package_text}:{version_text}:{advisory_text}")


def dependency_finding_keys(data: object, ecosystem: str) -> set[str]:
    keys: set[str] = set()
    if not isinstance(data, dict):
        return keys

    for finding in data.get("findings", []) or []:
        if isinstance(finding, dict):
            add_finding_key(
                keys,
                ecosystem,
                finding.get("package") or finding.get("name"),
                finding.get("version"),
                finding.get("advisory") or finding.get("id") or finding.get("vulnerability_id"),
            )

    for dependency in data.get("dependencies", []) or []:
        if not isinstance(dependency, dict):
            continue
        package = dependency.get("name")
        version = dependency.get("version")
        for vuln in dependency.get("vulns", []) or []:
            if isinstance(vuln, dict):
                add_finding_key(keys, "python", package, version, vuln.get("id") or vuln.get("aliases"))

    vulnerabilities = data.get("vulnerabilities")
    if isinstance(vulnerabilities, dict):
        for package, vuln in vulnerabilities.items():
            if not isinstance(vuln, dict):
                continue
            via = vuln.get("via", [])
            if not isinstance(via, list):
                via = [via]
            for item in via:
                if isinstance(item, dict):
                    advisory = item.get("source") or item.get("id") or item.get("name") or item.get("url")
                else:
                    advisory = item
                add_finding_key(keys, "node", package, vuln.get("range") or vuln.get("version"), advisory)
    elif isinstance(vulnerabilities, list):
        for vuln in vulnerabilities:
            if isinstance(vuln, dict):
                add_finding_key(
                    keys,
                    ecosystem,
                    vuln.get("package") or vuln.get("name"),
                    vuln.get("version"),
                    vuln.get("id") or vuln.get("aliases"),
                )

    for result in data.get("results", []) or []:
        if not isinstance(result, dict):
            continue
        packages = result.get("packages", [])
        if isinstance(packages, dict):
            packages = [packages]
        for package_entry in packages or []:
            if not isinstance(package_entry, dict):
                continue
            package_data = package_entry.get("package") if isinstance(package_entry.get("package"), dict) else package_entry
            package_name = package_data.get("name") if isinstance(package_data, dict) else None
            package_version = package_data.get("version") if isinstance(package_data, dict) else None
            vulns = package_entry.get("vulnerabilities", [])
            if isinstance(vulns, dict):
                vulns = [vulns]
            for vuln in vulns or []:
                if isinstance(vuln, dict):
                    add_finding_key(keys, ecosystem, package_name, package_version, vuln.get("id") or vuln.get("aliases"))

    return keys


def context_cwd(repo_root: Path, context: DependencyContext) -> Path:
    return repo_root if context.directory == "." else repo_root / context.directory


def context_baseline_files(context: DependencyContext) -> list[tuple[str, bool]]:
    files = [(name, True) for name in (context.manifest, context.lockfile) if name]
    unique: dict[str, bool] = {}
    for name, required in files:
        unique[name] = unique.get(name, False) or required
    return sorted(unique.items())


def git_file_at_ref(repo_root: Path, ref: str, relative: str) -> str | None:
    result = run_command(["git", "show", f"{ref}:{relative}"], cwd=repo_root)
    if result.returncode != 0:
        return None
    return result.stdout


def baseline_dependency_keys(
    repo_root: Path,
    context: DependencyContext,
    base_ref: str | None,
    timeout_seconds: int,
) -> tuple[set[str] | None, str | None]:
    if not base_ref:
        return None, "baseline unavailable: no base ref"
    needed = context_baseline_files(context)
    if not needed:
        return None, "baseline unavailable: no manifest or lockfile"
    with tempfile.TemporaryDirectory(prefix="plc-dependency-baseline.") as temp_name:
        temp_root = Path(temp_name)
        for name, required in needed:
            repo_relative = name if context.directory == "." else f"{context.directory}/{name}"
            content = git_file_at_ref(repo_root, base_ref, repo_relative)
            if content is None:
                if required:
                    return None, f"baseline unavailable: {repo_relative} not present at {base_ref}"
                continue
            target = temp_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        scan_command = list(context.scan_command)
        if context.unavailable_reason and not command_available(scan_command):
            return None, f"baseline unavailable: {context.unavailable_reason}"
        if not command_available(scan_command):
            return None, f"baseline unavailable: {command_name(scan_command)} not installed"
        result = run_command(scan_command, cwd=temp_root, env=context.scan_env, timeout=timeout_seconds)
    data = load_json_output(result.stdout) or load_json_output(result.stderr)
    keys = dependency_finding_keys(data, context.ecosystem) if data is not None else set()
    if result.timed_out:
        return None, f"baseline unavailable: {context.scanner} timed out after {result.timeout_seconds}s"
    if data is None:
        if result.returncode != 0 and networkish_failure(result):
            return None, f"baseline unavailable: {context.scanner} network unreachable"
        return None, f"baseline unavailable: {context.scanner} returned malformed JSON"
    if result.returncode != 0 and not keys:
        if networkish_failure(result):
            return None, f"baseline unavailable: {context.scanner} network unreachable"
        return None, f"baseline unavailable: {context.scanner} exited {result.returncode}"
    return keys, None


def run_dependency_vuln(args: argparse.Namespace) -> int:
    repo_root = detect_repo_root(args.repo_root)
    evidence_path = repo_path(repo_root, args.evidence, repo_root / "plc-evidence.jsonl")
    artifact_dir = repo_path(repo_root, args.artifact_dir, repo_root / ".plc" / "security")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    scope = build_scope(repo_root, args.path)
    # Issue #10 ask 1: also scan dependency manifests/lockfiles that live in
    # paths the SAST review-exclusion drops (vendor/, third_party/, etc.).
    # Those are typically standalone third-party apps not registered as
    # parent-project dependencies in nSpect/BlackDuck, so the local advisory
    # pass is the only CVE signal they get. The resulting DependencyContext
    # carries colocated_app=True so the per-context evidence makes that
    # visible.
    contexts = dependency_contexts(repo_root, scope.files, scope.review_excluded)
    colocated_context_count = sum(1 for c in contexts if c.colocated_app)
    record = base_record(repo_root, "dependency-vuln", "pip-audit/osv-scanner", evidence_path)
    record.update(
        {
            "base_ref": scope.base_ref,
            "scope_count": len(scope.files),
            "scope_files": list(scope.files),
            "dependency_context_count": len(contexts),
            "dependency_contexts": [dependency_context_record(context) for context in contexts],
        }
    )
    if colocated_context_count:
        record["colocated_app_context_count"] = colocated_context_count
    if scope.rescope_reason:
        record["scope_rescope"] = scope.rescope_reason
    if scope.always_excluded:
        record["scope_always_excluded_count"] = len(scope.always_excluded)
    if scope.review_excluded:
        record["scope_review_excluded_count"] = len(scope.review_excluded)

    # Issue #10 (review-10 findings 1 + 2): compute no-lockfile gaps for
    # BOTH scope.files and scope.review_excluded, regardless of whether
    # any dependency contexts were found. Multi-component repos can have
    # a scanned lockfile in one directory and a missing-lock gap in
    # another; the missing-lock gap must surface anyway. Colocated paths
    # (vendor/third_party/external/submodules) carry the colocated_app
    # marker on the gap so reviewers can see the parent-gate boundary.
    all_dep_files = list(scope.files) + list(scope.review_excluded)
    orphan_pyprojects = orphan_pyproject_paths(repo_root, all_dep_files)
    source_only_paths = dep_carrying_source_paths(all_dep_files)
    # Source-only is a "real" no-manifest gap only when no dependency
    # context exists in the SAME component directory. Issue #10 (review-12
    # finding 1): a scanned `web/package-lock.json` must not mask a
    # source-only gap for an unrelated `service/app.py` in the same work
    # unit. Compare each source-only path's directory tree against each
    # context's directory; drop a source-only path only when it is at or
    # under a context directory.
    context_dirs = {c.directory for c in contexts}

    def _under_any_context(rel_path: str) -> bool:
        # Walk parent directories of the changed source file. If any
        # ancestor (or the file's own directory) matches a dependency-
        # context directory, the file is already in a known ecosystem and
        # the source-only gap is not needed for it. Both sides use the
        # `c.directory` convention: "." for the repo root, otherwise a
        # POSIX-style relative path with no leading "./".
        parent = Path(rel_path).parent
        while True:
            parent_posix = parent.as_posix()
            if parent_posix in context_dirs:
                return True
            if parent_posix == ".":
                return False
            parent = parent.parent

    source_only_paths = [
        p for p in source_only_paths if not _under_any_context(p)
    ]
    orphan_gap = bool(orphan_pyprojects)
    source_only_gap = bool(source_only_paths)
    if orphan_gap or source_only_gap:
        # Mark colocation if every affected path is under a colocated
        # prefix (so the gap can be attributed to a colocated app vs the
        # parent project).
        gap_paths = list(orphan_pyprojects) + list(source_only_paths)
        gap_colocated = bool(gap_paths) and all(is_colocated_path(p) for p in gap_paths)
        gap_reasons: list[str] = []
        if orphan_gap:
            record["orphan_pyproject_count"] = len(orphan_pyprojects)
            record["orphan_pyprojects"] = list(orphan_pyprojects)
            gap_reasons.append(
                "pyproject.toml changed but no uv.lock or poetry.lock present"
            )
        if source_only_gap:
            record["source_only_dep_gap_count"] = len(source_only_paths)
            record["source_only_dep_gap_paths"] = list(source_only_paths)
            gap_reasons.append(
                "source in a dependency-bearing ecosystem changed but no "
                "manifest or lockfile is in scope"
            )
        if orphan_gap and source_only_gap:
            lockfile_status = "manifest-only-and-absent"
        elif orphan_gap:
            lockfile_status = "manifest-only"
        else:
            lockfile_status = "absent"
        record["lockfile_status"] = lockfile_status
        if gap_colocated:
            record["colocated_app_gap"] = True

    if not contexts:
        if orphan_gap or source_only_gap:
            record.update(
                {
                    "result": "warn",
                    "reason": "; ".join(gap_reasons)
                    + " — generate a lock file or supply a requirements.txt; "
                    "local advisory dependency scan cannot run and BlackDuck "
                    "has nothing to ingest either",
                }
            )
            append_evidence(evidence_path, record)
            print(f"DEPENDENCY-VULN WARN: {record['reason']}")
            return _exit_code_for_result(record["result"], args.fail_on)
        record.update(
            {
                "result": "n/a",
                "reason": "no changed dependency manifests or lockfiles in reviewable scope",
                "lockfile_status": "absent",
            }
        )
        append_evidence(evidence_path, record)
        print(f"DEPENDENCY-VULN N/A: {record['reason']}")
        return _exit_code_for_result(record["result"], args.fail_on)

    context_results: list[dict] = []
    new_keys: set[str] = set()
    pre_existing_keys: set[str] = set()
    unknown_keys: set[str] = set()
    skipped = 0
    errors = 0
    scanned = 0

    def _stub_dep_artifact(ctx_result: dict, ctx: DependencyContext, reason: str) -> None:
        """Write a SKIP stub artifact for a dependency context that did not run.

        Issue #10 (review-10 finding 5): every dependency-context outcome must
        leave a JSON artifact at the predictable per-context path, and the
        evidence row must carry the `artifact` field, so PR reviewers can
        open the artifact even when the tool did not run.
        """
        artifact = dependency_artifact_path(artifact_dir, ctx)
        stub = {
            "check": "dependency-vuln",
            "tool": ctx.scanner,
            "ecosystem": ctx.ecosystem,
            "manager": ctx.manager,
            "directory": ctx.directory,
            "manifest": ctx.manifest,
            "lockfile": ctx.lockfile,
            "result": "skip",
            "reason": reason,
            "colocated_app": bool(ctx.colocated_app),
            "timestamp": utc_now(),
        }
        atomic_write_text(
            artifact, json.dumps(stub, indent=2, sort_keys=True) + "\n"
        )
        ctx_result["artifact"] = (
            artifact.relative_to(repo_root).as_posix()
            if artifact.is_relative_to(repo_root)
            else artifact.as_posix()
        )

    for context in contexts:
        cwd = context_cwd(repo_root, context)
        context_result = dependency_context_record(context)
        context_result["cwd"] = cwd.relative_to(repo_root).as_posix() if cwd != repo_root else "."

        if context.check_command is not None:
            if not command_available(context.check_command):
                skipped += 1
                reason = f"{command_name(context.check_command)} not installed"
                context_result.update({"result": "skip", "reason": reason})
                _stub_dep_artifact(context_result, context, reason)
                context_results.append(context_result)
                continue
            check = run_command(list(context.check_command), cwd=cwd, timeout=args.timeout_seconds)
            if check.timed_out:
                skipped += 1
                reason = f"{command_name(context.check_command)} timed out after {check.timeout_seconds}s"
                context_result.update(
                    {
                        "result": "skip",
                        "reason": reason,
                        "timed_out": True,
                    }
                )
                _stub_dep_artifact(context_result, context, reason)
                context_results.append(context_result)
                continue
            if check.returncode != 0:
                skipped += 1
                if unsupported_lock_check(check) and context.version_too_old_reason:
                    reason = context.version_too_old_reason
                elif networkish_failure(check):
                    reason = f"{command_name(context.check_command)} network unreachable for lockfile check"
                else:
                    reason = context.stale_reason or f"{command_name(context.check_command)} lockfile consistency check failed"
                context_result.update({"result": "skip", "reason": reason})
                _stub_dep_artifact(context_result, context, reason)
                context_results.append(context_result)
                continue

        if context.unavailable_reason and not command_available(context.scan_command):
            skipped += 1
            context_result.update({"result": "skip", "reason": context.unavailable_reason})
            _stub_dep_artifact(context_result, context, context.unavailable_reason)
            context_results.append(context_result)
            continue

        if not command_available(context.scan_command):
            skipped += 1
            reason = f"{command_name(context.scan_command)} not installed"
            context_result.update({"result": "skip", "reason": reason})
            _stub_dep_artifact(context_result, context, reason)
            context_results.append(context_result)
            continue

        result = run_command(list(context.scan_command), cwd=cwd, env=context.scan_env, timeout=args.timeout_seconds)
        data = load_json_output(result.stdout) or load_json_output(result.stderr)
        finding_keys = dependency_finding_keys(data, context.ecosystem) if data is not None else set()

        artifact = dependency_artifact_path(artifact_dir, context)
        raw_scanner_output = result.stdout if result.stdout.strip() else result.stderr
        atomic_write_text(
            artifact,
            raw_scanner_output if raw_scanner_output.strip() else json.dumps(data or {}, indent=2, sort_keys=True) + "\n",
        )
        context_result["artifact"] = artifact.relative_to(repo_root).as_posix() if artifact.is_relative_to(repo_root) else artifact.as_posix()

        if result.timed_out:
            skipped += 1
            context_result.update(
                {
                    "result": "skip",
                    "reason": f"{context.scanner} timed out after {result.timeout_seconds}s",
                    "timed_out": True,
                }
            )
        elif data is None:
            if result.returncode != 0 and networkish_failure(result):
                skipped += 1
                context_result.update({"result": "skip", "reason": f"{context.scanner} network unreachable"})
            else:
                errors += 1
                context_result.update({"result": "error", "reason": f"{context.scanner} returned malformed JSON"})
        elif result.returncode != 0 and not finding_keys:
            if networkish_failure(result):
                skipped += 1
                context_result.update({"result": "skip", "reason": f"{context.scanner} network unreachable"})
            else:
                errors += 1
                context_result.update(
                    {"result": "error", "reason": f"{context.scanner} exited {result.returncode}; no parseable findings"}
                )
        else:
            scanned += 1
            context_new_keys: set[str] = set()
            context_pre_existing_keys: set[str] = set()
            context_unknown_keys: set[str] = set()
            if finding_keys:
                baseline_keys, baseline_reason = baseline_dependency_keys(
                    repo_root,
                    context,
                    scope.base_ref,
                    args.timeout_seconds,
                )
                if baseline_keys is None:
                    context_unknown_keys = finding_keys
                    unknown_keys.update(finding_keys)
                    context_result["baseline_reason"] = baseline_reason
                else:
                    context_pre_existing_keys = finding_keys.intersection(baseline_keys)
                    context_new_keys = finding_keys.difference(context_pre_existing_keys)
                    pre_existing_keys.update(context_pre_existing_keys)
                    new_keys.update(context_new_keys)
            context_result.update(
                {
                    "result": "warn" if finding_keys else "pass",
                    "finding_count": len(finding_keys),
                    "new_finding_count": len(context_new_keys),
                    "pre_existing_finding_count": len(context_pre_existing_keys),
                    "unknown_baseline_finding_count": len(context_unknown_keys),
                }
            )
        context_results.append(context_result)

    record.update(
        {
            "dependency_context_results": context_results,
            "dependency_contexts_scanned": scanned,
            "dependency_contexts_skipped": skipped,
            "findings_new": len(new_keys),
            "findings_pre_existing": len(pre_existing_keys),
            "findings_unknown_baseline": len(unknown_keys),
            "findings_actionable": len(new_keys) + len(unknown_keys),
        }
    )
    if errors:
        record.update({"result": "error", "reason": f"{errors} dependency context(s) errored"})
    elif new_keys or pre_existing_keys or unknown_keys:
        record["result"] = "warn"
    elif skipped:
        record.update({"result": "skip", "reason": f"{skipped} dependency context(s) skipped"})
    else:
        record["result"] = "pass"

    # Issue #10 (review-10 finding 1): a scanned dependency context must not
    # mask a separate no-lockfile gap in the same work unit. Upgrade PASS /
    # SKIP / N/A to WARN when an orphan-pyproject or source-only gap was
    # detected above, and attach the gap reason to the record.
    if (orphan_gap or source_only_gap) and record["result"] in ("pass", "skip", "n/a"):
        gap_reason = "; ".join(gap_reasons)
        existing_reason = record.get("reason")
        record["result"] = "warn"
        if existing_reason:
            record["reason"] = f"{gap_reason}; also: {existing_reason}"
        else:
            record["reason"] = gap_reason

    append_evidence(evidence_path, record)
    print(
        "DEPENDENCY-VULN {status}: {new} new, {pre} pre-existing, {unknown} unknown-baseline finding(s); "
        "{scanned} scanned, {skipped} skipped".format(
            status=str(record["result"]).upper(),
            new=record["findings_new"],
            pre=record["findings_pre_existing"],
            unknown=record["findings_unknown_baseline"],
            scanned=scanned,
            skipped=skipped,
        )
    )
    return _exit_code_for_result(record["result"], args.fail_on)


def semgrep_counts(data: dict) -> tuple[int, int, int]:
    high = medium = low = 0
    for finding in data.get("results", []):
        severity = str(finding.get("extra", {}).get("severity", "")).upper()
        if severity == "ERROR":
            high += 1
        elif severity == "WARNING":
            medium += 1
        else:
            low += 1
    return high, medium, low


FAIL_ON_CHOICES = ("pass", "warn", "error")


def _exit_code_for_result(result: str, fail_on: str) -> int:
    result_normalized = (result or "").lower()
    fail_on_normalized = (fail_on or "error").lower()
    severity_rank = {"pass": 0, "skip": 0, "warn": 1, "error": 2}
    threshold_rank = {"pass": 1, "warn": 1, "error": 2}
    if fail_on_normalized == "pass":
        return 2 if result_normalized != "pass" else 0
    return (
        2
        if severity_rank.get(result_normalized, 0) >= threshold_rank.get(fail_on_normalized, 2)
        else 0
    )


def partial_scope_reason(record: dict) -> str:
    parts = []
    if record.get("scope_copied_count") is not None:
        parts.append(f"{record['scope_copied_count']} copied")
    if record.get("scope_missing_file_count"):
        parts.append(f"{record['scope_missing_file_count']} missing/non-file")
    if record.get("scope_symlink_skipped_count"):
        parts.append(f"{record['scope_symlink_skipped_count']} symlink skipped")
    return "partial scope not scanned: " + ", ".join(parts)


def run_sast(args: argparse.Namespace) -> int:
    repo_root = detect_repo_root(args.repo_root)
    evidence_path = repo_path(repo_root, args.evidence, repo_root / "plc-evidence.jsonl")
    artifact_dir = repo_path(repo_root, args.artifact_dir, repo_root / ".plc" / "security")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Define the artifact path before early returns so every SAST outcome
    # writes `.plc/security/semgrep.json`.
    semgrep_json = artifact_dir / "semgrep.json"
    artifact_rel = (
        semgrep_json.relative_to(repo_root).as_posix()
        if semgrep_json.is_relative_to(repo_root)
        else semgrep_json.as_posix()
    )

    scope = build_scope(repo_root, args.path)

    # SAST-specific scope filter: env files contain shell-style key=value
    # secrets, not source-code constructs. Drop them from the SAST scope by
    # default; the secret scanner (Pulse) still sees them via its own scope.
    env_excluded: list[str] = []
    if not env_bool("PLC_INCLUDE_ENV_FILES_IN_SAST", False):
        kept: list[str] = []
        for path in scope.files:
            if ENV_FILE_PATTERN.search(path):
                env_excluded.append(path)
            else:
                kept.append(path)
        scope.files = kept

    scope_file = artifact_dir / "scope-files.txt"
    unfiltered_file = artifact_dir / "scope-files-unfiltered.txt"
    write_lines(scope_file, scope.files)
    write_lines(unfiltered_file, scope.unfiltered)

    record = base_record(repo_root, "sast", "semgrep", evidence_path)
    record.update(
        {
            "base_ref": scope.base_ref,
            "scope_count": len(scope.files),
            "scope_files": list(scope.files),
            "scope_file": scope_file.relative_to(repo_root).as_posix()
            if scope_file.is_relative_to(repo_root)
            else scope_file.as_posix(),
            "artifact": artifact_rel,
        }
    )
    if scope.rescope_reason:
        record["scope_rescope"] = scope.rescope_reason
    if scope.always_excluded:
        record["scope_always_excluded_count"] = len(scope.always_excluded)
    if scope.review_excluded:
        record["scope_review_excluded_count"] = len(scope.review_excluded)
    if scope.ignored_paths:
        record["scope_ignored_path_count"] = len(scope.ignored_paths)
    if env_excluded:
        record["scope_env_excluded_count"] = len(env_excluded)
        record["scope_env_excluded"] = list(env_excluded)

    def _finalize_skip(reason: str) -> int:
        record.update({"result": "skip", "reason": reason})
        write_sast_stub(semgrep_json, result="skip", reason=reason)
        append_evidence(evidence_path, record)
        print(f"SAST SKIP: {reason}")
        return _exit_code_for_result(record["result"], args.fail_on)

    if not scope.files:
        if scope.rescope_reason and "nothing to scan" in scope.rescope_reason:
            reason = scope.rescope_reason
        elif scope.rescope_reason:
            reason = f"changed-file scope unavailable: {scope.rescope_reason}"
        elif env_excluded:
            # Issue #10 (review-10 finding 7): the env-exclusion policy is
            # intentional. When dropping env files emptied the scope, say
            # so explicitly — the user should not see a generic "no files"
            # message; they should see the policy and the secret-scanner
            # follow-up, plus the override env var.
            reason = (
                f"{len(env_excluded)} env file(s) excluded from SAST by default "
                "(run Pulse secret scanner for env files; set "
                "PLC_INCLUDE_ENV_FILES_IN_SAST=1 to override)"
            )
        else:
            reason = "no reviewable files for Semgrep"
        return _finalize_skip(reason)

    semgrep_command, tool_source, semgrep_env, semgrep_label = project_local_semgrep(repo_root, args.semgrep)
    record["tool_source"] = tool_source
    if semgrep_command is None:
        return _finalize_skip(semgrep_label)

    scope_copied_count = 0
    scope_missing_file_count = 0
    scope_symlink_skipped_count = 0
    with tempfile.TemporaryDirectory(prefix="plc-security-artifacts.") as temp_name:
        temp_root = Path(temp_name)
        scan_tree = make_scan_tree(repo_root, scope.files)
        try:
            scope_copied_count = len(scan_tree.copied_files)
            scope_missing_file_count = len(scan_tree.missing_files)
            scope_symlink_skipped_count = len(scan_tree.skipped_symlinks)
            record["scope_copied_count"] = scope_copied_count
            if scope_missing_file_count:
                record["scope_missing_file_count"] = scope_missing_file_count
            if scope_symlink_skipped_count:
                record["scope_symlink_skipped_count"] = scope_symlink_skipped_count
            if not scan_tree.copied_files:
                return _finalize_skip("no regular files copied to Semgrep scan tree")
            semgrep_config = args.config
            if semgrep_config == "auto":
                # `semgrep --config=auto` requires Semgrep telemetry to fetch
                # the registry ruleset, so it fails outright under the
                # `--metrics off` posture PLC scans always use
                # ("Cannot create auto config when metrics are off"). Fall
                # back to the bundled local ruleset so the scan still
                # produces evidence instead of skipping silently.
                record["semgrep_config_requested"] = "auto"
                record["semgrep_config_fallback"] = "auto->bundled"
                print(
                    "SAST NOTE: Semgrep --config=auto requires metrics; "
                    "falling back to bundled local rules."
                )
                semgrep_config = "bundled"
            if semgrep_config == "bundled":
                offline_rules = extract_offline_rules(Path(args.offline_rules), temp_root)
                if offline_rules is None:
                    return _finalize_skip("bundled Semgrep rules unavailable")
                semgrep_config = offline_rules.as_posix()
            # Issue #10 (review-10 finding 3): the bundled Rust ruleset is
            # INFO/WARNING only. When `.rs` files are in scope, drop the
            # `--severity=ERROR` filter so those rules actually run.
            severity_threshold: str | None = "ERROR"
            if scope_has_rust_sources(scope.files):
                severity_threshold = None
            result, data = run_semgrep(
                semgrep_command=semgrep_command,
                semgrep_env=semgrep_env,
                config=semgrep_config,
                scan_root=scan_tree.root,
                timeout_seconds=args.timeout_seconds,
                severity_threshold=severity_threshold,
            )
        finally:
            scan_tree.temp.cleanup()

    high, medium, low = semgrep_counts(data)
    errors = data.get("errors") or []
    output_missing = "results" not in data and "errors" not in data
    record.update(
        {
            "findings_high": high,
            "findings_medium": medium,
            "findings_low": low,
            "severity_filter": severity_threshold if severity_threshold else "ALL",
        }
    )
    if errors:
        reason = errors[0].get("message", "semgrep failed")
    else:
        reason = ""
    partial_scope = bool(record.get("scope_missing_file_count") or record.get("scope_symlink_skipped_count"))
    if partial_scope:
        record["scope_partial_reason"] = partial_scope_reason(record)
    timed_out = False
    if result.timed_out:
        timed_out = True
        record.update(
            {
                "result": "skip",
                "reason": f"semgrep timed out after {result.timeout_seconds}s",
                "timed_out": True,
            }
        )
    elif result.returncode != 0:
        if networkish_failure(result, reason):
            record.update({"result": "skip", "reason": f"semgrep network unreachable: {reason or 'see stderr'}"})
        else:
            record.update({"result": "error", "reason": reason or f"semgrep exited {result.returncode}; no diagnostics"})
    elif errors:
        record.update({"result": "error", "reason": reason})
    elif output_missing:
        record.update({"result": "error", "reason": "semgrep returned empty JSON"})
    elif high or medium or low:
        record["result"] = "warn"
        if partial_scope:
            record["reason"] = record["scope_partial_reason"]
    elif partial_scope:
        record.update({"result": "warn", "reason": record["scope_partial_reason"]})
    else:
        record["result"] = "pass"

    # Outcome decides whether the artifact carries findings or a stub. An
    # `error` or `skip` outcome means we don't have a trustworthy findings
    # projection, so write a stub instead of a "looks-successful empty scan".
    if record["result"] in ("error", "skip"):
        write_sast_stub(
            semgrep_json,
            result=record["result"],
            reason=record.get("reason"),
            extra={"timed_out": True} if record.get("timed_out") else None,
        )
    else:
        write_sast_findings(semgrep_json, data)

    append_evidence(evidence_path, record)
    if record["result"] in ("skip", "error") and record.get("reason"):
        print(f"SAST {record['result'].upper()}: {record['reason']}")
    else:
        print(f"SAST {record['result'].upper()}: {high} high, {medium} medium, {low} low")
    return _exit_code_for_result(record["result"], args.fail_on)


def read_evidence(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


LICENSE_MISSING_TOKENS = frozenset({"", "unknown", "none", "null"})

# License substrings that always require OSRB review. Match is case-insensitive
# and substring-based so explicit `GPL-3.0-only`, classifier strings like
# `License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)`,
# and free-form "AGPL" all trigger. Custom/Other declarations also escalate
# because we cannot classify them locally — only OSRB can.
OSRB_LICENSE_PATTERNS = (
    # `\bgpl` (no trailing `\b`) catches `GPL`, `GPL-3.0`, `GPLv3`, `gpl_v2`,
    # etc. Same shape for the related family below. The leading word-boundary
    # prevents incidental matches inside unrelated words.
    re.compile(r"\bgpl", re.IGNORECASE),
    re.compile(r"\blgpl", re.IGNORECASE),
    re.compile(r"\bagpl", re.IGNORECASE),
    re.compile(r"\bsspl", re.IGNORECASE),
    re.compile(r"\bgnu general public\b", re.IGNORECASE),
    re.compile(r"\blesser general public\b", re.IGNORECASE),
    re.compile(r"\baffero\b", re.IGNORECASE),
    re.compile(r"\bcommons[ -]clause\b", re.IGNORECASE),
    re.compile(r"\bcc[ -]by[ -]nc\b", re.IGNORECASE),
    re.compile(r"\bcc[ -]by[ -]sa\b", re.IGNORECASE),
    re.compile(r"\bproprietary\b", re.IGNORECASE),
    re.compile(r"\bcustom\b", re.IGNORECASE),
)


def license_requires_osrb_review(license_field: object, classifiers: list[str]) -> bool:
    """True if the License field or any classifier names an OSRB-class license.

    Issue #10 ask 4 (review-8 follow-up): the original normalizer only
    warned on missing metadata. Explicit copyleft / proprietary / custom
    licenses must also escalate so a clean local PASS does not hide a
    license that needs human OSRB review.
    """
    candidates: list[str] = []
    if license_field is not None:
        candidates.append(str(license_field))
    candidates.extend(str(c) for c in classifiers)
    for text in candidates:
        for pattern in OSRB_LICENSE_PATTERNS:
            if pattern.search(text):
                return True
    return False


def license_is_missing(license_field: object) -> bool:
    """True if a pip-licenses License field looks like a metadata gap.

    pip-licenses prints whatever the wheel's metadata declared. Empty,
    `UNKNOWN`, `None`, or `null` all mean "the package did not declare a
    license"; that is a false negative on the local pass, not a clean
    result.
    """
    if license_field is None:
        return True
    text = str(license_field).strip().lower()
    return text in LICENSE_MISSING_TOKENS


def normalize_pip_licenses(raw_data: object) -> dict:
    """Project raw `pip-licenses --format=json` output to PLC license evidence.

    Input: list of dicts from `pip-licenses` (`Name`, `Version`, `License`,
    optional `License-Expression`, optional classifier fields).

    Output: dict with the per-package projection, the count of packages
    whose license metadata is missing, and the aggregate `result`/`reason`
    fields the runner writes to `.plc/security/pip-licenses.json` and to
    `plc-evidence.jsonl`. WARN with `license_metadata_missing` whenever
    any package is missing license metadata, because BlackDuck/OSRB is the
    only path that can confirm or override that gap.
    """
    if not isinstance(raw_data, list):
        return {
            "check": "license",
            "tool": "pip-licenses",
            "result": "error",
            "reason": "pip-licenses output was not a JSON list",
            "packages": [],
            "missing_metadata": [],
            "missing_metadata_count": 0,
            "total_packages": 0,
        }

    packages: list[dict] = []
    missing: list[dict] = []
    osrb_review: list[dict] = []
    for entry in raw_data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or entry.get("name") or "").strip()
        version = str(entry.get("Version") or entry.get("version") or "").strip()
        license_field = entry.get("License") if "License" in entry else entry.get("license")
        license_expression = (
            entry.get("License-Expression")
            if "License-Expression" in entry
            else entry.get("license_expression")
        )
        classifiers_raw = entry.get("Classifier") or entry.get("classifier") or []
        if isinstance(classifiers_raw, str):
            classifiers = [classifiers_raw]
        elif isinstance(classifiers_raw, list):
            classifiers = [str(c) for c in classifiers_raw if c]
        else:
            classifiers = []
        license_classifiers = [c for c in classifiers if "License" in c][:3]
        projection = {
            "package": f"{name}=={version}" if name and version else name or "",
            "name": name,
            "version": version,
            "license": str(license_field) if license_field is not None else None,
            "license_expression": str(license_expression) if license_expression else None,
            "license_classifiers": license_classifiers,
        }
        # Issue #10 (review-10 finding 4): evaluate the two review reasons
        # independently and let a package carry both. A package with
        # License: UNKNOWN AND License-Expression: GPL-3.0-only is both a
        # metadata-missing case AND an OSRB-class case; counting only one
        # would hide the copyleft signal the issue exists to surface.
        reasons: list[str] = []
        if license_is_missing(license_field):
            reasons.append("license_metadata_missing")
            missing.append(projection)
        # License-Expression is part of the candidate set: modern metadata
        # can put the authoritative SPDX expression there even when the
        # legacy License field is missing or permissive.
        osrb_candidates = list(classifiers)
        if license_expression is not None:
            osrb_candidates.append(str(license_expression))
        if license_requires_osrb_review(license_field, osrb_candidates):
            reasons.append("license_requires_osrb_review")
            osrb_review.append(projection)
        if reasons:
            projection["review_reasons"] = reasons
        packages.append(projection)

    if missing and osrb_review:
        result = "warn"
        reason = "license_metadata_missing_and_requires_osrb_review"
    elif missing:
        result = "warn"
        reason = "license_metadata_missing"
    elif osrb_review:
        result = "warn"
        reason = "license_requires_osrb_review"
    elif packages:
        result = "pass"
        reason = ""
    else:
        result = "skip"
        reason = "pip-licenses produced no rows"

    out: dict = {
        "check": "license",
        "tool": "pip-licenses",
        "result": result,
        "packages": packages,
        "missing_metadata": missing,
        "missing_metadata_count": len(missing),
        "osrb_review": osrb_review,
        "osrb_review_count": len(osrb_review),
        "total_packages": len(packages),
    }
    if reason:
        out["reason"] = reason
    return out


def run_normalize_licenses(args: argparse.Namespace) -> int:
    """CLI: read pip-licenses JSON, write the normalized PLC license artifact.

    Reads the raw scanner output, applies `normalize_pip_licenses`, writes
    the projected artifact to `--output`, and appends one evidence row to
    `--evidence`. Designed to be used from the shell snippet immediately
    after `pip-licenses --format=json --output-file=...`.
    """
    repo_root = detect_repo_root(args.repo_root)
    evidence_path = repo_path(repo_root, args.evidence, repo_root / "plc-evidence.jsonl")
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else repo_root / ".plc" / "security" / "pip-licenses.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        normalized = {
            "check": "license",
            "tool": "pip-licenses",
            "result": "error",
            "reason": f"could not read pip-licenses input: {exc}",
            "packages": [],
            "missing_metadata": [],
            "missing_metadata_count": 0,
            "total_packages": 0,
        }
    else:
        normalized = normalize_pip_licenses(raw)

    artifact_rel = (
        output_path.relative_to(repo_root).as_posix()
        if output_path.is_relative_to(repo_root)
        else output_path.as_posix()
    )
    normalized["timestamp"] = utc_now()
    atomic_write_text(
        output_path, json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    )

    record = base_record(repo_root, "license", "pip-licenses", evidence_path)
    record.update(
        {
            "result": normalized["result"],
            "missing_metadata_count": normalized["missing_metadata_count"],
            "osrb_review_count": normalized.get("osrb_review_count", 0),
            "total_packages": normalized["total_packages"],
            "artifact": artifact_rel,
        }
    )
    if normalized.get("reason"):
        record["reason"] = normalized["reason"]
    append_evidence(evidence_path, record)
    print(
        f"LICENSE {normalized['result'].upper()}: "
        f"{normalized['missing_metadata_count']} missing-metadata, "
        f"{normalized.get('osrb_review_count', 0)} osrb-review / "
        f"{normalized['total_packages']} packages"
    )
    return _exit_code_for_result(record["result"], args.fail_on)


def top_sast_findings(
    repo_root: Path, artifact_rel: str | None, limit: int = 3
) -> list[str]:
    """Return up to `limit` SAST findings as `path:start_line check_id`.

    Reads the sanitized Semgrep artifact at `artifact_rel` and emits the
    first `limit` distinct findings. Returns an empty list on any read /
    parse failure so the caller can fall back to count-only details.
    """
    if not artifact_rel:
        return []
    artifact_path = (repo_root / artifact_rel) if not Path(artifact_rel).is_absolute() else Path(artifact_rel)
    try:
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    seen: list[str] = []
    for finding in data.get("results") or []:
        path = finding.get("path") or ""
        start_line = finding.get("start_line")
        check_id = finding.get("check_id") or ""
        if not path or not check_id or start_line is None:
            continue
        entry = f"{path}:{start_line} {check_id}"
        if entry not in seen:
            seen.append(entry)
        if len(seen) >= limit:
            break
    return seen


def status_details(record: dict, repo_root: Path | None = None) -> str:
    reason = str(record.get("reason", "") or "")
    result = str(record.get("result", "") or "").lower()
    if record.get("check") == "dependency-vuln":
        new = int(record.get("findings_new", 0) or 0)
        pre_existing = int(record.get("findings_pre_existing", 0) or 0)
        unknown = int(record.get("findings_unknown_baseline", 0) or 0)
        scanned = int(record.get("dependency_contexts_scanned", 0) or 0)
        skipped = int(record.get("dependency_contexts_skipped", 0) or 0)
        contexts = int(record.get("dependency_context_count", 0) or 0)
        if new or pre_existing or unknown:
            details = (
                f"{new} new, {pre_existing} pre-existing, {unknown} unknown-baseline "
                f"finding(s); {scanned} scanned"
            )
            if skipped:
                details += f", {skipped} skipped"
            return details
        if reason:
            return reason
        if contexts:
            details = "0 findings"
            if scanned or skipped:
                details += f"; {scanned} scanned"
            if skipped:
                details += f", {skipped} skipped"
            return details
        return "no changed dependency manifests or lockfiles in reviewable scope"
    if reason and result in ("skip", "error"):
        return reason
    high = int(record.get("findings_high", 0) or 0)
    medium = int(record.get("findings_medium", 0) or 0)
    low = int(record.get("findings_low", 0) or 0)
    severity_filter = record.get("severity_filter")
    partial = record.get("scope_partial_reason")
    if high or medium or low:
        if severity_filter == "ERROR":
            details = f"{high} high (severity filter: ERROR)"
        else:
            details = f"{high} high, {medium} medium, {low} low"
        if partial:
            details += f"; {partial}"
        elif reason:
            details += f"; {reason}"
        if record.get("check") == "sast" and repo_root is not None:
            top = top_sast_findings(repo_root, record.get("artifact"))
            if top:
                details += " · top: " + ", ".join(f"`{entry}`" for entry in top)
        return details
    if record.get("scope_count") is not None:
        if severity_filter == "ERROR":
            details = f"{record['scope_count']} file(s), 0 high findings (severity filter: ERROR)"
        else:
            details = f"{record['scope_count']} file(s), 0 high findings"
        if partial:
            details += f"; {partial}"
        elif reason:
            details += f"; {reason}"
        return details
    if reason:
        return reason
    return "completed"


def worktree_dirty_paths(repo_root: Path) -> list[str]:
    """Return the union of staged, unstaged, and untracked non-ignored paths.

    Uses SCM-specific commands that return clean repo-relative POSIX paths
    suitable for set intersection.
    """
    if not is_git_repo(repo_root):
        return p4_opened_names(repo_root) if is_p4_workspace(repo_root) else []
    paths: set[str] = set()
    paths.update(git_diff_names(repo_root))
    paths.update(git_diff_names(repo_root, "--cached"))
    paths.update(git_untracked_names(repo_root))
    return sorted(paths)


def commit_diff_paths(repo_root: Path, old_commit: str, new_commit: str) -> list[str] | None:
    """git diff --name-only old..new. Returns None if the diff cannot run."""
    if not is_git_repo(repo_root):
        return None
    for ref in (old_commit, new_commit):
        if (
            run_command(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_root).returncode
            != 0
        ):
            return None
    output = git_output(repo_root, "diff", "--name-only", f"{old_commit}..{new_commit}")
    if output is None:
        return None
    return [line for line in output.splitlines() if line]


def render_summary(args: argparse.Namespace) -> int:
    repo_root = detect_repo_root(args.repo_root)
    evidence_path = repo_path(repo_root, args.evidence, repo_root / "plc-evidence.jsonl")
    branch = args.branch or current_branch(repo_root)
    commit = current_commit(repo_root)
    current_dirty = set(worktree_dirty_paths(repo_root))
    scm = scm_metadata(repo_root).get("scm", "none")
    latest_by_check: dict[str, dict] = {}
    for record in read_evidence(evidence_path):
        if record.get("branch") == branch:
            latest_by_check[str(record.get("check", "unknown"))] = record

    print("## PLC Security Evidence")
    revision_label = "HEAD" if scm == "git" else "Workspace"
    revision = commit[:7] if scm == "git" and commit != "unknown" else commit
    print(f"Branch: {branch} | {revision_label}: {revision}")
    print()
    print("| Check | Status | Tool | Details | Artifact |")
    print("|-------|--------|------|---------|----------|")
    if not latest_by_check:
        print("| Security scans | SKIP | - | no evidence for this branch | - |")
        return 0
    for check, record in sorted(latest_by_check.items()):
        status = str(record.get("result", "unknown")).upper()
        details = status_details(record, repo_root=repo_root)
        record_commit = record.get("commit")
        scope_files = record.get("scope_files")
        stale_marked = False
        if scm == "git" and record_commit and record_commit != commit:
            if isinstance(scope_files, list):
                scope_set = {str(path) for path in scope_files}
                diff_paths = commit_diff_paths(repo_root, str(record_commit), commit)
                if diff_paths is None:
                    # Diff unrunnable (e.g. unreachable old commit); fall back
                    # to the legacy behaviour of marking stale.
                    status = f"{status} (STALE)"
                    details = f"{details}; evidence commit {str(record_commit)[:7]}"
                    stale_marked = True
                else:
                    overlap = scope_set.intersection(diff_paths)
                    dirty_overlap = scope_set.intersection(current_dirty)
                    if overlap or dirty_overlap:
                        status = f"{status} (STALE)"
                        details = f"{details}; evidence commit {str(record_commit)[:7]}"
                        stale_marked = True
            else:
                # Older evidence without scope_files: legacy commit-mismatch behaviour.
                status = f"{status} (STALE)"
                details = f"{details}; evidence commit {str(record_commit)[:7]}"
                stale_marked = True
        if not stale_marked:
            current_worktree_digest = worktree_digest(repo_root, evidence_path) if record.get("worktree_digest") else None
            if record.get("worktree_digest") and current_worktree_digest != record.get("worktree_digest"):
                if isinstance(scope_files, list):
                    scope_set = {str(path) for path in scope_files}
                    if scope_set.intersection(current_dirty):
                        status = f"{status} (STALE: uncommitted changes)"
                        details = f"{details}; worktree changed after scan"
                else:
                    status = f"{status} (STALE: uncommitted changes)"
                    details = f"{details}; worktree changed after scan"
            elif not record.get("worktree_digest") and current_dirty:
                if isinstance(scope_files, list):
                    scope_set = {str(path) for path in scope_files}
                    if scope_set.intersection(current_dirty):
                        status = f"{status} (STALE: uncommitted changes)"
                        details = f"{details}; worktree has unscanned changes"
        artifact_cell = summary_artifact_cell(record)
        print(
            "| {check} | {status} | {tool} | {details} | {artifact} |".format(
                check=check,
                status=status,
                tool=record.get("tool", "-"),
                details=details.replace("|", "/"),
                artifact=artifact_cell.replace("|", "/"),
            )
        )
    return 0


def summary_artifact_cell(record: dict) -> str:
    """Render the Artifact column for one evidence record."""
    artifact = record.get("artifact")
    if isinstance(artifact, str) and artifact:
        return f"`{artifact}`"
    contexts = record.get("dependency_context_results")
    if isinstance(contexts, list):
        paths = [c.get("artifact") for c in contexts if isinstance(c, dict) and c.get("artifact")]
        if paths:
            return ", ".join(f"`{p}`" for p in paths)
    return "-"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-sast", help="run Semgrep on the current work-unit scope")
    run_parser.add_argument("--repo-root")
    run_parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="explicit file path to scan; bypasses scope filtering but must be under the repo root",
    )
    run_parser.add_argument("--evidence", help="path to plc-evidence.jsonl")
    run_parser.add_argument("--artifact-dir", help="directory for scan artifacts")
    run_parser.add_argument("--semgrep", default=PROJECT_LOCAL_SENTINEL)
    run_parser.add_argument("--config", default="bundled")
    run_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=env_int("PLC_SEMGREP_TIMEOUT_SECONDS", DEFAULT_SEMGREP_TIMEOUT_SECONDS),
        help="maximum seconds to wait for one Semgrep invocation",
    )
    run_parser.add_argument(
        "--offline-rules",
        default=str(Path(__file__).resolve().parents[1] / "assets" / "semgrep-rules.tar.gz"),
    )
    run_parser.add_argument(
        "--fail-on",
        choices=FAIL_ON_CHOICES,
        default="error",
        help="exit 2 when the result is at or above this severity: pass | warn | error (default: error)",
    )
    run_parser.set_defaults(func=run_sast)

    dependency_parser = subparsers.add_parser(
        "run-dependency-vuln",
        help="run dependency vulnerability scanners in changed manifest/lockfile contexts",
    )
    dependency_parser.add_argument("--repo-root")
    dependency_parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="explicit file path to inspect for dependency context; bypasses scope filtering",
    )
    dependency_parser.add_argument("--evidence", help="path to plc-evidence.jsonl")
    dependency_parser.add_argument("--artifact-dir", help="directory for scan artifacts")
    dependency_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=env_int("PLC_DEPENDENCY_TIMEOUT_SECONDS", DEFAULT_SEMGREP_TIMEOUT_SECONDS),
        help="maximum seconds to wait for one dependency scanner or lock check invocation",
    )
    dependency_parser.add_argument(
        "--fail-on",
        choices=FAIL_ON_CHOICES,
        default="error",
        help="exit 2 when the result is at or above this severity: pass | warn | error (default: error)",
    )
    dependency_parser.set_defaults(func=run_dependency_vuln)

    summary_parser = subparsers.add_parser("summary", help="render latest branch evidence as a PR table")
    summary_parser.add_argument("--repo-root")
    summary_parser.add_argument("--evidence", help="path to plc-evidence.jsonl")
    summary_parser.add_argument("--branch", help="branch to summarize; defaults to current branch")
    summary_parser.set_defaults(func=render_summary)

    license_parser = subparsers.add_parser(
        "normalize-licenses",
        help="normalize raw pip-licenses JSON into PLC license evidence",
    )
    license_parser.add_argument("--repo-root")
    license_parser.add_argument("--evidence", help="path to plc-evidence.jsonl")
    license_parser.add_argument(
        "--input",
        required=True,
        help="path to raw pip-licenses --format=json output",
    )
    license_parser.add_argument(
        "--output",
        help="destination for normalized artifact (default: .plc/security/pip-licenses.json)",
    )
    license_parser.add_argument(
        "--fail-on",
        choices=FAIL_ON_CHOICES,
        default="error",
        help="exit 2 when the result is at or above this severity: pass | warn | error (default: error)",
    )
    license_parser.set_defaults(func=run_normalize_licenses)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
