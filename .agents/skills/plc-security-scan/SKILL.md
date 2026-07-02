---
name: plc-security-scan
description: >-
  PLC L1 security scanning skill. Detects secrets on every work unit and runs
  SAST, dependency, license, and container scans before every commit using
  project-local scanner tooling where available.
---

# PLC L1 Security Scan

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"coding","check":"plc-skill-invocation","tool":"plc-security-scan","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-security-scan"}}
JSON
```

```powershell
@'
{"pillar":"coding","check":"plc-skill-invocation","tool":"plc-security-scan","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"plc-security-scan"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

You are working in a PLC L1 compliant repository. Run security scans throughout every coding session.

All checks are optional and non-blocking. If a tool is unavailable or a check cannot run, log a `gap` to `plc-evidence.jsonl` and mark the check as `SKIP` in the PR evidence table. Never silently skip a check. Local checks are early-warning, advisory signals only; they are not proof of compliance and have not been owner-approved as release-equivalent. By default the bundled runner exits `0` for `PASS`, `WARN`, and `SKIP`; it exits `2` only for `ERROR`, such as a scanner crash, malformed scanner output, or an internal runner failure. Scanner timeouts are recorded as `SKIP` (with `timed_out: true` in the evidence record), not `ERROR`. Pass `--fail-on={pass,warn,error}` (default `error`) to raise the exit threshold — `--fail-on=warn` exits `2` on any finding, `--fail-on=pass` exits `2` on any non-`pass` result (including `SKIP`). CI may treat exit `2` as a real local-scanner failure.

Authoritative PLC security evidence comes from nSpect program registrations and in-pipeline ScanSpect jobs that use the Pulse scanning platform; this skill is scoped to local, advisory developer scans only.

---

## Local scanner authority and fallback policy

**Single source of truth.** This section is the authoritative wording contract for all PLC skills. If any other skill or doc disagrees about local-vs-authoritative framing, this section wins. Stronger wording requires explicit, recorded owner approval per the *Owner approval record* format below.

This skill ships local, **advisory** scanners only. None of the local checks have been owner-approved as release-equivalent. Use the following rules verbatim in evidence output and agent summaries; do not strengthen them without explicit approval from the relevant scanner owner (NVSEC / Pulse / ProdSec / OSRB).

**Secret scanning.** The only supported local path is the Pulse Secret Scanner Docker image (`gitlab-master.nvidia.com:5005/pstooling/pulse-group/pulse-secret-scanner`). It uses the same scanner image distributed for the release pipeline, which reduces engine drift but does *not* make local execution release-equivalent: local runs use the developer's working tree, command-line flags, and allowlist file, none of which match the pipeline configuration verbatim. A hosted NVSEC/Pulse service is **not** configured for local use; no owner approval is recorded for one. Do not introduce TruffleHog CLI, gitleaks, or any other secret scanner as a fallback in this skill. If a developer chooses to run one ad hoc, the resulting evidence row must be labeled `advisory, non-canonical` and must not be presented as a substitute for the authoritative scan.

**SAST.** Semgrep with the bundled local ruleset is a local helper, not a SAST authority. Authoritative SAST is SonarQube (non-automotive) or Coverity (automotive) via nSpect / ScanSpect. A clean Semgrep run does not imply a clean Sonar/Coverity run.

**Dependency vulnerabilities.** pip-audit and osv-scanner are local helpers. Authoritative coverage is BlackDuck via Pulse / nSpect / ScanSpect. A clean local pass does not imply BlackDuck will pass.

**License compliance.** pip-licenses only sees what the local Python package metadata declares. Authoritative license/OSRB coverage is BlackDuck via Pulse / nSpect / ScanSpect plus the OSRB workflow. When local and authoritative results disagree, the authoritative result wins:

- A clean local license check does **not** clear OSRB review; if the dependency hits the OSRB list (GPL/LGPL/AGPL/SSPL/Commons Clause/unknown), it still needs OSRB regardless of what the local tool says.
- A flagged local license must **not** be auto-suppressed because BlackDuck happened to pass on a previous run; flag it for human review and let BlackDuck/OSRB decide at the release gate.
- Never write evidence text that says "BlackDuck will accept this" or "OSRB-clear" based on a local result.

**Security code review.** Inline review by the coding assistant is an advisory developer aid; it is not a substitute for ProdSec review, ScanSpect/nSpect analysis, or authoritative release-gate sign-off. Do not surface inline-review findings as "cleared by ProdSec" or similar.

**Release-notes wording.** Any external description of this skill (release notes, status updates, PR boilerplate) must call local scans advisory. The phrases "release-equivalent", "zero discrepancy with the release gate", "would block release", and similar are reserved for wording an owner has explicitly approved; absent that approval, do not use them.

### Owner approval record

Stronger-than-advisory wording is gated by a human-curated record file. An agent considering such wording must read this file before using it.

- **Location:** `~/.agentic-plc/scanner-approvals.jsonl` (one JSON object per line).
- **Schema:** `{"scanner": "<pulse|semgrep|...>", "wording": "<exact phrase the approval covers>", "owner": "<NVSEC|Pulse|ProdSec|OSRB|...>", "approval_ref": "<ticket/email/wiki link>", "approved_on": "<ISO-8601>", "expires_on": "<ISO-8601 or null>"}`.
- **Behavior:** if the file is absent, empty, or has no record matching `(scanner, wording)`, **refuse the stronger wording and use the default advisory framing** above. Do not write to this file from any skill — it is human-curated.

---

## Coverage hygiene and transparency

This skill is local-advisory, but local scans catch real things authoritative scanners miss. Be explicit about the gaps so the user does not get a false sense of coverage.

### Colocated standalone third-party apps

A third-party app vendored into this repo (for example, `third_party/some-app/` carrying its own `package.json`, `Cargo.toml`, `requirements.txt`, etc.) is usually not registered as a dependency of the parent project in nSpect / BlackDuck — those scanners see it as either a separate program or as out-of-scope source. Local `osv-scanner --lockfile=<path>` is therefore the only fast advisory check for that app's CVEs.

Run dependency scanning for every lockfile in the work-unit scope, including ones under `vendor/`, `third_party/`, `external/`, `submodules/`, or any path the default `PLC_REVIEW_EXCLUDE_DIRS` removes from SAST scope. SAST review-exclusion is about source-review noise, not about hiding dependency manifests — the dependency runner walks lockfiles regardless of SAST exclusion. Record a `colocated_app: true` field on each per-lockfile dependency evidence row so reviewers can see which findings are **not** covered by the parent project's release gate. The runner classifies colocation by directory regex (`vendor`, `third_party`, `external`, `submodules`), not by the SAST review-exclude regex, so the four documented prefixes are tagged even though only some appear in `DEFAULT_REVIEW_EXCLUDE_DIRS`.

### Repos without lock files

If a Python project ships only `requirements.txt` (no `uv.lock` / `poetry.lock`), the bundled runner runs `pip-audit` against the manifest as the normal requirements path. If only `pyproject.toml` is present with no `uv.lock` / `poetry.lock`, the runner records `WARN` with the reason starting `"pyproject.toml changed but no uv.lock or poetry.lock present"`, `lockfile_status: "manifest-only"`, and an `orphan_pyprojects` list naming the affected files. The runner does **not** try to scan `pyproject.toml` directly with `pip-audit`; without a resolver lock, pinned versions are unknowable.

If no manifest is present at all but reviewable source files in a dependency-bearing ecosystem (Python/JS/TS/Ruby/Go/Rust) changed, the runner records `WARN` with `lockfile_status: "absent"` and a `source_only_dep_gap_paths` list. The runner does **not** attempt to discover or scan an active project venv; the WARN names the gap so the developer can supply a manifest or run pip-audit by hand.

No-lockfile gap detection is **independent of whether other dependency contexts exist** in the same work unit. A multi-component repo where `web/package-lock.json` is scanned and `service/pyproject.toml` has no lock will produce one PASS-or-WARN context for the node lockfile and an `orphan_pyprojects` entry for the service directory; the aggregate result is upgraded to WARN if either piece warrants it. The gap detector walks both `scope.files` and `scope.review_excluded`, so an orphan `vendor/app/pyproject.toml` or source-only `vendor/app/app.py` change is flagged with `colocated_app_gap: true` rather than silently recorded as `N/A`.

Do not record dependency scanning as `N/A` purely because no lockfile exists. The lockfile-absent / manifest-only cases are precisely the cases where BlackDuck has nothing to ingest either, so the local advisory result is the only signal the developer gets before release. Always label the evidence row with `lockfile_status: "present" | "absent" | "manifest-only" | "inconsistent"` so this state is visible in the PR table without re-reading the manifest.

### Rust SAST

SonarQube does not support Rust. There is no authoritative SAST equivalent for `.rs` files in the standard PLC pipeline. The bundled Semgrep ruleset (`assets/semgrep-rules.tar.gz`) includes a `rust/` rule pack, so the local Semgrep pass is the only SAST signal for Rust code at all. Those bundled Rust rules declare INFO/WARNING severities, so the runner drops the default `--severity=ERROR` filter when `.rs` files are in scope and records `severity_filter: "ALL"` on the evidence row; otherwise no Rust rule would ever fire. Surface Rust SAST findings prominently in the PR summary because no downstream Sonar/Coverity pass will catch what they miss — but the local-advisory authority contract at the top of this skill still applies: a Rust Semgrep `WARN` remains local advisory evidence, not release-equivalent, unless the stronger wording is gated by a matching record in the human-curated owner-approval file defined by *Local scanner authority and fallback policy → Owner approval record*. The point is to make the local signal harder to overlook, not to relabel it.

### License coverage metadata and false-negative escalation

`pip-licenses --format=json` reads only what the package metadata declares (`License`, `License-Expression`, classifier set). Many wheels declare `License: UNKNOWN` or leave the field blank because the project's `setup.py` did not populate it. That is a false negative, not a clean result.

Treat `License` values of `UNKNOWN`, empty string, `null`, or the string `Unknown` (case-insensitive) as a `WARN` row with `reason: "license_metadata_missing"` and a `package: "<name>==<version>"` field. Do not record `PASS` for these rows. Always include a `metadata` block per package in the JSON artifact with the raw `License`, `License-Expression`, and the top three `Classifier::License::*` strings so the reviewer can re-classify without re-running. Escalate to OSRB review when the metadata is missing — only OSRB can confirm or override.

Explicit non-permissive / OSRB-class licenses are also a `WARN` row, with `license_requires_osrb_review` recorded per package. The normalizer matches the License field, the `License-Expression` field, and the package classifiers against GPL, LGPL, AGPL, SSPL, Commons-Clause / CC-BY-NC / CC-BY-SA, Affero, "GNU General Public", "Lesser General Public", proprietary, and custom-license patterns (case-insensitive, prefix-based so `GPL-3.0-only`, `LGPLv3`, classifier-only declarations, and modern SPDX-only-via-License-Expression declarations all trigger). The artifact carries a `review_reasons` *list* per affected package so a single package can carry both `license_metadata_missing` AND `license_requires_osrb_review` when, for example, `License: UNKNOWN` is paired with `License-Expression: AGPL-3.0`. When a single artifact contains both kinds, the top-level `reason` is `license_metadata_missing_and_requires_osrb_review`. Aggregate counts are `missing_metadata_count` and `osrb_review_count`; the two lists are not mutually exclusive. A clean local `PASS` on a copyleft-licensed package is worse than skipping; the normalizer makes sure it cannot happen.

### Default detailed scan artifacts

Detailed JSON artifacts in `.plc/security/<scanner>.json` are produced by default. There is no `--detailed` flag and no "summary-only" mode in the runner. The PR summary table shows aggregate counts plus 1–3 top findings per row; the artifact is the source of truth for everything else. Every dependency-context outcome — including SKIP paths for unavailable scanners and lock-check failures — writes a JSON stub at the per-context `dependency-<dir>-<manager>-<lockfile>.json` path, and the evidence record carries the artifact path so PR reviewers can open it even when the tool did not run. All artifact writes go through an atomic-replace helper (tempfile + `Path.replace()`); an interrupted write cannot leave a truncated artifact replacing a previous good one.

### SAST file/line output

The sanitized Semgrep artifact (`.plc/security/semgrep.json`) guarantees `path`, `start_line`, `end_line`, `check_id`, and `severity` per finding — see `sanitize_semgrep_data` in the runner. The PR summary's WARN rows are required to surface 1–3 findings as `file:line check-id` so a reviewer can triage without opening the JSON. If a WARN row does not list `file:line` for at least one finding, the summary generator is broken, not the artifact.

### `.env` default scan-scope handling

`.env`, `.env.*`, and `.envrc` files contain secrets and shell-style configuration, not source code. The default scopes treat them accordingly:

- **In scope for the secret scanner (Pulse) by default.** Do not exclude them from secret scanning to silence noise. The existing Pulse triage at *On unverified finding* already covers the `.gitignore`-match recommendation for `.env.dev` / `.env.local`-style local-only dev fixtures.
- **Out of scope for SAST (Semgrep) by default.** Semgrep rules target source-code constructs; key=value files produce only false positives. The SAST runner skips files matching `\.env(\..*)?$` or `\.envrc$` from its reviewable scope before copying the temp scan tree. Set `PLC_INCLUDE_ENV_FILES_IN_SAST=1` only when the user explicitly asks for SAST on env-file syntax.

### Temporary scan artifact cleanup

The bundled runner uses `tempfile.TemporaryDirectory` for every per-invocation working tree (Semgrep scan tree, extracted rule archive, sanitizer buffers); those directories are cleaned up automatically when the runner exits, including under exceptions. The only persistent output the runner writes inside the repo is the `.plc/security/` tree and the `plc-evidence.jsonl` file.

`.plc/security/` is deliberately **not** added to `.gitignore` so PR reviewers can browse the artifacts the developer scanned against; `.plc/tools/` (binaries, venvs, caches) is `.gitignore`d separately. If the runner crashes mid-write, the sanitized-tempfile `trap` in the shell fallback and the runner's atomic `mv` of the sanitized projection prevent a partially-written artifact from replacing a previous good one — the previous artifact stays in place. Stray temp trees from an earlier crash will be named `plc-semgrep.*` or `plc-security-artifacts.*` under the system temp dir; safe to `rm -rf` outside an active scan.

---

## Scan artifacts directory

Every **local scanner check** writes its detailed output to `.plc/security/` at the repo root. This is the single, predictable location the developer can inspect when aggregate evidence counts require more detail. The evidence log itself stays compact and does not repeat artifact paths.

Layout:

```
.plc/security/
├── pulse-secret-scan.json   # Pulse Secret Scanner (Raw field redacted; Redacted field kept)
├── semgrep.json             # Semgrep — sanitized projection: {version, results: [{path, start_line, end_line, check_id, severity}]} (extra.lines, extra.metavars, and extra.message dropped — see rule 3 below)
├── dependency-<context>-<manager>-<manifest-or-lockfile>.json
│                           # pip-audit/osv-scanner JSON for one dependency context; path parts are slugged, e.g. dependency-root-requirements-requirements-dev-txt.json
└── pip-licenses.json        # pip-licenses --format=json
```

Rules:

1. **Every local scanner check writes a JSON artifact, even on `SKIP`.** If the tool didn't run, write a stub: `{"check":"<name>","tool":"<tool>","result":"skip","reason":"<reason>","timestamp":"<ISO-8601 UTC>"}`. This way the path stated in the PR summary always resolves to a file the reader can open. The `Container` and `Security Review` rows are exceptions — they have no scanner and produce no artifact; they show `—` in the Artifact column.
2. **Artifacts are overwritten per run.** The latest run wins; `plc-evidence.jsonl` is still the append-only historical log of every attempt.
3. **No raw source snippets or credentials in any artifact.** Every scanner output is passed through a sanitizer before it touches disk. The sanitizers are implemented in Python (`python3` is already a hard prerequisite of this skill — the bundled runner is Python — so this works on every machine the rest of the skill works on):
   - **Pulse Secret Scanner.** A `python3` filter drops the `Raw` field from each JSONL line; only `Redacted` is kept.
   - **Semgrep.** A `python3` projection retains only `path`, `start_line`, `end_line`, `check_id`, and `severity`. `extra.lines`, `extra.metavars`, **and `extra.message`** are dropped: `extra.lines` and `extra.metavars` carry source excerpts; `extra.message` can interpolate matched metavariables, so a rule that fires on a hardcoded credential can put the credential back into `message`. `file:line check_id` is enough for actionability in the PR summary; the developer reads the message by running Semgrep locally on the flagged location, not from the artifact.
   - **pip-audit, osv-scanner, pip-licenses.** No secret material is surfaced; these are saved as-is.

   The skip-vs-error contract is:

   - **Sanitizer cannot run** (e.g., `python3` somehow missing) → write a `skip` stub with a clear reason. The scanner did not run; nothing about the code was inspected.
   - **Scanner crashed, exited non-zero unexpectedly, or emitted invalid/empty sanitized JSON** → write an `error` stub. Something was attempted but produced no usable result.

   In either case, never write unsanitized output to the artifact path.
4. **Path stability.** The filenames above are the contract — the PR summary points at them and other tooling can rely on them. Do not move files into subdirectories per branch / commit; branch and commit metadata live inside `plc-evidence.jsonl` and inside each artifact's own fields.

Before running any scan, ensure the directory exists:

```bash
mkdir -p .plc/security
```

Add scanner-tooling state and the developer-local evidence log to the repo `.gitignore`; the runner does not edit `.gitignore` for you. Do **not** ignore `.plc/` as a whole — `.plc/security/` is deliberately reviewable so PR reviewers can browse the artifacts the developer scanned against (see *Coverage hygiene and transparency → Temporary scan artifact cleanup*).

```
.plc/tools/
plc-evidence.jsonl
```

The artifact directory is the source the developer and the PR-summary template both read from. Status (`PASS` / `WARN` / `SKIP` / `N/A`) and one-line counts go to `plc-evidence.jsonl`; detail (per-finding file:line, package versions, license names) lives in the JSON artifact.

---

## Setup

This skill must work even when the user forgot to run `setup-agentic-plc`. Before running local advisory scans, prepare project-local scanner state under `.plc/tools`. Do not add scanner packages to project dependencies, do not modify lockfiles, and do not install scanner tools into user-level or platform-level locations.

Resolve the repo root and create the local tool directories:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO" ]; then
  REPO="$(p4 -ztag info 2>/dev/null | sed -n 's/^\.\.\. clientRoot //p' | head -1)"
fi
if [ -z "$REPO" ]; then
  REPO="$(pwd)"
fi
mkdir -p "$REPO/.plc/tools/uv-cache" "$REPO/.plc/tools/bin" "$REPO/.plc/tools/cache"
```

Prefer project-local `uvx` execution for Python scanners. These commands are version-pinned and use a repo-local uv cache:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'semgrep==1.157.0' semgrep --version
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'pip-audit==2.9.0' pip-audit --version
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'pip-licenses==5.5.5' pip-licenses --version
```

This is intentionally independent of whether the repo itself uses Poetry, uv, pip, or another Python workflow. Do not mutate `pyproject.toml`, `poetry.lock`, `uv.lock`, or `requirements*.txt` to install scanner tooling.

The `uvx` commands are the preferred path on Windows, WSL, macOS, and Linux when `uv` is available in the active shell.

If `uvx` is unavailable, use a project-local Python venv fallback:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
python3 -m venv "$REPO/.plc/tools/python-venv"
"$REPO/.plc/tools/python-venv/bin/python" -m pip install \
  'semgrep==1.157.0' \
  'pip-audit==2.9.0' \
  'pip-licenses==5.5.5'
```

On Windows, use the equivalent scripts directory:

```powershell
$Repo = (git rev-parse --show-toplevel 2>$null); if (-not $Repo) { $Repo = (Get-Location).Path }
$VenvDir = Join-Path $Repo ".plc\tools\python-venv"
New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir -Parent) | Out-Null
py -3 -m venv $VenvDir
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install `
  'semgrep==1.157.0' `
  'pip-audit==2.9.0' `
  'pip-licenses==5.5.5'
```

