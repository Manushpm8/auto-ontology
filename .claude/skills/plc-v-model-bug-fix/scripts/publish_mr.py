#!/usr/bin/env python3
"""Three-tier MR-publish helper for the plc-v-model-bug-fix skill.

Stdlib-only. Auto-detects the remote provider from `git remote get-url origin` and
opens an MR via the most ergonomic path that is actually available:

  Tier 1 — CLI:  if `gh` / `glab` is on PATH, shell out to it. Best UX —
           agent does everything, zero clicks. Auth handled by the CLI.
  Tier 2 — REST API + PAT:  if Tier 1 misses but a GITLAB_TOKEN /
           GITHUB_TOKEN env var is set (auto-loaded from an untracked
           `.env` in repo root, with `.env` silently added to `.gitignore`),
           POST to the provider's API. Zero
           clicks, no CLI install needed — works on managed machines.
  Tier 3 — URL handoff:  if neither Tier 1 nor Tier 2 fires, print the
           "Create new MR" URL with title, branches, draft flag, and
           (when it fits) description URL-encoded. The user clicks the
           URL, the form opens pre-filled, they click Create. Works on
           every OS / WSL identically. One human click.

Direct MCP write tools don't exist for GitLab yet — when they do, add a
Tier-0 check above the CLI tier.

Usage:
    publish_mr.py \\
        --title "Fix bug 5234567: RTSP IPv6 crash on RTX 6000" \\
        --description-file .plc/briefs/bug-5234567-mr-description.md \\
        --branch bug/5234567 \\
        [--base main] \\
        [--draft] \\
        [--dry-run]

Exit codes:
    0  - MR opened via CLI or API (URL printed) OR URL handoff emitted
         (branch pushed; user clicks the printed URL to finish)
    1  - remote provider unsupported (Gerrit) or origin remote missing
         OR API call failed (auth, scope, network — error to stderr)
    2  - argument or git error
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_HOST_HINTS = ("github.com", "github.")
GITLAB_HOST_HINTS = ("gitlab.com", "gitlab-master.", "gitlab.")
GERRIT_HOST_HINTS = ("gerrit.", "review.")


def git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo_root), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def detect_repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd().resolve()
    try:
        output = git_output(cwd, "rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ERROR: not a git repo at {cwd}: {exc.stderr.strip()}")
    return Path(output).resolve()


def detect_provider(repo_root: Path) -> str:
    """Return one of: github, gitlab, gerrit, unknown for the origin remote."""
    try:
        remote = git_output(repo_root, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return "unknown"
    remote_lower = remote.lower()
    if any(hint in remote_lower for hint in GITHUB_HOST_HINTS):
        return "github"
    if any(hint in remote_lower for hint in GITLAB_HOST_HINTS):
        return "gitlab"
    if any(hint in remote_lower for hint in GERRIT_HOST_HINTS):
        return "gerrit"
    return "unknown"


def detect_base_branch(repo_root: Path) -> str:
    """Best-effort default base branch — refs/remotes/origin/HEAD when available."""
    try:
        # symbolic-ref returns "refs/remotes/origin/main" or similar.
        symref = git_output(repo_root, "symbolic-ref", "refs/remotes/origin/HEAD")
        return symref.rsplit("/", 1)[-1]
    except subprocess.CalledProcessError:
        pass
    # Fallback: main, then master.
    for candidate in ("main", "master"):
        try:
            git_output(repo_root, "rev-parse", "--verify", candidate)
            return candidate
        except subprocess.CalledProcessError:
            continue
    return "main"


def run_gh(title: str, body: str, branch: str, base: str, draft: bool,
           repo_root: Path, dry_run: bool) -> int:
    cmd = [
        "gh", "pr", "create",
        "--title", title,
        "--body", body,
        "--base", base,
        "--head", branch,
    ]
    if draft:
        cmd.append("--draft")
    if dry_run:
        return _run_publish(cmd, repo_root, dry_run)
    if shutil.which("gh") is not None:
        return _run_publish(cmd, repo_root, dry_run)
    # Tier 2 — REST API via GITHUB_TOKEN env var.
    _autoload_env_from_dotenv(repo_root)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return _create_github_pr_via_api(title, body, branch, base, draft, repo_root, token)
    # Tier 3 — URL handoff.
    return _emit_url_handoff(title, body, branch, base, draft, repo_root, "github")


def run_glab(title: str, body: str, branch: str, base: str, draft: bool,
             repo_root: Path, dry_run: bool) -> int:
    cmd = [
        "glab", "mr", "create",
        "--title", title,
        "--description", body,
        "--target-branch", base,
        "--source-branch", branch,
    ]
    if draft:
        cmd.append("--draft")
    if dry_run:
        return _run_publish(cmd, repo_root, dry_run)
    if shutil.which("glab") is not None:
        return _run_publish(cmd, repo_root, dry_run)
    # Tier 2 — REST API via GITLAB_TOKEN env var.
    _autoload_env_from_dotenv(repo_root)
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        return _create_gitlab_mr_via_api(title, body, branch, base, draft, repo_root, token)
    # Tier 3 — URL handoff.
    return _emit_url_handoff(title, body, branch, base, draft, repo_root, "gitlab")


# ----------------------------------------------------------------------
# Tier 2 — REST API via PAT env var.
#
# Loads an untracked .env from repo root if present (silently adding it to
# .gitignore), reads GITLAB_TOKEN / GITHUB_TOKEN,
# and POSTs to the provider's API. Zero clicks, no CLI install, works on
# managed machines.
# ----------------------------------------------------------------------


def _autoload_env_from_dotenv(repo_root: Path) -> None:
    """Load `.env` (if present) into os.environ and ensure it's gitignored.

    Existing os.environ values take precedence — we never overwrite a
    variable the user has already exported in the shell. The .gitignore
    update is silent: no prompt, no message. The user-visible side
    effect is one extra line in .gitignore on first run.

    .env parser is intentionally minimal: KEY=VALUE per line, # for
    comments, blank lines skipped, outer single/double quotes stripped.
    No shell expansion, no multi-line values.
    """
    dotenv = repo_root / ".env"
    if not dotenv.is_file():
        return
    if _dotenv_is_tracked(repo_root):
        print(
            "ERROR: .env is already tracked or staged; refusing to load tokens from it. "
            "Remove it from git tracking before using .env-based MR publishing.",
            file=sys.stderr,
        )
        return
    try:
        text = dotenv.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
    _silent_ensure_env_in_gitignore(repo_root)


def _dotenv_is_tracked(repo_root: Path) -> bool:
    result = subprocess.run(
        ("git", "-C", str(repo_root), "ls-files", "--error-unmatch", ".env"),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _silent_ensure_env_in_gitignore(repo_root: Path) -> None:
    """Append `.env` to .gitignore if not already there. Silent. Idempotent."""
    gitignore = repo_root / ".gitignore"
    pattern = ".env"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    except OSError:
        return
    for line in existing.splitlines():
        if line.strip() == pattern:
            return
    try:
        with gitignore.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"{pattern}\n")
    except OSError:
        pass  # silent — gitignore enforcement is best-effort


def _gitlab_project_path(web_url: str) -> tuple[str, str] | None:
    """Split `https://host/group/project` into (host, group/project)."""
    if not web_url.startswith(("http://", "https://")):
        return None
    rest = web_url.split("://", 1)[1]
    host, sep, path = rest.partition("/")
    if not sep or not path:
        return None
    return host, path.rstrip("/")


def _create_gitlab_mr_via_api(title: str, body: str, branch: str, base: str,
                               draft: bool, repo_root: Path, token: str) -> int:
    web_url = _origin_web_url(repo_root)
    if not web_url:
        print("ERROR: cannot derive web URL from `git remote get-url origin`.", file=sys.stderr)
        return 1
    parts = _gitlab_project_path(web_url)
    if not parts:
        print(f"ERROR: cannot parse GitLab project path from {web_url}", file=sys.stderr)
        return 1
    host, project_path = parts
    api_url = (
        f"https://{host}/api/v4/projects/"
        f"{urllib.parse.quote(project_path, safe='')}/merge_requests"
    )
    payload = {
        "source_branch": branch,
        "target_branch": base,
        "title": title,
        "description": body,
    }
    if draft:
        # GitLab's API treats draft via a title prefix or the `draft` field;
        # `draft: true` is supported in v15+, which gitlab-master runs.
        payload["draft"] = True
    return _post_json(
        api_url=api_url,
        payload=payload,
        headers={"Private-Token": token, "Content-Type": "application/json"},
        provider_label="GitLab",
        host=host,
        unauthorized_remediation=(
            f"Generate a new token with scopes 'api' (or 'write_repository') at:\n"
            f"  https://{host}/-/user_settings/personal_access_tokens"
        ),
        mr_url_field="web_url",
    )


def _create_github_pr_via_api(title: str, body: str, branch: str, base: str,
                               draft: bool, repo_root: Path, token: str) -> int:
    web_url = _origin_web_url(repo_root)
    if not web_url:
        print("ERROR: cannot derive web URL from `git remote get-url origin`.", file=sys.stderr)
        return 1
    parts = _gitlab_project_path(web_url)  # same shape: (host, owner/repo)
    if not parts:
        print(f"ERROR: cannot parse GitHub repo path from {web_url}", file=sys.stderr)
        return 1
    host, repo_path = parts
    # GitHub's API host is api.github.com for public; for Enterprise, it's
    # <host>/api/v3. Default to public for github.com hosts.
    api_host = "api.github.com" if host == "github.com" else f"{host}/api/v3"
    api_url = f"https://{api_host}/repos/{repo_path}/pulls"
    payload = {
        "title": title,
        "head": branch,
        "base": base,
        "body": body,
        "draft": draft,
    }
    return _post_json(
        api_url=api_url,
        payload=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        provider_label="GitHub",
        host=host,
        unauthorized_remediation=(
            "Generate a fine-grained PAT with 'Pull requests: write' permission at:\n"
            f"  https://{host}/settings/tokens"
        ),
        mr_url_field="html_url",
    )


def _post_json(api_url: str, payload: dict, headers: dict,
               provider_label: str, host: str,
               unauthorized_remediation: str, mr_url_field: str) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = json.loads(resp.read().decode("utf-8"))
            mr_url = response.get(mr_url_field, "")
            print(f"{provider_label} MR opened: {mr_url}")
            return 0
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
        except OSError:
            error_body = ""
        if exc.code == 401:
            print(f"ERROR: {provider_label} token is invalid or expired.", file=sys.stderr)
            print(unauthorized_remediation, file=sys.stderr)
        elif exc.code == 403:
            print(
                f"ERROR: {provider_label} token lacks required scopes for MR creation.",
                file=sys.stderr,
            )
            print(unauthorized_remediation, file=sys.stderr)
        elif exc.code == 409:
            print(
                f"ERROR: {provider_label} reports the MR already exists for this branch.",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: {provider_label} API returned HTTP {exc.code}.",
                file=sys.stderr,
            )
            if error_body:
                print(error_body[:400], file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"ERROR: cannot reach {provider_label} API at {host}: {exc}", file=sys.stderr)
        return 1


# ----------------------------------------------------------------------
# Tier 2 — URL handoff.
#
# When neither `gh` nor `glab` is on PATH, fall back to emitting the
# provider's "Create new MR" web URL with as many fields pre-filled as
# possible. The user clicks the URL, the form opens populated, they click
# Create. Works on every OS. No install, no PAT.
# ----------------------------------------------------------------------

# URL-length budget: most browsers handle ~8000 chars comfortably; we cap
# embedded description at this many raw chars so the encoded URL stays
# well under that. For longer descriptions we save them to a file and
# point the user at it.
_URL_DESCRIPTION_BUDGET_CHARS = 1500


def _git_url_to_web(url: str) -> str | None:
    """Convert a git remote URL to its https web URL.

    Handles three common shapes:
      - `https://host/path[.git]`         → `https://host/path`
      - `git@host:path[.git]`             → `https://host/path`
      - `ssh://[git@]host[:port]/path`    → `https://host/path`
    """
    if not url:
        return None
    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("git@"):
        rest = url[len("git@"):]
        host, _, path = rest.partition(":")
        if not host or not path:
            return None
        return f"https://{host}/{path}"
    if url.startswith("ssh://"):
        rest = url[len("ssh://"):]
        if rest.startswith("git@"):
            rest = rest[len("git@"):]
        host_part, _, path = rest.partition("/")
        if not host_part or not path:
            return None
        host = host_part.split(":")[0]  # drop :port
        return f"https://{host}/{path}"
    return None


def _origin_web_url(repo_root: Path) -> str | None:
    try:
        remote = git_output(repo_root, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return None
    return _git_url_to_web(remote)


def _build_gitlab_new_mr_url(web_url: str, branch: str, base: str,
                              title: str, description: str | None,
                              draft: bool) -> str:
    params = [
        ("merge_request[source_branch]", branch),
        ("merge_request[target_branch]", base),
        ("merge_request[title]", title),
    ]
    if description:
        params.append(("merge_request[description]", description))
    if draft:
        params.append(("merge_request[draft]", "true"))
    return f"{web_url}/-/merge_requests/new?{urllib.parse.urlencode(params)}"


def _build_github_compare_url(web_url: str, branch: str, base: str,
                               title: str, description: str | None) -> str:
    params = [("quick_pull", "1"), ("title", title)]
    if description:
        params.append(("body", description))
    return f"{web_url}/compare/{base}...{branch}?{urllib.parse.urlencode(params)}"


def _description_file_path(repo_root: Path, branch: str) -> Path:
    safe = branch.replace("/", "-").replace("\\", "-")
    return repo_root / ".plc" / "briefs" / f"{safe}-mr-description.md"


def _emit_url_handoff(title: str, body: str, branch: str, base: str,
                      draft: bool, repo_root: Path, provider: str) -> int:
    """Print a pre-filled 'Create new MR' URL. Saves long descriptions to a file."""
    web_url = _origin_web_url(repo_root)
    if not web_url:
        print(
            "ERROR: cannot derive web URL from `git remote get-url origin`. "
            "Configure an origin remote (https or ssh) and retry.",
            file=sys.stderr,
        )
        return 1

    embed_description = bool(body) and len(body) <= _URL_DESCRIPTION_BUDGET_CHARS
    description_for_url = body if embed_description else None

    description_file: Path | None = None
    if body and not embed_description:
        description_file = _description_file_path(repo_root, branch)
        description_file.parent.mkdir(parents=True, exist_ok=True)
        description_file.write_text(body, encoding="utf-8")

    if provider == "gitlab":
        url = _build_gitlab_new_mr_url(web_url, branch, base, title, description_for_url, draft)
        cli_hint = "glab"
    else:
        url = _build_github_compare_url(web_url, branch, base, title, description_for_url)
        cli_hint = "gh"

    token_env_var = "GITLAB_TOKEN" if provider == "gitlab" else "GITHUB_TOKEN"
    print()
    print(f"Branch '{branch}' is pushed. {cli_hint} not installed and "
          f"{token_env_var} not set —")
    print("falling back to web handoff. Click this URL to open the MR with")
    print("title, source branch, and target branch pre-filled:")
    print()
    print(f"  {url}")
    print()
    if embed_description:
        print("Description is also pre-filled.")
    elif description_file is not None:
        print(f"Description is long ({len(body)} chars); paste it from:")
        print(f"  {description_file.relative_to(repo_root)}")
    if draft and provider == "github":
        print()
        print("Note: GitHub doesn't support `&draft=1` in the compare URL — after")
        print("the form opens, click the dropdown next to 'Create pull request'")
        print("and choose 'Create draft pull request'.")
    print()
    print("For zero-click MR creation next time, pick ONE:")
    if provider == "gitlab":
        print(f"  Option A — install `{cli_hint}` (recommended for personal machines):")
        print(f"      choco install glab    # Windows")
        print(f"      brew install glab     # macOS")
        print(f"      sudo apt install glab # Linux (Ubuntu 22.04+)")
        print(f"      then: glab auth login")
        print(f"  Option B — set GITLAB_TOKEN (works on managed machines, no install):")
        web_host = _origin_web_url(repo_root) or ""
        host = web_host.split("/", 3)[2] if "://" in web_host else "<your-gitlab-host>"
        print(f"      1. Generate a PAT with scopes 'api' (or 'write_repository') at:")
        print(f"         https://{host}/-/user_settings/personal_access_tokens")
        print(f"      2. Add to .env in this repo:    GITLAB_TOKEN=<paste-token>")
        print(f"      ({_silent_gitignore_note()})")
    else:
        print(f"  Option A — install `{cli_hint}` (recommended for personal machines):")
        print(f"      winget install GitHub.cli   # Windows")
        print(f"      brew install gh             # macOS")
        print(f"      sudo apt install gh         # Linux")
        print(f"      then: gh auth login")
        print(f"  Option B — set GITHUB_TOKEN (works on managed machines, no install):")
        print(f"      1. Generate a fine-grained PAT with 'Pull requests: write' permission.")
        print(f"      2. Add to .env in this repo:    GITHUB_TOKEN=<paste-token>")
        print(f"      ({_silent_gitignore_note()})")
    return 0


def _silent_gitignore_note() -> str:
    return ".env is auto-added to .gitignore on first publish_mr.py run; tracked .env files are rejected"


def run_gerrit(repo_root: Path) -> int:
    print(
        "ERROR: Gerrit publishing is not implemented in publish_mr.py. "
        "The orchestrator should invoke the Gerrit MCP directly for Gerrit "
        "remotes per source/plc-v-model-bug-fix/SKILL.md Phase 9.",
        file=sys.stderr,
    )
    return 1


def _run_publish(cmd: list[str], repo_root: Path, dry_run: bool) -> int:
    if dry_run:
        print("DRY-RUN: " + " ".join(_quote(c) for c in cmd))
        return 0
    result = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
    return result.returncode


def _quote(c: str) -> str:
    if " " in c or "\n" in c:
        escaped = c.replace('"', '\\"')
        return f'"{escaped}"'
    return c


def load_description(description_arg: str | None, description_file_arg: str | None,
                      repo_root: Path) -> str:
    if description_arg is not None and description_file_arg is not None:
        raise SystemExit("ERROR: --description and --description-file are mutually exclusive")
    if description_arg is not None:
        return description_arg
    if description_file_arg is not None:
        path = Path(description_file_arg)
        if not path.is_absolute():
            path = (repo_root / path).resolve()
        if not path.is_file():
            raise SystemExit(f"ERROR: --description-file not found: {description_file_arg}")
        return path.read_text(encoding="utf-8")
    raise SystemExit("ERROR: one of --description or --description-file is required")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Open a merge request for a bug-fix branch. Auto-detects remote "
            "provider (GitHub / GitLab / Gerrit) and shells out to gh or glab."
        ),
    )
    parser.add_argument("--title", required=True, help="MR title.")
    parser.add_argument("--description", help="MR description body (inline string).")
    parser.add_argument("--description-file", help="Path to a file whose contents become the MR description.")
    parser.add_argument("--branch", required=True, help="Source branch (the bug-fix branch).")
    parser.add_argument("--base", help="Target branch (defaults to origin/HEAD or main).")
    parser.add_argument("--draft", action="store_true", help="Open the MR as draft.")
    parser.add_argument("--repo-root", help="Repo root override (defaults to git rev-parse --show-toplevel).")
    parser.add_argument("--provider", choices=("github", "gitlab", "gerrit", "auto"), default="auto",
                        help="Force a provider instead of auto-detecting.")
    parser.add_argument("--dry-run", action="store_true", help="Print the command that would be invoked, don't run it.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    repo_root = detect_repo_root(args.repo_root)
    body = load_description(args.description, args.description_file, repo_root)
    base = args.base or detect_base_branch(repo_root)
    provider = args.provider if args.provider != "auto" else detect_provider(repo_root)

    if provider == "github":
        return run_gh(args.title, body, args.branch, base, args.draft, repo_root, args.dry_run)
    if provider == "gitlab":
        return run_glab(args.title, body, args.branch, base, args.draft, repo_root, args.dry_run)
    if provider == "gerrit":
        return run_gerrit(repo_root)
    print(
        "ERROR: could not detect remote provider (no github/gitlab/gerrit hostname "
        "in `git remote get-url origin`). Pass --provider explicitly.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
