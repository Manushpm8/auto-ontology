"""Shared helpers for Agentic PLC multi-repo workspaces.

The skill scripts are intentionally stdlib-only and executable in isolation.
This module gives them one place to parse `agentic_plc/workspace.json`, validate
manifest-relative paths, sanitize repo URLs, and resolve repo attribution.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

EMBEDDED_CREDS_RE = re.compile(r"^https?://[^/@]+:[^/@]+@")
TOKEN_PARAM_RE = re.compile(
    r"((?:token|key|secret|password|api[-_]?key)=)[^&\s]+",
    re.IGNORECASE,
)

DEFAULT_WORK_PRODUCTS_ROOT = "agentic_plc/work_products"
DEFAULT_MAX_WORKSPACE_REPOS = 100

TRAVERSAL_PLAN_SCHEMA = "workspace-traversal-plan/v1"
TRAVERSAL_SUMMARY_SCHEMA = "workspace-traversal-summary/v1"
MAJOR_PHASES = (
    "workspace_preflight",
    "requirements_authoring",
    "design_authoring",
    "test_plan_authoring",
    "implementation",
    "verification",
    "security_scan",
    "acceptance_validation",
    "traceability_matrix",
    "final_traversal_summary",
)
DEFAULT_SELECTED_PHASES = tuple(phase for phase in MAJOR_PHASES if phase not in {"workspace_preflight", "final_traversal_summary"})


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


def strip_url_credentials(url: str) -> str:
    """Remove embedded basic-auth credentials and blank token-like query values."""
    if EMBEDDED_CREDS_RE.match(url):
        scheme, _, rest = url.partition("://")
        _, _, after_at = rest.partition("@")
        url = f"{scheme}://{after_at}"
    return TOKEN_PARAM_RE.sub(r"\1", url)


def git_remote_url(repo_root: Path) -> str | None:
    url = git_output(repo_root, "config", "--get", "remote.origin.url")
    return strip_url_credentials(url) if url else None


def git_revision(repo_root: Path) -> str | None:
    return git_output(repo_root, "rev-parse", "HEAD")


def git_dirty_state(repo_root: Path) -> str | None:
    status = git_output(repo_root, "status", "--porcelain")
    if status is None:
        return None
    if not status:
        return "clean"
    count = len([line for line in status.splitlines() if line.strip()])
    return f"{count} changed"


def scm_type(repo_root: Path) -> str:
    if git_output(repo_root, "rev-parse", "--is-inside-work-tree") == "true":
        return "git"
    return "none"


def _anchor_dir(anchor: Path) -> Path:
    resolved = anchor.resolve()
    return resolved if resolved.is_dir() else resolved.parent


def validate_manifest_relative_path(value: Any, *, field: str) -> tuple[str | None, str | None]:
    """Validate a manifest path as relative and non-escaping.

    The manifest is JSON and examples use POSIX-style paths. Rejecting Windows
    absolute paths as well as POSIX absolute paths prevents `C:\\...` from being
    treated as a harmless relative string on Unix hosts.
    """
    if not isinstance(value, str):
        return None, f"{field} must be a string"
    path = value.strip()
    if not path:
        return None, f"{field} must be non-empty"
    if "\\" in path:
        return None, f"{field} must use '/' path separators"
    posix = PurePosixPath(path)
    if posix.is_absolute() or PureWindowsPath(path).is_absolute():
        return None, f"{field} must be relative"
    if any(part == ".." for part in posix.parts):
        return None, f"{field} must not contain '..'"
    return posix.as_posix(), None


def resolve_under_root(root: Path, relative_path: str) -> tuple[Path | None, str | None]:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, f"path resolves outside workspace root: {relative_path}"
    return candidate, None


def workspace_repo_limit(max_repos: int | None = None) -> int:
    if max_repos is not None:
        return max(1, max_repos)
    raw = os.environ.get("AGENTIC_PLC_MAX_WORKSPACE_REPOS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return DEFAULT_MAX_WORKSPACE_REPOS
    return DEFAULT_MAX_WORKSPACE_REPOS


def discover_workspace(anchor: Path, *, max_repos: int | None = None) -> dict:
    """Find and parse the nearest parent workspace manifest.

    Returns an empty `workspace_root` when no manifest exists. If a manifest is
    present, `errors` records validation problems so callers can surface them
    instead of silently accepting partial workspace context.
    """
    start = _anchor_dir(anchor)
    for candidate_root in (start, *start.parents):
        manifest_path = candidate_root / "agentic_plc" / "workspace.json"
        if not manifest_path.is_file():
            continue
        errors: list[str] = []
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raw = {}
            errors.append(f"workspace manifest is not valid JSON: {exc}")
        if not isinstance(raw, dict):
            raw = {}
            errors.append("workspace manifest root must be an object")
        repo_limit = workspace_repo_limit(max_repos)
        context = {
            "workspace_root": candidate_root.resolve(),
            "manifest_path": manifest_path.resolve(),
            "manifest": raw,
            "workspace_id": _string_or_none(raw.get("workspace_id")),
            "work_products_root": work_products_root(candidate_root, raw, errors),
            "repos": normalize_repos(candidate_root, raw.get("repos"), errors, max_repos=repo_limit),
            "max_repos": repo_limit,
            "errors": errors,
        }
        return context
    return {
        "workspace_root": None,
        "manifest_path": None,
        "manifest": {},
        "workspace_id": None,
        "work_products_root": None,
        "repos": [],
        "max_repos": workspace_repo_limit(max_repos),
        "errors": [],
    }


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def work_products_root(workspace_root: Path, manifest: dict, errors: list[str]) -> Path:
    raw = manifest.get("work_products_root", DEFAULT_WORK_PRODUCTS_ROOT)
    relative, error = validate_manifest_relative_path(raw, field="work_products_root")
    if error:
        errors.append(error)
        relative = DEFAULT_WORK_PRODUCTS_ROOT
    resolved, resolve_error = resolve_under_root(workspace_root, relative or DEFAULT_WORK_PRODUCTS_ROOT)
    if resolve_error:
        errors.append(resolve_error)
        return (workspace_root / DEFAULT_WORK_PRODUCTS_ROOT).resolve()
    return resolved or (workspace_root / DEFAULT_WORK_PRODUCTS_ROOT).resolve()


def normalize_repos(workspace_root: Path, repos: Any, errors: list[str], *, max_repos: int | None = None) -> list[dict]:
    if repos is None:
        errors.append("repos must be present")
        return []
    if not isinstance(repos, list):
        errors.append("repos must be a list")
        return []
    limit = workspace_repo_limit(max_repos)
    if len(repos) > limit:
        errors.append(f"repos contains {len(repos)} entries; maximum supported for one traversal is {limit}")
        return []

    out: list[dict] = []
    seen_ids: dict[str, int] = {}
    seen_roots: dict[Path, int] = {}
    for index, entry in enumerate(repos):
        field = f"repos[{index}]"
        if not isinstance(entry, dict):
            message = f"{field} must be an object"
            errors.append(message)
            out.append({"index": index, "status": "invalid", "errors": [message]})
            continue

        repo_errors: list[str] = []
        relative, path_error = validate_manifest_relative_path(entry.get("path"), field=f"{field}.path")
        if path_error:
            repo_errors.append(path_error)

        root_path: Path | None = None
        if relative:
            root_path, resolve_error = resolve_under_root(workspace_root, relative)
            if resolve_error:
                repo_errors.append(f"{field}.path {resolve_error}")
            elif root_path is not None:
                root_key = root_path.resolve()
                if root_key in seen_roots:
                    repo_errors.append(f"{field}.path duplicates repos[{seen_roots[root_key]}].path")
                else:
                    seen_roots[root_key] = index

        repo_id = _string_or_none(entry.get("repo_id"))
        if repo_id:
            if repo_id in seen_ids:
                repo_errors.append(f"{field}.repo_id duplicates repos[{seen_ids[repo_id]}].repo_id")
            else:
                seen_ids[repo_id] = index

        repo_url = _string_or_none(entry.get("url"))
        if repo_url:
            repo_url = strip_url_credentials(repo_url)

        if repo_errors:
            status = "invalid"
        elif root_path is None:
            status = "invalid"
        elif not root_path.exists():
            status = "missing"
        elif not root_path.is_dir():
            status = "inaccessible"
        else:
            status = "accessible"

        revision = git_revision(root_path) if root_path is not None and root_path.is_dir() else None
        normalized = {
            "index": index,
            "repo_id": repo_id,
            "path": relative,
            "url": repo_url,
            "root": root_path,
            "status": status,
            "revision": revision,
            "errors": repo_errors,
        }
        out.append(normalized)
        errors.extend(repo_errors)
    return out


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def workspace_relative(context: dict, path: Path | None) -> str | None:
    if path is None:
        return None
    workspace_root = context.get("workspace_root")
    if isinstance(workspace_root, Path):
        try:
            return path.resolve().relative_to(workspace_root.resolve()).as_posix()
        except ValueError:
            return path.name
    return path.as_posix()


def _json_load(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid: {exc}"
    if not isinstance(data, dict):
        return None, "invalid: profile root must be an object"
    return data, None


def _profile_summary(context: dict, path: Path) -> tuple[dict, dict | None]:
    data, error = _json_load(path)
    summary = {"path": workspace_relative(context, path), "status": "current"}
    if error:
        summary["status"] = "missing" if error == "missing" else "invalid"
        if error != "missing":
            summary["reason"] = error
        return summary, None
    profile_type = _string_or_none(data.get("profile_type")) if data else None
    if profile_type:
        summary["profile_type"] = profile_type
    schema = _string_or_none(data.get("schema_version")) if data else None
    if schema:
        summary["schema_version"] = schema
    return summary, data


def _workspace_profile_warnings(context: dict, workspace_profile: dict | None) -> list[str]:
    if not workspace_profile or workspace_profile.get("profile_type") != "workspace":
        return []
    profile_repos = workspace_profile.get("repos")
    if not isinstance(profile_repos, list):
        return []
    manifest_by_id = {
        repo.get("repo_id"): repo
        for repo in context.get("repos", [])
        if isinstance(repo.get("repo_id"), str)
    }
    manifest_paths = {
        repo.get("path")
        for repo in context.get("repos", [])
        if isinstance(repo.get("path"), str)
    }
    warnings: list[str] = []
    for index, profile_repo in enumerate(profile_repos):
        if not isinstance(profile_repo, dict):
            continue
        repo_id = _string_or_none(profile_repo.get("repo_id"))
        path = _string_or_none(profile_repo.get("path"))
        if repo_id and repo_id in manifest_by_id:
            manifest_repo = manifest_by_id[repo_id]
            if path and path != manifest_repo.get("path"):
                warnings.append(
                    f"profile repos[{index}] path {path!r} disagrees with workspace.json path {manifest_repo.get('path')!r} for repo_id {repo_id!r}"
                )
            profile_url = _string_or_none(profile_repo.get("url"))
            manifest_url = manifest_repo.get("url")
            if profile_url and manifest_url and strip_url_credentials(profile_url) != manifest_url:
                warnings.append(
                    f"profile repos[{index}] url disagrees with workspace.json url for repo_id {repo_id!r}"
                )
        elif path and path not in manifest_paths:
            warnings.append(f"profile repos[{index}] path {path!r} is not listed in workspace.json")
        elif repo_id:
            warnings.append(f"profile repos[{index}] repo_id {repo_id!r} is not listed in workspace.json")
    return warnings


def collect_profile_context(context: dict) -> tuple[list[dict], dict[str, dict], list[str]]:
    workspace_root = context.get("workspace_root")
    if not isinstance(workspace_root, Path):
        return [], {}, []

    profiles: list[dict] = []
    repo_profiles: dict[str, dict] = {}
    warnings: list[str] = []

    workspace_profile_summary, workspace_profile = _profile_summary(context, workspace_root / ".plc" / "profile.json")
    profiles.append(workspace_profile_summary)
    warnings.extend(_workspace_profile_warnings(context, workspace_profile))

    for repo in context.get("repos", []):
        root = repo.get("root")
        if not isinstance(root, Path):
            continue
        summary, _ = _profile_summary(context, root / ".plc" / "profile.json")
        path = repo.get("path")
        if isinstance(path, str):
            repo_profiles[path] = summary
        profiles.append(summary)
    return profiles, repo_profiles, warnings


def traversal_plan_path(feature_dir: Path) -> Path:
    return feature_dir / "traversal-plan.json"


def traversal_summary_json_path(feature_dir: Path) -> Path:
    return feature_dir / "traversal-summary.json"


def traversal_summary_markdown_path(feature_dir: Path) -> Path:
    return feature_dir / "traversal-summary.md"


def _plan_id(generated_at: str, feature: str) -> str:
    safe_time = re.sub(r"[^0-9A-Za-z]+", "-", generated_at).strip("-")
    safe_feature = re.sub(r"[^0-9A-Za-z]+", "-", feature).strip("-") or "feature"
    return f"{safe_time}-{safe_feature}"


def _repo_skip_reason(repo: dict) -> str | None:
    status = repo.get("status")
    if status == "accessible":
        return None
    if status == "missing":
        return "Repo path missing under workspace root"
    if status == "inaccessible":
        return "Repo path is not an inspectable directory"
    errors = repo.get("errors") or []
    if errors:
        return "; ".join(str(error) for error in errors)
    return "Manifest repo entry is invalid"


def build_traversal_plan(
    context: dict,
    feature_dir: Path,
    *,
    generated_at: str | None = None,
    plan_id: str | None = None,
    plan_version: int = 1,
    refresh_reason: str = "initial workspace preflight",
) -> dict:
    workspace_root = context.get("workspace_root")
    manifest_path = context.get("manifest_path")
    work_products_root = context.get("work_products_root")
    generated_at = generated_at or utc_now()
    feature = feature_dir.name
    profiles, repo_profiles, profile_warnings = collect_profile_context(context)
    workspace = {
        "workspace_id": context.get("workspace_id"),
        "manifest": workspace_relative(context, manifest_path if isinstance(manifest_path, Path) else None),
        "feature": feature,
        "work_products_dir": workspace_relative(context, feature_dir),
    }
    repos_out: list[dict] = []
    for repo in context.get("repos", []):
        root = repo.get("root")
        profile = repo_profiles.get(repo.get("path")) if isinstance(repo.get("path"), str) else None
        status = repo.get("status")
        selected_phases = ["workspace_preflight"] if status == "accessible" else []
        candidate_phases = list(DEFAULT_SELECTED_PHASES) if status == "accessible" else []
        selection_reasons = ["manifest repo included in workspace preflight"] if status == "accessible" else []
        if profile and profile.get("status") == "current":
            selection_reasons.append("brownfield profile available")
        repo_out = {
            "index": repo.get("index"),
            "repo_id": repo.get("repo_id"),
            "path": repo.get("path"),
            "sanitized_repo_url": repo.get("url"),
            "status": status,
            "scm": scm_type(root) if isinstance(root, Path) and root.is_dir() else None,
            "revision": repo.get("revision"),
            "dirty_state": git_dirty_state(root) if isinstance(root, Path) and root.is_dir() else None,
            "profile": profile or {"path": None, "status": "missing"},
            "selected_phases": selected_phases,
            "candidate_phases": candidate_phases,
            "phase_selection_status": "pending_feature_relevance" if status == "accessible" else "not_applicable",
            "selection_reasons": selection_reasons,
            "skip_reason": _repo_skip_reason(repo),
            "errors": list(repo.get("errors") or []),
        }
        repos_out.append({key: value for key, value in repo_out.items() if value not in (None, [], {})})
    return {
        "schema_version": TRAVERSAL_PLAN_SCHEMA,
        "plan_id": plan_id or _plan_id(generated_at, feature),
        "plan_version": plan_version,
        "generated_at": generated_at,
        "refresh_reason": refresh_reason,
        "major_phases": list(MAJOR_PHASES),
        "workspace": {key: value for key, value in workspace.items() if value is not None},
        "guardrails": {
            "max_repos": context.get("max_repos", DEFAULT_MAX_WORKSPACE_REPOS),
            "duplicate_resolved_paths": "reject",
            "reenter_same_repo_phase_action": "forbid_per_plan_version",
            "repo_set_source": "agentic_plc/workspace.json",
        },
        "profiles_consulted": profiles,
        "profile_warnings": profile_warnings,
        "repos": repos_out,
        "work_products_root": workspace_relative(context, work_products_root if isinstance(work_products_root, Path) else None),
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json_object(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _coerce_plan_version(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _plan_context_signature(plan: dict) -> dict:
    """Return the plan fields that should trigger a snapshot refresh."""
    repo_fields = (
        "index",
        "repo_id",
        "path",
        "sanitized_repo_url",
        "status",
        "scm",
        "revision",
        "profile",
        "selected_phases",
        "candidate_phases",
        "phase_selection_status",
        "selection_reasons",
        "skip_reason",
        "errors",
    )
    repos: list[dict] = []
    for repo in plan.get("repos", []):
        if not isinstance(repo, dict):
            continue
        repos.append({field: repo.get(field) for field in repo_fields if field in repo})
    return {
        "workspace": plan.get("workspace") or {},
        "guardrails": plan.get("guardrails") or {},
        "profiles_consulted": plan.get("profiles_consulted") or [],
        "profile_warnings": plan.get("profile_warnings") or [],
        "major_phases": plan.get("major_phases") or [],
        "repos": repos,
        "work_products_root": plan.get("work_products_root"),
    }


def ensure_traversal_plan(context: dict, feature_dir: Path) -> dict | None:
    if not isinstance(context.get("workspace_root"), Path):
        return None
    path = traversal_plan_path(feature_dir)
    existing = load_json_object(path)
    if existing and existing.get("schema_version") == TRAVERSAL_PLAN_SCHEMA:
        current = build_traversal_plan(
            context,
            feature_dir,
            generated_at=_string_or_none(existing.get("generated_at")) or utc_now(),
            plan_id=_string_or_none(existing.get("plan_id")),
            plan_version=_coerce_plan_version(existing.get("plan_version"), 1),
            refresh_reason=_string_or_none(existing.get("refresh_reason")) or "initial workspace preflight",
        )
        if _plan_context_signature(existing) == _plan_context_signature(current):
            return existing
        plan = build_traversal_plan(
            context,
            feature_dir,
            plan_id=_string_or_none(existing.get("plan_id")),
            plan_version=_coerce_plan_version(existing.get("plan_version"), 0) + 1,
            refresh_reason="workspace manifest, repo availability, repo revision, or profile metadata changed",
        )
        atomic_write_json(path, plan)
        return plan
    plan = build_traversal_plan(context, feature_dir)
    atomic_write_json(path, plan)
    return plan


def refresh_traversal_plan(context: dict, feature_dir: Path, refresh_reason: str) -> dict | None:
    if not isinstance(context.get("workspace_root"), Path):
        return None
    path = traversal_plan_path(feature_dir)
    existing = load_json_object(path) or {}
    plan_version = _coerce_plan_version(existing.get("plan_version"), 0) + 1
    plan_id = _string_or_none(existing.get("plan_id"))
    plan = build_traversal_plan(
        context,
        feature_dir,
        plan_id=plan_id,
        plan_version=plan_version,
        refresh_reason=refresh_reason,
    )
    atomic_write_json(path, plan)
    return plan


def _preflight_status(repo: dict) -> str:
    status = repo.get("status")
    if status == "accessible":
        return "PASS"
    if status == "missing":
        return "SKIP"
    if status == "invalid":
        return "ERROR"
    return "WARN"


def _preflight_reason(repo: dict) -> str:
    status = repo.get("status")
    if status == "accessible":
        return "Repo path resolved under workspace root"
    return str(repo.get("skip_reason") or "Repo could not be inspected")


def _summary_from_plan(plan: dict) -> dict:
    repos = []
    for repo in plan.get("repos", []):
        outcome = {
            "phase": "workspace_preflight",
            "action": "resolve_repo",
            "status": _preflight_status(repo),
            "reason": _preflight_reason(repo),
        }
        repos.append(
            {
                "repo_id": repo.get("repo_id"),
                "path": repo.get("path"),
                "sanitized_repo_url": repo.get("sanitized_repo_url"),
                "status": repo.get("status"),
                "scm": repo.get("scm"),
                "revision": repo.get("revision"),
                "dirty_state": repo.get("dirty_state"),
                "phase_outcomes": [outcome],
            }
        )
    return {
        "schema_version": TRAVERSAL_SUMMARY_SCHEMA,
        "generated_at": utc_now(),
        "major_phases": list(plan.get("major_phases") or MAJOR_PHASES),
        "workspace": plan.get("workspace", {}),
        "traversal_plan": {
            "plan_id": plan.get("plan_id"),
            "plan_version": plan.get("plan_version"),
            "path": "traversal-plan.json",
        },
        "repos": repos,
        "workspace_outcomes": [_final_traversal_summary_outcome()],
        "profile_warnings": list(plan.get("profile_warnings") or []),
        "advisory": (
            "This traversal summary is an advisory audit surface for agent activity and repo accessibility. "
            "It does not replace authoritative PLC release gates or team approval."
        ),
    }


def _final_traversal_summary_outcome() -> dict:
    return {
        "phase": "final_traversal_summary",
        "action": "write_summary",
        "status": "PASS",
        "reason": "Traversal summary artifact generated for reviewer audit",
        "artifact": "traversal-summary.md",
    }


def _summary_repo_signature(summary: dict) -> list[dict]:
    fields = ("repo_id", "path", "sanitized_repo_url", "status", "scm", "revision", "dirty_state")
    repos = []
    for repo in summary.get("repos", []):
        if isinstance(repo, dict):
            repos.append({field: repo.get(field) for field in fields if field in repo})
    return repos


def _summary_matches_plan(summary: dict, plan: dict) -> bool:
    expected = _summary_from_plan(plan)
    return (
        summary.get("schema_version") == TRAVERSAL_SUMMARY_SCHEMA
        and summary.get("traversal_plan", {}).get("plan_id") == plan.get("plan_id")
        and summary.get("traversal_plan", {}).get("plan_version") == plan.get("plan_version")
        and summary.get("major_phases") == expected.get("major_phases")
        and _summary_repo_signature(summary) == _summary_repo_signature(expected)
    )


def _ensure_final_summary_outcome(summary: dict) -> bool:
    outcomes = summary.setdefault("workspace_outcomes", [])
    key = _outcome_key(_final_traversal_summary_outcome())
    if key in {_outcome_key(outcome) for outcome in outcomes if isinstance(outcome, dict)}:
        return False
    outcomes.append(_final_traversal_summary_outcome())
    return True


def ensure_traversal_summary(context: dict, feature_dir: Path, plan: dict | None = None) -> dict | None:
    if not isinstance(context.get("workspace_root"), Path):
        return None
    plan = plan or ensure_traversal_plan(context, feature_dir)
    if not plan:
        return None
    path = traversal_summary_json_path(feature_dir)
    existing = load_json_object(path)
    if existing and _summary_matches_plan(existing, plan):
        changed = False
        if existing.get("major_phases") != list(plan.get("major_phases") or MAJOR_PHASES):
            existing["major_phases"] = list(plan.get("major_phases") or MAJOR_PHASES)
            changed = True
        changed = _ensure_final_summary_outcome(existing) or changed
        if changed:
            write_traversal_summary(feature_dir, existing)
        return existing
    summary = _summary_from_plan(plan)
    write_traversal_summary(feature_dir, summary)
    return summary


def _status_from_record(record: dict) -> str:
    value = str(record.get("result") or record.get("status") or record.get("decision") or "").lower()
    if value in {"pass", "green", "clean", "accepted"}:
        return "PASS"
    if value in {"fail", "red", "critical", "error", "rejected"}:
        return "FAIL"
    if value in {"skip", "aborted", "blocked", "n/a"}:
        return "SKIP"
    return "WARN"


def _repo_key(repo: dict) -> str:
    return str(repo.get("repo_id") or repo.get("path") or repo.get("sanitized_repo_url") or repo.get("index") or "")


def _outcome_key(outcome: dict) -> tuple[str, str]:
    return (str(outcome.get("phase") or ""), str(outcome.get("action") or ""))


def _find_summary_repo(summary: dict, *, repo_id: str | None = None, repo_url: str | None = None, repo_path: str | None = None) -> dict | None:
    repo_url = strip_url_credentials(repo_url.strip()) if repo_url else None
    for repo in summary.get("repos", []):
        if repo_id and repo_id == repo.get("repo_id"):
            return repo
        if repo_path and repo_path == repo.get("path"):
            return repo
        if repo_url and repo_url == repo.get("sanitized_repo_url"):
            return repo
    return None


def _artifact_relative(feature_dir: Path, artifact: str | Path | None) -> str | None:
    if artifact is None:
        return None
    artifact_path = Path(artifact)
    if not artifact_path.is_absolute():
        return artifact_path.as_posix()
    try:
        return artifact_path.resolve().relative_to(feature_dir.resolve()).as_posix()
    except ValueError:
        return artifact_path.name


def record_phase_outcome(
    context: dict,
    feature_dir: Path,
    *,
    phase: str,
    action: str,
    status: str,
    reason: str,
    repo_root: Path | None = None,
    repo_id: str | None = None,
    repo_url: str | None = None,
    artifact: str | Path | None = None,
    invocation_type: str = "cli",
    working_dir: Path | None = None,
) -> None:
    if phase not in MAJOR_PHASES:
        raise ValueError(f"unknown traversal phase: {phase}")
    if status not in {"PASS", "WARN", "SKIP", "FAIL", "ERROR"}:
        raise ValueError(f"unknown traversal status: {status}")
    plan = ensure_traversal_plan(context, feature_dir)
    summary = ensure_traversal_summary(context, feature_dir, plan)
    if not plan or not summary:
        return

    repo_entry = matching_repo(context, repo_root) if repo_root is not None else {}
    target = _find_summary_repo(
        summary,
        repo_id=repo_id or repo_entry.get("repo_id"),
        repo_url=repo_url or repo_entry.get("url"),
        repo_path=repo_entry.get("path"),
    )
    outcome = {
        "phase": phase,
        "action": action,
        "status": status,
        "reason": reason,
        "invocation_type": invocation_type,
    }
    artifact_value = _artifact_relative(feature_dir, artifact)
    if artifact_value:
        outcome["artifact"] = artifact_value
    if working_dir is not None:
        outcome["working_dir"] = workspace_relative(context, working_dir)

    if target is None:
        existing = {_outcome_key(item) for item in summary.setdefault("workspace_outcomes", [])}
        if _outcome_key(outcome) not in existing:
            summary["workspace_outcomes"].append(outcome)
    else:
        outcomes = target.setdefault("phase_outcomes", [])
        existing = {_outcome_key(item) for item in outcomes}
        if _outcome_key(outcome) not in existing:
            outcomes.append(outcome)
    summary["generated_at"] = utc_now()
    write_traversal_summary(feature_dir, summary)


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip() or "-"


def render_traversal_summary_markdown(summary: dict) -> str:
    lines = ["# Workspace Traversal Summary", ""]
    workspace = summary.get("workspace", {})
    if workspace:
        lines.extend(
            [
                f"- Workspace ID: {_markdown_cell(workspace.get('workspace_id'))}",
                f"- Manifest: {_markdown_cell(workspace.get('manifest'))}",
                f"- Feature: {_markdown_cell(workspace.get('feature'))}",
                f"- Work products: {_markdown_cell(workspace.get('work_products_dir'))}",
                "",
            ]
        )
    plan = summary.get("traversal_plan", {})
    lines.extend(
        [
            f"- Traversal plan: {_markdown_cell(plan.get('path'))}",
            f"- Plan ID: {_markdown_cell(plan.get('plan_id'))}",
            f"- Plan version: {_markdown_cell(plan.get('plan_version'))}",
            "",
            "| Repo | Manifest path | Status | Revision | Phase | Action | Outcome | Reason | Artifact |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for repo in summary.get("repos", []):
        outcomes = repo.get("phase_outcomes") or []
        for outcome in outcomes:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(repo.get("repo_id")),
                        _markdown_cell(repo.get("path")),
                        _markdown_cell(repo.get("status")),
                        _markdown_cell(repo.get("revision")),
                        _markdown_cell(outcome.get("phase")),
                        _markdown_cell(outcome.get("action")),
                        _markdown_cell(outcome.get("status")),
                        _markdown_cell(outcome.get("reason")),
                        _markdown_cell(outcome.get("artifact")),
                    ]
                )
                + " |"
            )
    for outcome in summary.get("workspace_outcomes", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    "(workspace)",
                    "-",
                    "-",
                    "-",
                    _markdown_cell(outcome.get("phase")),
                    _markdown_cell(outcome.get("action")),
                    _markdown_cell(outcome.get("status")),
                    _markdown_cell(outcome.get("reason")),
                    _markdown_cell(outcome.get("artifact")),
                ]
            )
            + " |"
        )
    profile_warnings = summary.get("profile_warnings") or []
    if profile_warnings:
        lines.extend(["", "## Profile Warnings", ""])
        lines.extend(f"- {_markdown_cell(warning)}" for warning in profile_warnings)
    lines.extend(["", "## Advisory Note", "", str(summary.get("advisory") or "")])
    return "\n".join(lines).rstrip() + "\n"


def write_traversal_summary(feature_dir: Path, summary: dict) -> None:
    atomic_write_json(traversal_summary_json_path(feature_dir), summary)
    traversal_summary_markdown_path(feature_dir).write_text(
        render_traversal_summary_markdown(summary),
        encoding="utf-8",
    )


def feature_dir_for_evidence_path(context: dict, evidence_path: Path) -> Path | None:
    workspace_root = context.get("workspace_root")
    work_products_root = context.get("work_products_root")
    if not isinstance(workspace_root, Path) or not isinstance(work_products_root, Path):
        return None
    if evidence_path.name != "evidence.jsonl":
        return None
    try:
        evidence_path.resolve().relative_to(work_products_root.resolve())
    except ValueError:
        return None
    if evidence_path.parent == work_products_root:
        return None
    return evidence_path.parent


def record_evidence_phase_outcome(evidence_path: Path, record: dict) -> None:
    context = discover_workspace(evidence_path)
    if context.get("errors"):
        return
    feature_dir = feature_dir_for_evidence_path(context, evidence_path.resolve())
    if feature_dir is None:
        return
    check = str(record.get("check") or "evidence")
    ref = str(record.get("ref") or record.get("finding_id") or "unscoped")
    pillar = str(record.get("pillar") or "").upper()
    phase = "security_scan" if pillar == "CODING" else "verification"
    status = _status_from_record(record)
    result_value = record.get("result") or record.get("status") or record.get("decision") or "recorded"
    reason = f"Recorded {check} evidence for {ref} with outcome {result_value}"
    artifact = record.get("artifact") or evidence_path
    record_phase_outcome(
        context,
        feature_dir,
        phase=phase,
        action=f"record_{check}_{ref}",
        status=status,
        reason=reason,
        repo_id=_string_or_none(record.get("repo_id")),
        repo_url=_string_or_none(record.get("repo_url")),
        artifact=artifact,
        invocation_type="evidence",
    )


def matching_repo(context: dict, repo_root: Path) -> dict:
    resolved_repo = repo_root.resolve()
    for entry in context.get("repos", []):
        root = entry.get("root")
        if not isinstance(root, Path) or entry.get("status") == "invalid":
            continue
        try:
            resolved_repo.relative_to(root)
        except ValueError:
            continue
        return entry
    return {}


def repo_by_id_or_url(context: dict, repo: str | None = None, repo_url: str | None = None) -> dict:
    repo = (repo or "").strip()
    repo = strip_url_credentials(repo) if repo.startswith(("http://", "https://")) else repo
    repo_url = strip_url_credentials(repo_url.strip()) if repo_url else ""
    for entry in context.get("repos", []):
        if repo and repo == entry.get("repo_id"):
            return entry
        if repo and repo == entry.get("path"):
            return entry
        if repo and repo == entry.get("url"):
            return entry
        if repo_url and repo_url == entry.get("url"):
            return entry
    return {}


def stable_workspace_root(value: str | None, discovered: bool) -> str | None:
    if value:
        return value.strip()
    if discovered:
        return "."
    return None


def workspace_work_products_dir(anchor: Path) -> Path | None:
    context = discover_workspace(anchor)
    root = context.get("work_products_root")
    return root if isinstance(root, Path) else None


def env_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