Use isolated project-local tools first when invoking scanners through `uvx`, `.plc/tools/python-venv`, or `.plc/tools/bin`.

Install `osv-scanner v2.3.5` only under `.plc/tools/bin/osv-scanner` or `.plc/tools/bin/osv-scanner.exe`. Use an already cached binary from `.plc/tools/cache` only when its pinned checksum matches; otherwise download the pinned binary from the official Google OSV Scanner GitHub release at `https://github.com/google/osv-scanner/releases/download/v2.3.5/`. Verify the pinned checksum before marking it usable. If the current OS/architecture is unsupported or the official release download is unreachable, log a SKIP for `osv-scanner` with that specific reason.

Pinned OSV Scanner assets:

| Platform | Asset | SHA256 |
|---|---|---|
| macOS x86_64 | `osv-scanner_darwin_amd64` | `3b1c72d59dcbad99fa4eb2c72bf2e82017f83e0268340e4b00af76a1fea32c85` |
| macOS arm64 | `osv-scanner_darwin_arm64` | `b740efe0b08fb817865e818a498997d5f042f14b8eeafb6393176ce84dd09cf6` |
| Linux x86_64 | `osv-scanner_linux_amd64` | `bb30c580afe5e757d3e959f4afd08a4795ea505ef84c46962b9a738aa573b41b` |
| Linux arm64 | `osv-scanner_linux_arm64` | `fa46ad2b3954db5d5335303d45de921613393285d9a93c140b63b40e35e9ce50` |
| Windows x86_64 | `osv-scanner_windows_amd64.exe` | `b165d33c08bda663119a459f5187e096d2525f888503495f5f34925741e981a2` |
| Windows arm64 | `osv-scanner_windows_arm64.exe` | `a3d2ffa712fd2376e88a02c98b917a87885c761d0d21980b8f5eabfb910cbcb8` |

See `setup-agentic-plc/SKILL.md` → *macOS/Linux/WSL install command* for shell selection (WSL uses the bash command below; do not mix the Windows `.exe` into a WSL shell).

macOS/Linux/WSL install command:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
OSV_VERSION="v2.3.5"
mkdir -p "$REPO/.plc/tools/bin" "$REPO/.plc/tools/cache"
case "$(uname -s)-$(uname -m)" in
  Darwin-x86_64) OSV_ASSET="osv-scanner_darwin_amd64"; OSV_ASSET_ID="380248790"; OSV_SHA256="3b1c72d59dcbad99fa4eb2c72bf2e82017f83e0268340e4b00af76a1fea32c85" ;;
  Darwin-arm64) OSV_ASSET="osv-scanner_darwin_arm64"; OSV_ASSET_ID="380248752"; OSV_SHA256="b740efe0b08fb817865e818a498997d5f042f14b8eeafb6393176ce84dd09cf6" ;;
  Linux-x86_64) OSV_ASSET="osv-scanner_linux_amd64"; OSV_ASSET_ID="380248788"; OSV_SHA256="bb30c580afe5e757d3e959f4afd08a4795ea505ef84c46962b9a738aa573b41b" ;;
  Linux-aarch64|Linux-arm64) OSV_ASSET="osv-scanner_linux_arm64"; OSV_ASSET_ID="380248755"; OSV_SHA256="fa46ad2b3954db5d5335303d45de921613393285d9a93c140b63b40e35e9ce50" ;;
  *) echo "Unsupported OS/architecture for osv-scanner: $(uname -s)-$(uname -m)" >&2; exit 2 ;;
esac
OSV_CACHE="$REPO/.plc/tools/cache/$OSV_ASSET"
OSV_RELEASE_URL="https://github.com/google/osv-scanner/releases/download/$OSV_VERSION/$OSV_ASSET"
OSV_API_URL="https://api.github.com/repos/google/osv-scanner/releases/assets/$OSV_ASSET_ID"
if [ ! -f "$OSV_CACHE" ]; then
  echo "Downloading $OSV_RELEASE_URL"
  curl -fL -H 'Accept: application/octet-stream' "$OSV_API_URL" -o "$OSV_CACHE"
fi
python3 - "$OSV_CACHE" "$OSV_SHA256" <<'PY'
import hashlib
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"checksum mismatch for {path}: expected {expected}, got {actual}")
PY
install -m 755 "$OSV_CACHE" "$REPO/.plc/tools/bin/osv-scanner"
"$REPO/.plc/tools/bin/osv-scanner" --version
```

Windows PowerShell install command:

```powershell
$Repo = (git rev-parse --show-toplevel 2>$null); if (-not $Repo) { $Repo = (Get-Location).Path }
$Version = "v2.3.5"
$Arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
if ($Arch -eq [System.Runtime.InteropServices.Architecture]::Arm64) {
  $Asset = "osv-scanner_windows_arm64.exe"
  $AssetId = "380248753"
  $Sha256 = "a3d2ffa712fd2376e88a02c98b917a87885c761d0d21980b8f5eabfb910cbcb8"
} else {
  $Asset = "osv-scanner_windows_amd64.exe"
  $AssetId = "380248754"
  $Sha256 = "b165d33c08bda663119a459f5187e096d2525f888503495f5f34925741e981a2"
}
$BinDir = Join-Path $Repo ".plc\tools\bin"
$CacheDir = Join-Path $Repo ".plc\tools\cache"
New-Item -ItemType Directory -Force -Path $BinDir, $CacheDir | Out-Null
$Cache = Join-Path $CacheDir $Asset
$ReleaseUrl = "https://github.com/google/osv-scanner/releases/download/$Version/$Asset"
$ApiUrl = "https://api.github.com/repos/google/osv-scanner/releases/assets/$AssetId"
if (-not (Test-Path $Cache)) {
  Write-Host "Downloading $ReleaseUrl"
  Invoke-WebRequest -Headers @{Accept = "application/octet-stream"} -Uri $ApiUrl -OutFile $Cache
}
if ((Get-FileHash -Algorithm SHA256 $Cache).Hash.ToLowerInvariant() -ne $Sha256) {
  throw "checksum mismatch for $Cache"
}
Copy-Item -Force $Cache (Join-Path $BinDir "osv-scanner.exe")
& (Join-Path $BinDir "osv-scanner.exe") --version
```

For backward compatibility with old Agentic PLC releases, do not remove or modify any existing PATH tools. Prefer isolated project-local execution first: `uvx`, then an existing `.plc/tools/python-venv`, then lazy creation/population of that venv with the pinned Python scanners above. Use PATH only after those isolated options are unavailable.

**Docker** (required for Pulse secret scanning):
```bash
docker --version
```
If not installed: request Docker Desktop at http://nv/dockerdesktop.

Authenticate with a GitLab personal access token (not your AD password). Create one at https://gitlab-master.nvidia.com/-/profile/personal_access_tokens with `read_registry` scope. Use it as the password when prompted:
```bash
docker login gitlab-master.nvidia.com:5005
```
`write_registry` is not required to pull the Pulse scanner image.

> Note: Semgrep here is a **local**, fast SAST helper.
> For PLC and MVSB compliance, your **canonical** SAST must be **SonarQube** (non‑automotive) or **Coverity** (automotive), registered and wired via nSpect / in‑pipeline jobs as documented in [Static Analysis Tools](https://nvidia.atlassian.net/wiki/spaces/PRODSEC/pages/2569245428).

---
## During Coding — Security

### Bundled runner (preferred SAST and dependency flow)

If `plc_security_scan.py` ships with this skill, prefer it for the SAST and dependency vulnerability passes. The runner builds the work-unit scope, applies always-excluded and review-excluded filters, copies a temporary scan tree for Semgrep, runs dependency scanners from the manifest/lockfile owner directory, checks lockfile consistency before dependency scanning, and appends results to `plc-evidence.jsonl` for you.

Minimal invocations:

```bash
python3 <SKILL_DIR>/scripts/plc_security_scan.py run-sast
python3 <SKILL_DIR>/scripts/plc_security_scan.py run-dependency-vuln
```

The SAST runner writes the resolved scope to `.plc/security/scope-files.txt` (and the unfiltered list to `.plc/security/scope-files-unfiltered.txt`). The shell scope-building snippet below is a manual fallback for hosts where the runner cannot run; do not run both. The bundled runner is the preferred local SAST and dependency-vulnerability flow. The license, secret, and container check sections still use the shell snippets.

### Scan scope and project context

Default to the current work unit, not the whole repository, unless the user asks for a full baseline scan. In Git, build the reviewable file list from the union of committed branch changes, staged changes, unstaged changes, and untracked non-ignored files. In Perforce, use non-delete files from `p4 -ztag opened` in the current client.

If the bundled runner (`plc_security_scan.py run-sast` and `plc_security_scan.py run-dependency-vuln`) is available, use it instead — it builds the scope, applies exclusions, records evidence, and handles temp trees/project-context execution automatically. The shell snippet below is a manual fallback for hosts where the runner cannot run. In the fallback flow, `PLC_SCOPE_FILE` names a temp file used by the dependency/secret/license shell snippets in this skill; the bundled runner does not read `PLC_SCOPE_FILE` and writes its own scope/evidence instead.

```bash
PLC_SCOPE_FILE="${PLC_SCOPE_FILE:-$(mktemp -t plc-reviewable.XXXXXX)}"
PLC_SCOPE_UNFILTERED="${PLC_SCOPE_FILE}.unfiltered"
PLC_SCOPE_CANDIDATE="${PLC_SCOPE_FILE}.candidate"
PLC_SCOPE_ALWAYS_EXCLUDED="${PLC_SCOPE_FILE}.always-excluded"
PLC_SCOPE_REVIEW_EXCLUDED="${PLC_SCOPE_FILE}.review-excluded"

BASE_REF="${BASE_REF:-}"
if [ -z "$BASE_REF" ]; then
  BASE_REF="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
fi
if [ -z "$BASE_REF" ]; then
  BASE_REF="$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null | sed 's#^refs/remotes/##' || true)"
fi
if [ -z "$BASE_REF" ]; then
  for candidate in origin/main origin/master main master; do
    if git rev-parse --verify --quiet "$candidate" >/dev/null; then
      BASE_REF="$candidate"
      break
    fi
  done
fi

RESCOPE_REASON=""
if [ -n "$BASE_REF" ] && MERGE_BASE_RAW="$(git merge-base HEAD "$BASE_REF" 2>/dev/null)"; then
  MERGE_BASE="$MERGE_BASE_RAW"
  {
    git diff --name-only --diff-filter=ACMR "$MERGE_BASE"...HEAD
    git diff --name-only --diff-filter=ACMR --cached
    git diff --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | sort -u > "$PLC_SCOPE_UNFILTERED"
else
  if [ -n "$BASE_REF" ]; then
    RESCOPE_REASON="base ref '$BASE_REF' not reachable; using local worktree changes only"
  else
    RESCOPE_REASON="no upstream/default base ref found; using local worktree changes only"
  fi
  echo "RESCOPE: $RESCOPE_REASON" >&2
  {
    git diff --name-only --diff-filter=ACMR --cached
    git diff --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | sort -u > "$PLC_SCOPE_UNFILTERED"
fi

PLC_ALWAYS_EXCLUDE_DIRS="${PLC_ALWAYS_EXCLUDE_DIRS:-\.venv|venv|node_modules|coverage|\.tox|\.mypy_cache|\.pytest_cache|\.plc|\.plc-scan|plc-evidence\.jsonl}"
PLC_REVIEW_EXCLUDE_DIRS="${PLC_REVIEW_EXCLUDE_DIRS:-dist|build|vendor|third_party|generated}"
grep -E "(^|/)(${PLC_ALWAYS_EXCLUDE_DIRS})(/|$)" "$PLC_SCOPE_UNFILTERED" > "$PLC_SCOPE_ALWAYS_EXCLUDED" || true
grep -Ev "(^|/)(${PLC_ALWAYS_EXCLUDE_DIRS})(/|$)" "$PLC_SCOPE_UNFILTERED" > "$PLC_SCOPE_CANDIDATE" || true
grep -E "(^|/)(${PLC_REVIEW_EXCLUDE_DIRS})(/|$)" "$PLC_SCOPE_CANDIDATE" > "$PLC_SCOPE_REVIEW_EXCLUDED" || true

if [ "${PLC_INCLUDE_GENERATED_VENDOR:-0}" != "1" ]; then
  grep -Ev "(^|/)(${PLC_REVIEW_EXCLUDE_DIRS})(/|$)" "$PLC_SCOPE_CANDIDATE" > "$PLC_SCOPE_FILE" || true
  if [ -s "$PLC_SCOPE_REVIEW_EXCLUDED" ]; then
    echo "RESCOPE: excluded generated/vendor/build/dist paths listed in $PLC_SCOPE_REVIEW_EXCLUDED; set PLC_INCLUDE_GENERATED_VENDOR=1 and rebuild scope if those changes are intentional" >&2
  fi
else
  cp "$PLC_SCOPE_CANDIDATE" "$PLC_SCOPE_FILE"
  echo "RESCOPE: including generated/vendor/build/dist paths because PLC_INCLUDE_GENERATED_VENDOR=1" >&2
fi
if [ -s "$PLC_SCOPE_ALWAYS_EXCLUDED" ]; then
  echo "RESCOPE: excluded dependency/cache paths listed in $PLC_SCOPE_ALWAYS_EXCLUDED" >&2
fi
if [ ! -s "$PLC_SCOPE_FILE" ] && [ -n "$RESCOPE_REASON" ]; then
  echo "SKIP: changed-file scope unavailable because $RESCOPE_REASON" >&2
fi
```

The scope block first uses `BASE_REF` when provided, then `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` (GitLab MR pipelines: the bundled runner resolves it via `origin/<name>` when available, otherwise the plain name), then the branch upstream, then `origin/HEAD`, then common default branch names. If no base ref is reachable, record the `RESCOPE` note and use local worktree changes only: staged, unstaged, and untracked non-ignored files. If that rescope produces an empty reviewable file list, record `SKIP — changed-file scope unavailable` instead of reporting a passing scan. If HEAD equals the merge base with the resolved base ref and the worktree has no local, staged, or untracked changes, the runner records `SKIP — no changes vs base ref '<base_ref>'; nothing to scan` so the empty scope is not silently reported as `PASS`. If the repo is a Perforce workspace, the bundled runner uses `p4 -ztag opened`; if no non-delete files are opened, record `SKIP — no files opened in Perforce client; nothing to scan`. If the repo is neither Git nor Perforce, require a user-provided file list or an explicit full-scan request; otherwise record `SKIP — changed-file scope unavailable`.

Prefer the bundled runner when possible because it records scope decisions in `plc-evidence.jsonl`. If you use the shell snippets instead, capture both stdout and stderr; the `RESCOPE` and `SKIP` notes are written to stderr.

Always exclude dependency, cache, and local scan artifact paths using `PLC_ALWAYS_EXCLUDE_DIRS`, whose default is `.venv`, `venv`, `node_modules`, `coverage`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.plc`, `.plc-scan`, and `plc-evidence.jsonl`. Exclude generated, vendored, and build-style paths using `PLC_REVIEW_EXCLUDE_DIRS`, whose default is `dist`, `build`, `vendor`, `third_party`, and `generated`. The second list can contain real source in some repos, so the scope step writes the unfiltered list, the always-excluded list, and the review-excluded list.

Values are pipe-delimited regex alternations; regex metacharacters (e.g., `.`) must be escaped. Malformed values fall back to the default with a stderr warning. `PLC_INCLUDE_GENERATED_VENDOR` accepts any of `1`, `true`, `yes`, or `on` (case-insensitive) to include the review-excluded list in the reviewable scope; any other value keeps it filtered out.

If the review-excluded list is non-empty, inspect it before scanning. If the work unit intentionally modifies generated, vendored, `dist`, or `build` source files, set `PLC_INCLUDE_GENERATED_VENDOR=1`, rebuild the reviewable file list, and state that the normal review exclusion was overridden. This override does not include `.venv`, `node_modules`, cache, or coverage directories. If the exclusion is correct, keep the filtered list and report the excluded paths as out of scope.

For dependency and license scans, run from the directory that owns the manifest or lockfile:

- Python: the directory containing `pyproject.toml`, `poetry.lock`, `uv.lock`, `requirements*.txt`, or the project `.venv`.
- Node: the directory containing `package.json` and its lockfile.
- Go/Rust/other ecosystems: the directory containing the relevant lockfile.

**Baseline comparison.** Before presenting findings as work-unit findings, compare each finding against the base branch or a prior baseline scan when practical and label apparently pre-existing findings rather than auto-suppressing them. The bundled dependency runner compares parseable dependency findings against the base manifest/lockfile context and labels them as `new`, `pre-existing`, or `unknown-baseline`. The bundled SAST runner still reports every Semgrep result for the current work-unit scope as-is, so the agent is responsible for any SAST baseline-aware reframing. When applying this guidance manually, prefer stable comparison keys: Pulse `{detector, redacted hash, path}`, Semgrep `{rule id, path, Semgrep fingerprint if present}`, dependency scanners `{package, version, advisory id}`, license checks `{package, version, license}`. A changed file alone is not enough to call an old finding new — look for a changed hunk, changed dependency entry, changed manifest entry, or changed lockfile entry. If the baseline, fingerprint, or changed-entry check is unavailable, label the finding as `UNKNOWN — baseline comparison incomplete` and ask for review instead of suppressing it.

> Limitations: this skill does not bundle a persistent baseline store, fingerprint cache, or automatic suppression mechanism. Treat SAST, secret, license, and container baseline handling as recommended agent behaviour, not a runner contract.

### On every work unit

- **Secret detection.** Scan all changed files for API keys, credentials, tokens, private keys.
- Tool: Pulse Secret Scanner. The image is the only owner-blessed local secret scanner for this skill (see *Local scanner authority and fallback policy* above). It is the same image distributed for the release pipeline, which reduces engine drift, but local execution remains advisory and is not release-equivalent.

> The bundled runner does not yet cover secret scanning; follow the shell snippet below.

Use the reviewable file list above when practical. For Pulse, prefer a temporary scan tree that contains only reviewable files while preserving relative paths. Pulse scans the current state of those files only; for historical-state scanning, use a baseline scan. `PLC_SCAN_TMP_PARENT` is honored by both the bundled SAST runner (`plc_security_scan.py run-sast`) and the Pulse temp-tree fallback. If the temp directory is not accessible from Docker because of file-sharing restrictions, set `PLC_SCAN_TMP_PARENT="$HOME/.cache/plc-scan"` and rerun. If you intentionally put scan scratch space inside the repo, add that directory to `.gitignore`. If a temp tree is not practical for the repo shape, scan the repo root, state that scope was broadened, and label findings outside the reviewable file list as suspected baseline/pre-existing unless the diff changed that line or file.

```bash
(
set -o pipefail
mkdir -p .plc/security
ARTIFACT=.plc/security/pulse-secret-scan.json
REDACTED_TMP="$(mktemp -t pulse-redacted.XXXXXX.json)"

# Build a temp scan tree containing only reviewable files when scope is known.
SCAN_ROOT_WAS_TEMP=0
SCAN_ROOT="${PWD}"
if [ -s "${PLC_SCOPE_FILE:-}" ]; then
  SCAN_PARENT="${PLC_SCAN_TMP_PARENT:-${TMPDIR:-/tmp}}"
  mkdir -p -- "$SCAN_PARENT"
  SCAN_ROOT="$(mktemp -d "$SCAN_PARENT/plc-scan.XXXXXX")"
  SCAN_ROOT_WAS_TEMP=1
  while IFS= read -r path; do
    if [ -L "$path" ]; then
      echo "WARN: skipping symlink in Pulse scan scope: $path" >&2
      continue
    fi
    [ -f "$path" ] || continue
    mkdir -p -- "$SCAN_ROOT/$(dirname -- "$path")"
    cp -P -- "$path" "$SCAN_ROOT/$path"
  done < "$PLC_SCOPE_FILE"
fi
trap 'rm -f "$REDACTED_TMP"; if [ "${SCAN_ROOT_WAS_TEMP:-0}" = "1" ]; then rm -rf -- "$SCAN_ROOT"; fi' EXIT

ALLOWLIST_ARG=()
if [ -f "${PWD}/.nspect-allowlist.toml" ]; then
  if [ "$SCAN_ROOT" != "${PWD}" ]; then
    cp -P -- "${PWD}/.nspect-allowlist.toml" "$SCAN_ROOT/.nspect-allowlist.toml"
  fi
  ALLOWLIST_ARG=(--allowlist="${SCAN_ROOT}/.nspect-allowlist.toml")
fi

# Pipe scanner stdout DIRECTLY through the Python redactor so the raw stream
# never lands on disk. pipefail makes docker's failure visible. The three
# branches below cover: successful pipeline + empty redacted output (Pulse
# ran, no findings -> write a valid PASS envelope), successful pipeline +
# non-empty output (validate as JSON, then atomic mv), failed pipeline (write
# an error stub). No path ever produces a zero-byte artifact.
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if docker run --rm \
    -v "${SCAN_ROOT}":"${SCAN_ROOT}" -w "${SCAN_ROOT}" \
    gitlab-master.nvidia.com:5005/pstooling/pulse-group/pulse-secret-scanner:1.70 \
    filesystem . \
    --json --only-verified \
    --verifier gitlab=https://gitlab-master.nvidia.com \
    "${ALLOWLIST_ARG[@]}" \
  | python3 -c 'import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    obj = json.loads(line)
    if isinstance(obj, dict):
        obj.pop("Raw", None)
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
' > "$REDACTED_TMP"
then
  if [ ! -s "$REDACTED_TMP" ]; then
    printf '{"check":"secret-scan","tool":"pulse-secret-scanner","result":"pass","findings_verified":0,"timestamp":"%s"}\n' \
      "$TS" > "$ARTIFACT"
  elif ! python3 -c 'import json, sys
for line in open(sys.argv[1]):
    line = line.strip()
    if line:
        json.loads(line)
' "$REDACTED_TMP" >/dev/null 2>&1; then
    printf '{"check":"secret-scan","tool":"pulse-secret-scanner","result":"error","reason":"redacted output failed JSON validation","timestamp":"%s"}\n' \
      "$TS" > "$ARTIFACT"
    exit 1
  else
    mv "$REDACTED_TMP" "$ARTIFACT"
  fi
else
  printf '{"check":"secret-scan","tool":"pulse-secret-scanner","result":"error","reason":"pulse scanner failed (exit non-zero or docker/registry unreachable)","timestamp":"%s"}\n' \
    "$TS" > "$ARTIFACT"
  exit 1
fi
)
```

If `.nspect-allowlist.toml` exists in the repo root, it is copied into the scan tree and passed via `--allowlist`; otherwise the flag is omitted automatically.

The Pulse snippet runs inside a subshell (the surrounding `( ... )` above). `SCAN_ROOT` and `ALLOWLIST_ARG` are local to that snippet and do not persist after it exits.

This snippet is written to **fail closed and never write a zero-byte artifact**:

- `set -o pipefail` propagates docker's failure through the `| python3` pipe; without it the pipeline status is python's, and a docker crash followed by an empty stream is silently treated as success.
- The redacted stream is buffered to a `mktemp` file (with a trap to clean it up) and only `mv`'d to `.plc/security/pulse-secret-scan.json` after both (a) the pipeline succeeded and (b) the buffered output passes JSON validation (each line parses as a JSON object).
- **Empty redacted stream after a successful run** (Pulse ran, found nothing) is handled explicitly: instead of moving the zero-byte tmp file, the snippet writes a valid PASS envelope `{"check":"secret-scan","tool":"pulse-secret-scanner","result":"pass","findings_verified":0,"timestamp":...}`. The artifact path always resolves to a readable, schema-compliant JSON object.
- On any failure (docker non-zero, redaction invalid) we write an error stub at the artifact path. Never an unredacted artifact and never a zero-byte file.
- The raw scanner output is never written to disk: the Python redactor filters the stream in flight; the only on-disk intermediate is the *redacted* tempfile, and even that is removed by the trap.

The Python `del(Raw)` redactor is non-negotiable: it strips the raw credential value from every finding before the artifact hits disk. The Pulse Secret Scanner emits one JSON object per line with both `Raw` (the live credential) and `Redacted` (a safe preview); we only ever persist `Redacted`. `python3` is a hard prerequisite of this skill (the bundled runner is Python), so the redactor itself is always available; on the rare host where `python3` is absent, treat that as the same kind of setup failure as missing Docker.

Latest image tag and details: see [Pulse Secret Scanner](https://confluence.nvidia.com/display/PRODSEC/Pulse+Secret+Scanner).

- On **verified** finding: report the file and credential type, recommend rotation and replacement with an env var or vault reference.
    - Log to `plc-evidence.jsonl`.
    - Never print the secret value — use the `Redacted` field from scan output, not `Raw`.
- On **unverified** finding: determine if real.
    - **Before treating as real**, check whether the flagged path is covered by `.gitignore` — files like `.env.dev`, `.env.local`, or anything already ignored are typically local-only dev fixtures, not committed secrets. Report the gitignore match in the finding and recommend the user confirm before acting.
    - If false positive, add to `.nspect-allowlist.toml` using the `Redacted` value only, never `Raw`.

For guided triage: `nicc skills pull nspect-scan-remediation`.
### Before every commit

- **SAST** Scan for vulnerability patterns (injection, XSS, auth bypass, path traversal, insecure crypto).
    - Tool (local helper): Semgrep

Run Semgrep for the list of reviewable files changed in the work unit. Do not scan `.venv`, `node_modules`, generated output, build output, or vendored trees unless the user requested a full baseline scan or explicitly included review-excluded source paths.

Semgrep always uses the bundled local ruleset in this skill's `assets/` directory. Do not probe `semgrep.dev`; do not run `--config=auto`; and do not attempt registry-backed rule resolution. If a caller still passes `--config=auto`, the bundled runner transparently falls back to the bundled ruleset — Semgrep's auto-config requires telemetry, which conflicts with the mandatory `--metrics=off` posture and fails with `Cannot create auto config when metrics are off` — and records `semgrep_config_fallback: "auto->bundled"` in the evidence so the substitution is auditable.

Preferred command:

```bash
python3 <SKILL_DIR>/scripts/plc_security_scan.py run-sast
```

The bundled runner computes the reviewable file list, scans a temporary tree once with the bundled local ruleset, writes `.plc/security/semgrep.json`, and appends the SAST result to `plc-evidence.jsonl`. Semgrep 1.157.0 supports repeated `--include` patterns, but it does not provide a `--targets-file` option; using a temporary tree avoids `xargs` splitting and keeps one Semgrep JSON artifact per run. By default, one Semgrep invocation may run for up to 600 seconds; use `--timeout-seconds <N>` or `PLC_SEMGREP_TIMEOUT_SECONDS=<N>` to change that. A timeout is recorded as `SKIP`, not `PASS`.

Add scanner-tooling state and the legacy scratch directory to the repo `.gitignore`; the runner does not edit `.gitignore` for you. Do **not** ignore `.plc/` as a whole — `.plc/security/` is deliberately reviewable per *Coverage hygiene and transparency → Temporary scan artifact cleanup* so PR reviewers can browse the artifacts the developer scanned against. `plc-evidence.jsonl` is per-developer local evidence; ignore it in the developer's repo, but CI/shared automation should write to a workspace-owned log via `--evidence <path>` (so the CI path is not ignored).

```gitignore
.plc/tools/
plc-evidence.jsonl
.plc-scan/
```

`plc-evidence.jsonl` is per-developer local evidence. CI or shared automation should use `--evidence <path>` to write to its own workspace-owned log.

The snippet below is the manual equivalent for bundled-rule invocations, used only when the runner is unavailable on this host:

The Semgrep path below pipes Semgrep's `--json` output **directly** through the sanitizer; the raw Semgrep output never lands on disk. The sanitizer keeps only `path`, `start_line`, `end_line`, `check_id`, and `severity`. `extra.lines`, `extra.metavars`, and `extra.message` are all dropped — `lines` and `metavars` carry source excerpts; `message` can interpolate matched metavariables, so a rule firing on a hardcoded credential would re-embed the credential into the artifact.

The sanitized output is buffered to a `mktemp` file and only `mv`'d into the artifact path after the pipeline succeeded and the output passes JSON validation. With no findings, Semgrep still emits a complete JSON envelope with `results: []`, so the sanitizer produces a valid non-empty artifact — there is no zero-byte case under success.

Prepare the bundled rules:

```bash
mkdir -p /tmp/sec-scan-semgrep-rules
tar -xzf <SKILL_DIR>/assets/semgrep-rules.tar.gz -C /tmp/sec-scan-semgrep-rules
find /tmp/sec-scan-semgrep-rules \( -name '._*' -o -name '.DS_Store' \) -exec rm -rf {} +
find /tmp/sec-scan-semgrep-rules -type d -name '__MACOSX' -prune -exec rm -rf {} +
```

Run the pipe-through-sanitizer pattern with pinned Semgrep through `uvx` and the repo-local cache:

```bash
set -o pipefail
mkdir -p .plc/security
ARTIFACT=.plc/security/semgrep.json
SANITIZED_TMP="$(mktemp -t semgrep-sanitized.XXXXXX.json)"
trap 'rm -f "$SANITIZED_TMP"' EXIT
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'semgrep==1.157.0' semgrep scan --config=/tmp/sec-scan-semgrep-rules/semgrep-rules --severity=ERROR --metrics=off --disable-version-check \
    --json <FILE_OR_DIR_PATHS> \
  | python3 -c 'import json, sys
data = json.load(sys.stdin)
out = {"version": data.get("version"), "results": [
    {
        "path": r.get("path"),
        "start_line": (r.get("start") or {}).get("line"),
        "end_line": (r.get("end") or {}).get("line"),
        "check_id": r.get("check_id"),
        "severity": (r.get("extra") or {}).get("severity"),
    } for r in (data.get("results") or [])
]}
print(json.dumps(out))
' > "$SANITIZED_TMP"
then
  if [ ! -s "$SANITIZED_TMP" ] || ! python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$SANITIZED_TMP" >/dev/null 2>&1; then
    printf '{"check":"sast","tool":"semgrep","result":"error","reason":"sanitized semgrep output failed validation (empty or invalid JSON)","timestamp":"%s"}\n' \
      "$TS" > "$ARTIFACT"
    exit 1
  fi
  mv "$SANITIZED_TMP" "$ARTIFACT"
else
  printf '{"check":"sast","tool":"semgrep","result":"error","reason":"semgrep failed or sanitizer pipeline failed","timestamp":"%s"}\n' \
    "$TS" > "$ARTIFACT"
  exit 1
fi
```

The sanitized artifact has everything needed to render top findings as `file:line` in the user/PR summary:

```bash
python3 -c 'import json
data = json.load(open(".plc/security/semgrep.json"))
seen = set()
for r in (data.get("results") or []):
    line = f"{r.get(\"severity\",\"\")}\t{r.get(\"path\",\"\")}:{r.get(\"start_line\",\"\")}\t{r.get(\"check_id\",\"\")}"
    if line not in seen:
        seen.add(line)
        print(line)
' | head -20
```

- Report findings by severity, with `file:line` per finding pulled from the sanitized artifact above.
- Log a one-line summary (counts per severity, artifact path, scope) to `plc-evidence.jsonl` and continue.
- On `SKIP` (project-local Semgrep unavailable, bundled rules missing, timeout, etc.), the snippet above writes a valid skip-stub at `.plc/security/semgrep.json`. The raw Semgrep stream never lands on disk under any code path.

> For **authoritative** SAST coverage under PLC, your program must have a registered **SonarQube** (non‑automotive) or **Coverity** (automotive) project, linked in nSpect and/or enabled via [ScanSpect](https://nvidia.atlassian.net/wiki/spaces/IPP/pages/2860615567) according to [Static Analysis Tools](https://nvidia.atlassian.net/wiki/spaces/PRODSEC/pages/2569245428). This skill’s Semgrep step is an **extra** early‑feedback pass, not a replacement.
- **Dependency vulnerabilities.** Check all dependencies for known CVEs.
    - Tools (local helpers): pip-audit, osv-scanner

> Prefer `python3 <SKILL_DIR>/scripts/plc_security_scan.py run-dependency-vuln` for this check. The shell snippets below are fallback documentation for hosts where the runner cannot run.

The runner detects dependency contexts from changed manifests/lockfiles in the reviewable scope and from changed manifests/lockfiles in the review-excluded scope (vendor/, third_party/, external/, submodules/). SAST review exclusion does not hide dependency contexts: review-excluded contexts are scanned and tagged `colocated_app: true` on the per-context evidence (see *Coverage hygiene and transparency → Colocated standalone third-party apps*). Each scanner runs from the owning package directory. If only application code changed and no manifest/lockfile is in scope, the runner classifies by whether the changed source belongs to a dependency-bearing ecosystem (Python/JS/TS/Ruby/Go/Rust): source-only changes in such an ecosystem record `WARN` with `lockfile_status: "absent"` and a reason naming the gap, not `N/A` (see *Repos without lock files*); changes in other ecosystems or doc-only changes still record `N/A` with `lockfile_status: "absent"`.

Check lockfile consistency before vulnerability scanning. These checks verify that the lockfile matches the manifest; they do not check whether a newly published CVE exists. That is the scanner's job. If a consistency check command is unsupported because the installed package manager is too old, record `SKIP — <tool>-version-too-old for lockfile consistency check` instead of treating the lockfile as inconsistent.

- `uv.lock`: run `uv lock --check` from the directory containing `uv.lock` when supported by the installed uv version. If inconsistent, report `SKIP — uv.lock inconsistent with pyproject.toml; run uv lock` and do not report dependency CVE results against that inconsistent lock.
- `poetry.lock`: run `poetry check --lock` from the directory containing `poetry.lock` when supported by the installed Poetry version. If unsupported, try the version-appropriate Poetry lock check; if no check is available, record `SKIP — poetry-version-too-old for lockfile consistency check`. If inconsistent, report `SKIP — poetry.lock inconsistent with pyproject.toml; run poetry lock`.
- Other lockfiles: use `.plc/tools/bin/osv-scanner` when available, then PATH `osv-scanner` fallback. If neither is available, record `SKIP — osv-scanner unavailable`.

Python scanning rules:

- Use scanner execution in this order: `uvx` with `UV_CACHE_DIR="$REPO/.plc/tools/uv-cache"` when available, then an existing `.plc/tools/python-venv`, then lazy creation/population of that venv with the pinned Python scanners above, then PATH fallback.
- For `uv.lock`, use `osv-scanner --lockfile=uv.lock` after the resolver chooses `.plc/tools/bin` or PATH. Do not run `pip-audit` directly against `uv.lock`.
- For Poetry lockfile projects, the bundled runner uses resolved `osv-scanner --lockfile=poetry.lock` after the Poetry lock consistency check passes.
- For requirements-based projects, run pinned `pip-audit==2.9.0` through project-local `uvx` or `.plc/tools/python-venv` from the requirements directory when available; otherwise use PATH fallback.

When a base ref is available, the runner scans the base manifest/lockfile context too and compares stable dependency finding keys. Findings already present in the base scan are labeled `pre-existing`; findings that cannot be compared are labeled `unknown-baseline` instead of being presented as new work-unit findings. Dependency evidence uses explicit `findings_new`, `findings_pre_existing`, `findings_unknown_baseline`, and `findings_actionable` counts rather than overloading SAST severity fields.

Examples:

```bash
# uv project
cd <DIR_CONTAINING_UV_LOCK>
uv lock --check && "$REPO/.plc/tools/bin/osv-scanner" --lockfile=uv.lock
```

```bash
# Poetry project
cd <DIR_CONTAINING_POETRY_LOCK>
poetry check --lock && "$REPO/.plc/tools/bin/osv-scanner" --lockfile=poetry.lock
```

```bash
# requirements / project-local uvx
cd <DIR_CONTAINING_REQUIREMENTS>
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'pip-audit==2.9.0' pip-audit -r requirements.txt -f json
```

Other ecosystems:

```bash
cd <DIR_CONTAINING_LOCKFILE>
"$REPO/.plc/tools/bin/osv-scanner" --lockfile=<LOCKFILE_NAME>
```

- Report findings by severity. For each finding, surface package, installed version, fixed version (if any), and the CVE/GHSA ID — these are all available in the JSON artifacts above.
- For remediation guidance: `nicc skills pull cve-remediation`.
- Log a one-line summary (counts per severity, artifact path) to `plc-evidence.jsonl` and continue.
- On `SKIP` for any tool (not installed, network unreachable, lockfile missing), write the skip-stub to the corresponding `.plc/security/<tool>.json` so the artifact path always resolves.

> **When a scanner exits non-zero, disambiguate the reason before classifying the result.** `pip-audit` and `osv-scanner` both exit non-zero when they *successfully find vulnerabilities*; non-zero is therefore not by itself an error signal. Classify by inspecting the artifact, in this order:
>
> - **Valid JSON artifact + findings present** → `result=warn`. Non-zero exit here is the tool reporting "I found things"; do not relabel it as `error`.
> - **Valid JSON artifact + zero findings** → `result=pass`.
> - **No artifact / unparseable JSON + stderr** contains "connection", "timeout", "dns", "network", "unreachable", "could not resolve", "name or service": `result=skip, reason="<tool> network unreachable (likely sandbox/network policy); BlackDuck via Pulse/nSpect/ScanSpect remains the authoritative OSS scanner; local SKIP is advisory only"`. The PR evidence table should surface this reason verbatim.
> - **No artifact + the tool binary is missing**: `result=skip, reason="<tool> not installed"`.
> - **No artifact + any other stderr** (e.g., broken lockfile, malformed pyproject): `result=error, reason="<tool error message, with sensitive paths redacted>"`.
>
> The rule is: trust the *artifact* first, exit code second. Non-zero exit + valid JSON = findings, not failure. Never report a passing scan when the scanner actually failed to reach its advisory DB, and never report `error` when the tool successfully classified a non-empty findings list.

> For PLC/MVSB release-gate OSS and license coverage, the **authoritative** scanner is **BlackDuck via Pulse / nSpect / ScanSpect** (see [Pulse Platform](https://confluence.nvidia.com/display/PSTools/Welcome%3A+Pulse+Platform) and [ScanSpect](https://nvidia.atlassian.net/wiki/spaces/IPP/pages/2860615567)). Treat local pip-audit / osv-scanner as fast early detectors, not substitutes.
- **License compliance.** When adding or updating dependencies, check for licenses that require OSRB review: GPL, LGPL, AGPL, SSPL, Commons Clause, or any unknown/custom license.

> The bundled runner does not yet cover this check; follow the shell snippet.

    - Inspect Python package metadata and lock files for declared license fields.
    - Run license tools from the owning package directory, using project-local `uvx` or `.plc/tools/python-venv`. Do not run from the repo root unless the repo root owns the manifest.
    - If tools are available:

```bash
# Python, use project-local scanner tooling. Write pip-licenses' raw output to a
# tempfile, then pipe it through `plc_security_scan.py normalize-licenses` to
# produce `.plc/security/pip-licenses.json` and append the license evidence row.
# The normalizer turns UNKNOWN/empty/null License fields into a WARN row with
# `reason: license_metadata_missing` and writes the per-package metadata block
# (`license`, `license_expression`, top three license `classifiers`) so the
# reviewer can re-classify without re-running.
mkdir -p .plc/security
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
RAW="$(mktemp -t pip-licenses-raw.XXXXXX.json)"
trap 'rm -f "$RAW"' EXIT
if command -v uvx >/dev/null 2>&1; then
  UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'pip-licenses==5.5.5' pip-licenses --format=json --with-license-file --output-file="$RAW"
elif [ -x "$REPO/.plc/tools/python-venv/bin/pip-licenses" ]; then
  "$REPO/.plc/tools/python-venv/bin/pip-licenses" --format=json --with-license-file --output-file="$RAW"
elif [ -x "$REPO/.plc/tools/python-venv/Scripts/pip-licenses.exe" ]; then
  "$REPO/.plc/tools/python-venv/Scripts/pip-licenses.exe" --format=json --with-license-file --output-file="$RAW"
else
  printf '{"check":"license","tool":"pip-licenses","result":"skip","reason":"project-local pip-licenses unavailable","timestamp":"%s"}\n' \
    "$TS" > .plc/security/pip-licenses.json
  RAW=""
fi
if [ -n "$RAW" ]; then
  python3 <SKILL_DIR>/scripts/plc_security_scan.py normalize-licenses --input "$RAW"
fi
```

- On non-compliant or unknown license: the normalizer marks the row `WARN` with `reason: license_metadata_missing` and surfaces the per-package metadata block; flag for OSRB review before release.
- The normalizer logs the one-line summary to `plc-evidence.jsonl` for you (count of missing-metadata packages, total packages, artifact path).
- On `SKIP` (pip-licenses unavailable), the snippet above still writes the skip-stub to `.plc/security/pip-licenses.json` without invoking the normalizer.

> **Authoritative** OSS / license compliance for PLC uses **BlackDuck via nSpect / ScanSpect** and OSRB processes; this skill’s license checks are for fast local feedback only.
>
> When local and authoritative disagree, **follow the authoritative result**:
> - A clean `pip-licenses` run does **not** clear an OSRB review. If a dependency hits the OSRB list (GPL, LGPL, AGPL, SSPL, Commons Clause, unknown/custom), it still needs OSRB.
> - A local flag must **not** be auto-suppressed because BlackDuck happened to pass on an earlier run; flag it for human review and let BlackDuck/OSRB decide.
> - Never write evidence or PR copy that says "BlackDuck-clear" or "OSRB-clear" based on local-only output.
- **Container security** (if applicable).

When modifying a Dockerfile or container build file:

> Release-gate container scanning is done with **Anchore** via the Pulse Container Scanner, typically orchestrated by **ScanSpect** and visible via nSpect / security dashboards. No local container scanner is configured in this skill — rely on the pipeline scanners.

- **Security code review** (if applicable).

When writing or significantly modifying security-sensitive logic — auth, crypto, input validation, privilege escalation, session management, data access — review the changed code for:

- Unsanitized user input passed to shell, SQL, eval, or subprocess calls
- Missing auth checks, broken access controls, insecure defaults
- Weak algorithms (MD5, SHA1, DES), hardcoded keys or IVs, improper use of random
- Missing bounds checks, path traversal, deserialization of untrusted data
- Credentials or tokens in logs, error messages, or API responses
- Operations running at higher privilege than required

For each finding: report `file:line`, issue, severity (High / Medium / Low), and a concrete fix.

Log and continue.

---
## Before Submitting PR

Generate the evidence summary in the PR description by filtering `plc-evidence.jsonl` to the **current branch only**. Entries from other branches are historical and must not be summarized here — they pollute the report after merge-to-main.

Preferred command:

```bash
python3 <SKILL_DIR>/scripts/plc_security_scan.py summary
```

To scope correctly, first capture the current branch:

```bash
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
```

Then read the evidence log, keep only lines where `branch == "$CURRENT_BRANCH"`, and of those, keep the **most recent entry per `(check, tool)` pair** (last-write-wins per tool — you want the latest state of each scanner, not every historical attempt). Then **group those entries by `check`** when rendering the human table — categories that fire multiple tools (Dependencies → `pip-audit` + `osv-scanner`) become a single PR-table row that aggregates their per-tool statuses by the severity precedence in *Rules for the table* below.

Keeping only the latest per `check` (dropping the `tool`) is wrong: it silently discards one of the tools in a multi-tool category and the PR table loses an artifact path.

Render as:

```
## PLC Local Security Evidence (Advisory)
Branch: feature/my-change · HEAD: abc1234

Local advisory checks only. Authoritative release gates remain Pulse, SonarQube, Coverity, BlackDuck, nSpect, ScanSpect, OSRB, and Anchore — none of the rows below are release-gate results.

Detailed local artifacts remain in `.plc/security/`, but the PR table and `plc-evidence.jsonl` should stay compact.

| Check | Status | Tool | Details |
|-------|--------|------|---------|
| Secrets | PASS | Pulse Secret Scanner | 0 verified findings (local advisory; not a release-gate pass) |
| SAST | WARN | Semgrep | 2 high, 3 medium |
| Dependencies | PASS | pip-audit / osv-scanner | 0 critical, 0 high (local advisory; not a release-gate pass) |
| License | SKIP | — | pip-licenses not installed |
| Container | N/A | — | No container in this change |
| Security Review | PASS | inline | 0 high findings |
```

Rules for the table:

- **One row per category.** Categories that fire multiple tools (Dependencies → `pip-audit` + `osv-scanner`) collapse into a single row. Per-tool history lives in `plc-evidence.jsonl` as multiple lines; the table is the human view.
- **Category status** — aggregate the per-tool statuses with severity precedence `error` > `warn` > `skip` > `pass`. If tools in the category differ, the row shows the highest-severity status and `Details` notes the others (e.g., "pip-audit PASS · osv-scanner SKIP — not installed").
- **Status values** — `PASS` / `WARN` / `SKIP` / `N/A` (see definitions below).
- **Details** — must answer *what ran and what it found* in one line using aggregate counts only. If the reviewer needs per-finding detail, point them to `.plc/security/` in prose outside the evidence log.
- **Why a check skipped** — the artifact's `reason` field is canonical. Mirror it in `Details` so it's visible without opening the file (e.g., "SKIP — pip-licenses not installed", "SKIP — osv-scanner network unreachable").

Status values (local advisory only — none are release-gate results):

- `PASS` — the local check ran and found no significant findings. **Not** a release-gate pass; do not relabel it as one anywhere in the PR description or commit messages.
- `WARN` — the local check ran and reported findings the developer should review before pushing.
- `SKIP` — the tool was unavailable, blocked by network policy, or the check is not enabled.
- `N/A` — not applicable to this change (e.g., no container modified, no dependencies added).

> The setup verifier (`verify-setup.sh`) uses a four-state vocab `PASS/FAIL/WARN/SKIP` where `FAIL` means setup could not be confirmed at all. Per-check evidence rows in this skill use `PASS/WARN/SKIP/N/A` (no `FAIL`); a scanner that ran but reported nothing is `PASS`, a scanner that was unable to run is `SKIP`.

---
## Evidence Collection

Every check appends a line to `plc-evidence.jsonl` in the repo root. Every line includes the current **branch** and **commit SHA** so entries can be correctly attributed and filtered at PR time and after merge.

In multi-repo workspace mode, the bundled runner also carries workspace attribution from `agentic_plc/workspace.json` or `AGENTIC_PLC_*` environment variables. Any scanner evidence or finding that points into a repo must include `workspace_id` or a stable manifest-relative/caller-supplied `workspace_root`, `repo_id` or sanitized `repo_url`, `repo_revision`, repo-relative `path` when applicable, and `ref` or `finding_id` when the row represents a requirement/finding-specific record.

The bundled SAST runner appends the Semgrep entry automatically:

```bash
python3 <SKILL_DIR>/scripts/plc_security_scan.py run-sast
```

Capture both before running any check:

```bash
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
COMMIT="$(git rev-parse HEAD)"
```

**One JSONL line per tool invocation, not per category.** When a category covers more than one tool (e.g., Dependencies → `pip-audit` *and* `osv-scanner`), each tool gets its own compact line. The PR table renders one row per category by grouping the latest line per `tool` and merging their statuses (`error` > `warn` > `skip` > `pass`).

Example entries:

```json
{"pillar":"CODING","check":"secret-scan","tool":"pulse-secret-scanner","branch":"feature/auth-fix","commit":"abc1234def5678901234567890abcdef12345678","result":"pass","findings_verified":0,"timestamp":"2026-04-23T15:30:00Z"}
{"pillar":"CODING","check":"sast","tool":"semgrep","branch":"feature/auth-fix","commit":"abc1234def5678901234567890abcdef12345678","result":"warn","findings_high":2,"findings_medium":3,"findings_low":0,"timestamp":"2026-04-23T15:30:05Z"}
{"pillar":"CODING","check":"dependency-vuln","tool":"pip-audit","branch":"feature/auth-fix","commit":"abc1234def5678901234567890abcdef12345678","result":"pass","findings_new":0,"findings_pre_existing":0,"findings_unknown_baseline":0,"dependency_context_count":1,"dependency_contexts_scanned":1,"dependency_contexts_skipped":0,"timestamp":"2026-04-23T15:30:08Z"}
{"pillar":"CODING","check":"dependency-vuln","tool":"osv-scanner","branch":"feature/auth-fix","commit":"abc1234def5678901234567890abcdef12345678","result":"skip","reason":"osv-scanner not installed","dependency_context_count":1,"dependency_contexts_scanned":0,"dependency_contexts_skipped":1,"timestamp":"2026-04-23T15:30:10Z"}
{"pillar":"CODING","check":"sast","tool":"semgrep","branch":"feature/auth-fix","commit":"def5678901234567890abcdef1234567890abcd","result":"warn","findings_high":1,"findings_medium":0,"findings_low":0,"repo_id":"runtime","repo_revision":"def5678901234567890abcdef1234567890abcd","repo_url":"https://gitlab.example.com/team/runtime.git","timestamp":"2026-04-23T15:30:12Z","workspace_id":"sample-rate-limit-workspace"}
```

In the example above, the Dependencies row in the PR table aggregates the `pip-audit` (`pass`) and `osv-scanner` (`skip`) lines into one row. Under the `error > warn > skip > pass` precedence defined in *Before Submitting PR*, the row is `SKIP`; `Details` summarizes both (e.g., "pip-audit PASS · osv-scanner SKIP — not installed").

**Schema guarantees:**
- `check` — the category identifier (`secret-scan`, `sast`, `dependency-vuln`, `license`, etc.). Stable across tool changes; multiple JSONL lines per category are allowed.
- `tool` — the specific scanner binary (`pip-audit`, `osv-scanner`, etc.). The `(check, tool)` pair is unique per scan run.
- `branch` — the Git branch the scan ran on, or `p4/<client>` in a Perforce workspace. If Git HEAD is detached, use the commit SHA prefixed with `detached/`.
- `commit` — the full SHA of Git HEAD at scan time, or `p4/<client>` in a Perforce workspace. Never abbreviate Git SHAs in the log; abbreviate only for display.
- `timestamp` — ISO-8601 UTC.
- Aggregate finding counts — severity counts for SAST/secrets/license, or dependency counts such as `findings_new`, `findings_pre_existing`, `findings_unknown_baseline`, `dependency_context_count`, `dependency_contexts_scanned`, and `dependency_contexts_skipped`.
- `reason` — required when `result` is `skip` or `error`; use a short reason suitable for a PR evidence table.
- `workspace_id` or stable `workspace_root`, `repo_id` or sanitized `repo_url`, `repo_revision` — required for repo-pointing records in workspace mode. These fields disambiguate one SRD/SADD mapped to multiple repos and keep the evidence shape stable for future ledger ingestion. Prefer `workspace_id`; do not emit local host paths by default.

This file is append-only local developer state. Do not edit it manually and do not commit it. Post-merge, entries from feature branches may remain in a local log as historical record but are filtered out of the main-branch PR view by the rule above.

Never include secret values, tokens, credentials, or **any raw source snippet that could embed one** — in the evidence log, in agent output, in the artifact files under `.plc/security/`, or in the PR description. In `plc-evidence.jsonl`, log invocation identity, status, reasons, and aggregate counts only.

Two specific guarantees:

- Secret-scan artifacts are produced through a Python `del(Raw)` redactor so the `Raw` field never reaches disk.
- Semgrep artifacts are produced through a Python projection that keeps only `path`, `start_line`, `end_line`, `check_id`, and `severity`. `extra.lines`, `extra.metavars`, and `extra.message` are all dropped. `lines` and `metavars` carry the matched source line — which, when Semgrep flags a hardcoded credential, *is* the credential. `message` can interpolate matched metavariables into the message string, so the same leak path applies. The PR summary uses `file:line check_id` to render top findings; that is enough for actionability without re-embedding source.

Skip-vs-error follows the contract in *Scan artifacts directory* rule 3: a missing sanitizer (e.g., `python3` itself absent — already a hard prereq) → `skip` stub; a scanner that crashed, exited non-zero unexpectedly, or emitted invalid/empty sanitized JSON → `error` stub. Either way, the unsanitized output never reaches `.plc/security/`.

---
## Available Skills

| Skill | Status | When invoked | What it does |
| --- | --- | --- | --- |
| `nspect-scan-remediation` | Available | Pulse finds unverified secret findings | Triage findings, generate allowlist entries |
| `cve-remediation` | Available | CVEs found in dependencies | Fix CVEs across Python and multi-ecosystem lockfiles |
| `adversarial-security-review` | Available | On demand — high-risk components | Zero-trust security audit with risk rating |


---
## Scanner Reference

This table maps the **local, advisory** tools in this skill to the **authoritative** IPP / ProdSec stack (nSpect + Pulse + ScanSpect).

| Category | Local (advisory) | CI/CD (authoritative: PLC / MVSB) |
| --- | --- | --- |
| Secrets | Pulse Secret Scanner Docker image (TruffleHog engine; image is distributed from the same source as the release pipeline, advisory locally — see authority policy above). No fallback secret scanner is enabled. | Pulse Secret Scanner in nSpect / [ScanSpect](https://nvidia.atlassian.net/wiki/spaces/IPP/pages/2860615567) pipelines, with the pipeline policy and allowlist applied |
| SAST | Semgrep (local helper; bundled local rules, advisory only) | SonarQube (non‑automotive) / Coverity (automotive) via nSpect / ScanSpect, per [Static Analysis Tools](https://nvidia.atlassian.net/wiki/spaces/PRODSEC/pages/2569245428) |
| OSS Vulnerabilities | pip-audit, osv-scanner (local helpers) | BlackDuck via Pulse / nSpect / ScanSpect |
| License / OSRB | pip-licenses (local helper; never overrides OSRB) | BlackDuck via Pulse / nSpect / ScanSpect + OSRB workflows |
| Container | — (rely on pipeline) | Anchore via Pulse Container Scanner (ScanSpect / nSpect) |


Local tools should **never** be used as the only justification for accepting risk; they are there to keep your diffs clean **before** the central scanners run. No additional fallback scanners (TruffleHog CLI, gitleaks, alternative SAST engines) are enabled by this skill — if a developer chooses to run one ad hoc, the evidence row must say `advisory, non-canonical` and must not displace the authoritative pipeline result.

---
## IPP / ProdSec Reference

For full alignment with NVIDIA internal guidance:

- **nSpect program registration and dashboards**: see [nSpect](https://nvidia.atlassian.net/wiki/spaces/PRODSEC/pages/2569241861).
- **In-pipeline ScanSpect CI/CD integration and onboarding**:
    - [ScanSpect: IPP's In-pipeline Security Scanning Framework](https://nvidia.atlassian.net/wiki/spaces/IPP/pages/2860615567)
    - [Introduction to ScanSpect (In-pipeline Security Scanning)](https://nvidia.atlassian.net/wiki/spaces/IPP/pages/3163217623)
- **Pulse scanning platform and scanner catalog**: [Welcome: Pulse Platform](https://confluence.nvidia.com/display/PSTools/Welcome%3A+Pulse+Platform).
- **SAST configuration and policies**: [Static Analysis Tools](https://nvidia.atlassian.net/wiki/spaces/PRODSEC/pages/2569245428).

This skill is intentionally aligned with those references: local steps mirror the same categories and engines but are always **secondary** to the centrally qualified scanning services.

## Completion Event

At the end of the work-unit scan, record a completion event when `aplc` is available. Use `PASS` when requested local checks ran with no actionable findings, `WARN` when findings or degraded coverage remain, `SKIP` with `skip_reason` when local scanner prerequisites are unavailable, and `ERROR` with `error_reason` for unexpected runner failures. Keep `summary` to aggregate counts only.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"coding","check":"plc-security-scan","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0,"findings_high":0}}
JSON
```

```powershell
@'
{"pillar":"coding","check":"plc-security-scan","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory","summary":{"findings_total":0,"findings_high":0}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```
