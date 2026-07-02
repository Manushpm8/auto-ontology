---
name: setup-agentic-plc
description: >-
  Sets up Agentic PLC on the developer's machine: installs tooling,
  authenticates services, and verifies end-to-end. Invoke when the user says
  "install Agentic PLC", "upgrade Agentic PLC", "setup PLC", or "check my
  PLC environment". Idempotent — a second run on a healthy machine is a
  near-no-op. Consent-first — never install or authenticate without asking
  the user, and log every decision to `~/.agentic-plc/plc-setup-log.jsonl`
  with a `repo` field so entries are scoped per-project.
---

# Agentic PLC — Setup

## Mandatory Preflight: Record Invocation

First, emit a telemetry usage event. Pick the command for your shell:

```bash
cat <<'JSON' | bash scripts/aplc.sh publish --event-json @- --repo-root .
{"pillar":"coding","check":"plc-skill-invocation","tool":"setup-agentic-plc","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"setup-agentic-plc"}}
JSON
```

```powershell
@'
{"pillar":"coding","check":"plc-skill-invocation","tool":"setup-agentic-plc","status":"PASS","runtime_decision":"allow","authority":"advisory","summary":{"event_type":"skill_invocation","skill":"setup-agentic-plc"}}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 publish --event-json '@-' --repo-root .
```

> **Advisory-only scope.** Local scans installed by this setup are advisory only. See `plc-security-scan/SKILL.md` → *Local scanner authority and fallback policy* for what local scan output may and may not claim, and what wording requires owner approval.

You are the agent setting up Agentic PLC. Walk this checklist top-to-bottom. For each row in the tables below, do three things:

1. **Check** whether the item is already satisfied (run the gate command).
2. If not satisfied, **ask the user** with the exact proposed command. Wait for their answer. Do not consent on their behalf unless the user has opted into non-interactive mode (see below).
3. On success, decline, or failure, **log one JSONL line** to `~/.agentic-plc/plc-setup-log.jsonl` using the format at the bottom of this file.

At the end, run `scripts/verify_setup.py` from this skill's directory to confirm local scanner readiness.

Never print secret values. Never re-prompt for anything the log shows the user already declined.

## Optional Superpowers recommendation

Recommend Superpowers to the user, but do not require it and do not block Agentic PLC setup if it is skipped or unavailable.

Advantage: Agentic PLC owns PLC-specific standards, requirements/design traceability, security-scan handoff, and advisory-output rules. Superpowers adds workflow discipline for planning, TDD, parallel work, debugging, review, verification, and finishing branches around those PLC checks.

Install guidance:

- Claude Code: `/plugin install superpowers@claude-plugins-official`
- Cursor Agent chat: `/add-plugin superpowers`
- Codex: find `superpowers` in `/plugins`

If the plugin cannot be found in the agent/plugin marketplace, install it from https://github.com/obra/superpowers/tree/v5.1.0.

After installation, trigger the `using-superpowers` skill to enable Superpowers in the session.

### Non-interactive mode

If the user opts in explicitly — phrasings include:

- *"install Agentic PLC without asking me"*
- *"setup Agentic PLC non-interactively"*
- *"--yes"* / *"just do it"* / *"don't ask me for every step"*
- *"I'm in bypass permission mode"*

— then proceed without the consent prompts in step 2 above, but continue to log every decision to `~/.agentic-plc/plc-setup-log.jsonl`. Logging is non-negotiable; consent is skippable.

Use non-interactive mode when appropriate:
- Developer on their own trusted machine who wants to blast through setup.
- Sandbox / CI / service-agent contexts where no human is attached to the session.
- Re-runs where the user already answered the same prompts recently.

Even in non-interactive mode, **do not bypass authentication flows that genuinely need the user** (e.g., pasting a GitLab PAT for the Docker registry). If such a step is unavoidable and no env-var escape hatch applies, stop and tell the user what you need.

---

## 1. Required local scanner setup

These tools are enough to run local Agentic PLC scans. Treat missing scanners as degraded scan coverage.

| Tool | Gate (check this first) | Install command | Purpose |
|---|---|---|---|
| Docker | `docker --version && docker info` | Prefer the managed corporate Docker Desktop flow for this platform. Do not install Docker with a privileged shell command from this skill. | Pulse secret scanner; registry auth below is required only for Pulse secret scanning. Python scanners and `osv-scanner` work without it. |
| uv / uvx | `uvx --version` or `uv --version` | Optional. If unavailable, use the project-local Python venv fallback below. Do not install uv with a shell-piped network installer unless the user provides an approved internal artifact or mirror. | Fast isolated Python scanner execution |
| Python scanners | See project-local scanner bootstrap below | See project-local scanner bootstrap below | Semgrep SAST, pip-audit CVE scanning, pip-licenses Python license checks |
| osv-scanner | `.plc/tools/bin/osv-scanner --version` or `.plc/tools/bin/osv-scanner.exe --version` | See project-local scanner bootstrap below | Multi-ecosystem CVE scanning |

The bundled dependency runner is `plc_security_scan.py run-dependency-vuln`. It checks lockfile freshness before vulnerability scanning, uses `.plc/tools/bin/osv-scanner` before PATH fallback for lockfiles such as `uv.lock` and `poetry.lock`, and uses `uvx`, `.plc/tools/python-venv`, or PATH fallback for requirements scans.

### Project-local scanner bootstrap

Scanner tooling is repo-scoped. Use `.plc/tools` as the only Agentic PLC scanner tool state directory for this repo:

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

Do not add scanner tools to project dependencies. Do not edit `pyproject.toml`, `poetry.lock`, `uv.lock`, `requirements*.txt`, or language lockfiles solely to run Agentic PLC scanners. Do not use user-level or platform-level install targets for scanners.

Repo-mode Agentic PLC install also places the `aplc` telemetry CLI in this same `.plc/tools/python-venv` using `uv venv` and `uv pip install` when `uv` is available. Do not create a separate APLC-specific venv. User-mode install does not add telemetry hooks.

When `uvx` is available, use it with exact versions and the repo-local cache:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'semgrep==1.157.0' semgrep --version
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'pip-audit==2.9.0' pip-audit --version
UV_CACHE_DIR="$REPO/.plc/tools/uv-cache" uvx --from 'pip-licenses==5.5.5' pip-licenses --version
```

These commands work the same way on Windows, WSL, macOS, and Linux when `uv` is installed for the active shell. They create no project dependency entries and no persistent global tool shims.

If `uvx` is unavailable, use a project-local venv fallback:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
python3 -m venv "$REPO/.plc/tools/python-venv"
"$REPO/.plc/tools/python-venv/bin/python" -m pip install \
  'semgrep==1.157.0' \
  'pip-audit==2.9.0' \
  'pip-licenses==5.5.5'
```

On Windows (native PowerShell), use the same `python -m venv` — it creates the directory tree itself, so no `New-Item` is needed — then install with the venv's `Scripts\python.exe`:

```powershell
$Repo = (git rev-parse --show-toplevel 2>$null); if (-not $Repo) { $Repo = (Get-Location).Path }
$VenvDir = Join-Path $Repo ".plc\tools\python-venv"
py -3 -m venv $VenvDir
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install `
  'semgrep==1.157.0' `
  'pip-audit==2.9.0' `
  'pip-licenses==5.5.5'
```

The only platform difference is the venv interpreter path: `bin/python` on macOS/Linux/WSL, `Scripts\python.exe` on Windows. Everything else (`python -m venv`, the pinned pip install) is identical. Prefer driving file creation and JSON writes through Python rather than PowerShell file-write cmdlets (`Out-File`, `New-Item`), which can add a UTF-8 BOM and diverge across shells.

For `osv-scanner v2.3.5`, install only to `.plc/tools/bin/osv-scanner` or `.plc/tools/bin/osv-scanner.exe`. Use an already cached binary from `.plc/tools/cache` only when its pinned checksum matches; otherwise download the pinned binary from the official Google OSV Scanner GitHub release and verify its pinned checksum before marking it usable. Never write scanner binaries to a global directory. If the current OS/architecture is unsupported or the official release download is unreachable, log a SKIP for `osv-scanner` with that specific reason.

Official pinned source: `https://github.com/google/osv-scanner/releases/download/v2.3.5/`.

Pinned checksums:

| Platform | Asset | SHA256 |
|---|---|---|
| macOS x86_64 | `osv-scanner_darwin_amd64` | `3b1c72d59dcbad99fa4eb2c72bf2e82017f83e0268340e4b00af76a1fea32c85` |
| macOS arm64 | `osv-scanner_darwin_arm64` | `b740efe0b08fb817865e818a498997d5f042f14b8eeafb6393176ce84dd09cf6` |
| Linux x86_64 | `osv-scanner_linux_amd64` | `bb30c580afe5e757d3e959f4afd08a4795ea505ef84c46962b9a738aa573b41b` |
| Linux arm64 | `osv-scanner_linux_arm64` | `fa46ad2b3954db5d5335303d45de921613393285d9a93c140b63b40e35e9ce50` |
| Windows x86_64 | `osv-scanner_windows_amd64.exe` | `b165d33c08bda663119a459f5187e096d2525f888503495f5f34925741e981a2` |
| Windows arm64 | `osv-scanner_windows_arm64.exe` | `a3d2ffa712fd2376e88a02c98b917a87885c761d0d21980b8f5eabfb910cbcb8` |

Pick the right command for your shell:

- **macOS, Linux, or WSL (inside the WSL shell):** use the bash command below. Inside WSL `uname -s` returns `Linux` and the Linux `osv-scanner` binary is the correct asset — do **not** install the Windows `.exe` under `/mnt/c/...` and try to run it from the WSL shell.
- **Windows (native PowerShell, no WSL):** use the PowerShell command further down.
- **Windows with WSL where the repo lives on the Windows filesystem (`/mnt/c/...`):** still use the bash command from inside the WSL shell; it installs the Linux binary into `.plc/tools/bin/osv-scanner` relative to the repo root. Running the WSL-installed binary from PowerShell will fail; run all scans from the same shell that installed the binary.

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

`plc-security-scan` uses this same bootstrap lazily for Python scanners. If the user forgot to run `setup-agentic-plc`, the scan runner first tries project-local `uvx`, then an existing `.plc/tools/python-venv`, then creates/populates that venv with the pinned Python scanners above, then falls back to PATH if isolated setup is unavailable. Native `osv-scanner` is resolved from `.plc/tools/bin` first, then PATH.

Semgrep setup and scan execution always use the bundled local ruleset. Setup verification uses a single bundled smoke rule from that archive to keep the smoke test fast. Do not probe `semgrep.dev` or run `--config=auto`.

For backward compatibility, do not remove or modify scanners installed by old Agentic PLC releases. Prefer `.plc/tools`; use PATH fallback only when project-local tools are unavailable.

### Docker registry auth for Pulse

Pulse runs from NVIDIA GitLab's private container registry. If Docker is installed and the user wants Pulse secret scanning, make the registry login explicit:

| Service | Gate | Login command |
|---|---|---|
| Docker registry for Pulse (`gitlab-master.nvidia.com:5005`) | `docker image inspect gitlab-master.nvidia.com:5005/pstooling/pulse-group/pulse-secret-scanner:1.70 >/dev/null 2>&1 \|\| docker pull --quiet gitlab-master.nvidia.com:5005/pstooling/pulse-group/pulse-secret-scanner:1.70 >/dev/null` (first-time pull may take a few seconds) | `docker login gitlab-master.nvidia.com:5005` — use a GitLab PAT with `read_registry` scope from https://gitlab-master.nvidia.com/-/profile/personal_access_tokens |

`write_registry` is not required for pulling the Pulse scanner image.

### Side effects of declining

- Docker not installed → `SKIP — docker not installed`. Pulse secret scanning skipped; `plc-security-scan` evidence table reflects the same reason.
- Docker installed but daemon stopped → `WARN — start Docker Desktop to enable Pulse secret scanning`. Pulse secret scanning skipped until the daemon is running.
- Docker registry login declined → `SKIP — pulse registry auth declined`. Pulse secret scanning skipped even if Docker is installed; `plc-security-scan` evidence table shows the same reason.
- Project-local Python scanner bootstrap declined or blocked → Semgrep, pip-audit, and pip-licenses local advisory checks show `SKIP — project-local scanner bootstrap unavailable`.
- osv-scanner declined or unavailable → multi-ecosystem CVE scanning shows `SKIP — osv-scanner unavailable`.

Surviving scans (those not declined) remain advisory regardless of which gates were declined.

---

## 2. Optional NICC and MCP setup (skip by default)

Local Agentic PLC scans do not need NICC or any MCP server. Skip this entire section by default. Only walk it when the user has stated a specific external-system need that one of the PLC skills below would otherwise have to work around.

### When MCP might help

Two PLC skills publish explicit MCP tool calls today. Those calls give setup a concrete starting point — but the registry slug NICC uses for each server, and the display name that appears in Claude / Cursor / Codex, vary by NICC release and per install. The tool calls are exact; the server names are intentionally descriptive:

| Skill | External document source | Tool call the skill makes | MCP server in your harness |
|---|---|---|---|
| `plc-requirements-validation` | SRD in Confluence | `confluence_get_page(page_id=...)` | the Confluence MCP server |
| `plc-requirements-validation` | SRD in Google Drive or SharePoint | `glean_get_file(url=..., system="gdrive"\|"sharepoint")` | the Glean MCP server |
| `plc-design-validation` | SDD/SADD in Confluence | `confluence_get_page(page_id=...)` | the Confluence MCP server |
| `plc-design-validation` | SDD/SADD in Google Drive or SharePoint | `glean_get_file(url=..., system="gdrive"\|"sharepoint")` | the Glean MCP server |

Discover the actual target name from the installed NICC release before running any registration command — see the next subsection. Do not hard-code an exact server name from this skill; the harness-visible display strings (whatever your `/mcp` list shows) are not guaranteed to be the same as NICC's registration targets.

Other PLC skills may also ask for an MCP server when their work points at a specific external system — for example, `plc-requirements-authoring` mentions a Jama MCP server when the team manages requirements in Jama; `plc-design-authoring` mentions Google Drive / Glean, Confluence, or Perforce MCP servers when the SDD lives in one of those; `plc-test-plan-authoring` mentions TestRail / Jenkins / Jama MCP servers when the team uses those. Do **not** pre-register any of those at setup time. Let each skill ask only when (and only when) the user names a concrete source.

Both validation skills above also accept a PDF, a paste, or a local path. MCP is the convenience path, not the only path. If the user already has the source documents on disk, do not register any MCP server.

### Selective registration (do not register everything)

Do **not** run `nicc mcp --target all`. Registering the full MaaS set drops servers into Claude/Cursor/Codex that the user has not authenticated; those servers then surface as persistent unauthenticated/failed-startup notifications inside every harness session, which is the exact startup noise this section exists to avoid.

For each MCP server the user explicitly asks for:

1. Confirm which specific server (the Confluence MCP server, the Glean MCP server, or one of the authoring-skill servers above) — never assume "all of them".
2. Discover the narrow target name from the installed NICC release before running anything — try `nicc mcp list` or `nicc mcp --help`. If neither exposes a single-server target value, stop and tell the user; do not silently fan out to every MaaS server on their behalf.
3. Run the registration command for just that one server, using the target name you discovered in step 2.
4. Tell the user they still need to authenticate it inside the harness — Claude Code: run `/mcp` in the session, pick the server, complete the auth flow; Cursor/Codex: the equivalent in-agent command. Tokens persist across sessions; this is one-time per harness.
5. Log a `mcp_<slug>` entry (e.g., `mcp_confluence`, `mcp_glean`) per the log format below. The slug is a short, stable, lowercase internal step key — pick a fixed token per server so future log queries are deterministic. It is **not** the NICC target name and does **not** need to match the harness display name. Reflect declines so a future run does not re-prompt.

### Side effects of declining

- MCP not registered → `plc-requirements-validation` and `plc-design-validation` simply ask the user for a PDF, a paste, or a local path instead of reaching out to Confluence / Google Drive / SharePoint. No local scan is affected. Authoring/test-plan skills will ask for an external-system MCP server later only when their own work points at one; until they do, leave those unregistered.
- MCP registered but not authenticated in the harness → harness startup noise (unauthenticated-server warnings) and the affected PLC skill reports a clear "missing MCP" reason at fetch time. The fix is either to authenticate it (`/mcp`) or to unregister it.

### Optional NICC CLI

The NICC CLI is only needed if the user opts into the MCP work above (or wants NVCARPS helper skills, which are out of scope for this skill).

| Tool | Gate (check this first) | Install command | Purpose |
|---|---|---|---|
| NICC CLI | `nicc --version` | `curl -fsSL "https://gitlab-master.nvidia.com/api/v4/projects/245272/packages/generic/nicc-cli/latest/install/install.sh" \| sh -s -- --base-url "https://gitlab-master.nvidia.com/api/v4/projects/245272/packages/generic/nicc-cli/latest"` | Optional — required only for the selective MCP registration described above |

---

## 3. Verify

After everything above, run the one-shot verification script from this skill's directory. Prefer the Python verifier — it behaves identically on Windows (native PowerShell), macOS, Linux, and WSL and does not depend on bash, `tar`, `find`, or `grep`:

```bash
python <path-to-this-skill>/scripts/verify_setup.py
```

On Windows use `python` or `py`; on macOS/Linux use `python3` if `python` is unmapped. `verify_setup.py` is the canonical verifier on every platform. (The original bash verifier is retained only as `scripts/verify-setup.sh.legacy` for reference — Agentic PLC already requires Python everywhere, so there is no Python-free path that would need it.)

It runs: Pulse image check, project-local Semgrep smoke scan with one bundled rule through `uvx` or `.plc/tools/python-venv`, and project-local `osv-scanner` version check. Prints one PASS/FAIL/WARN/SKIP line per check.

Exit codes:

- `0` — no hard failures (degraded coverage allowed; review SKIP/WARN lines above).
- `1` — smoke fixture failed or an unexpected verifier internal error.

Network reachability failures do not cause exit `1`; they are reported as `WARN`/`SKIP` with `degraded` accounting. If local scanners are missing, the verifier exits `0` with a degraded-coverage summary and the scan skills report `SKIP` for the affected checks. CI consumers that want to gate on "no degraded coverage" should grep for the literal string `degraded local scanner coverage` in stdout.

Relay any FAIL or WARN lines to the user with a concrete next step (most point at a specific decline or expired auth in the log).

---

## Setup log format

Append one JSON line per decision to `~/.agentic-plc/plc-setup-log.jsonl`. Every entry includes a `repo` field so decisions from different projects don't collide. Use the bundled cross-platform helper — it resolves the repo automatically and writes plain UTF-8 with no BOM, so it works identically from PowerShell, bash, or zsh:

```bash
# Invoke with `python` on Windows, `python3` on macOS/Linux if `python` is unmapped.
# With a side effect (declines/skips/errors):
python <path-to-this-skill>/scripts/plc_setup_log.py --step <step> --status <status> --side-effect "<side-effect>"
# No side effect (typical for ok): omit the flag entirely — do NOT pass --side-effect ""
python <path-to-this-skill>/scripts/plc_setup_log.py --step <step> --status ok
```

`--status` is one of `ok | declined | error | skip`. For `ok` rows with no side effect, omit `--side-effect` rather than passing an empty `""` — native Windows PowerShell drops empty-string arguments before they reach the script. Do not hand-write this JSONL with PowerShell file-write cmdlets like `Out-File` (they add a UTF-8 BOM) or with shell `printf` interpolation (fragile quoting); the helper is the uniform path. A bash `printf >> ~/.agentic-plc/plc-setup-log.jsonl` one-liner is still acceptable on macOS/Linux/WSL if Python is unavailable, but never on native Windows.

Where:
- `<repo>` is the absolute path to the git repo root, or `_global` if not inside a git repo.
- `<step>` is one of the gate-column identifiers (e.g. `docker`, `docker_login`, `python_scanners`, `osv-scanner`, `nicc`, `mcp_confluence`, `mcp_glean`). Per-MCP-server steps use a short lowercase `mcp_<slug>` form so declines are recorded server-by-server; the slug is an internal log key, not the NICC target name and not the harness display name.
- `<status>` is `ok` | `declined` | `error` | `skip`.
- `<side-effect>` is free text describing what scan behavior degrades — empty string for `ok`.

Readers of this log: PLC skills such as `plc-security-scan`, `plc-requirements-validation`, `plc-design-validation`, and `plc-v-model` consult it at scan time and propagate `SKIP` reasons honestly.

Queries you'll run when the user asks "why is PLC skipping X". Always scope by the current repo (falls back to `_global` entries for machine-wide decisions):

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || echo '_global')"

# Most recent status for a specific step in this repo or global
grep '"step":"<step-name>"' ~/.agentic-plc/plc-setup-log.jsonl \
  | grep -E "\"repo\":\"($REPO|_global)\"" | tail -1

# All current declines for this repo
grep '"status":"declined"' ~/.agentic-plc/plc-setup-log.jsonl \
  | grep -E "\"repo\":\"($REPO|_global)\"" | sort -u
```

---

## Scope boundaries

- This skill does not place its own files. File placement is done by the packaged installer at the top of the release artifact, invoked by you when the user says "install" or "upgrade". The installer is `install.sh` on macOS/Linux and `install.ps1` on Windows; the bootstrap (`bootstrap.sh` / `bootstrap.ps1`) downloads, verifies, and unpacks the release, then runs it. File placement is a prerequisite — by the time this skill runs, the files are already on disk.
- This skill does not uninstall anything. If the user wants to remove Agentic PLC, direct them to delete the skill directories and clean the `## Agentic PLC` block from their rule files manually (out of scope for this release).
- This skill does not modify the `## Agentic PLC` block in `CLAUDE.md`/`AGENTS.md` — the release installer owns that.

## Completion Event

At the end of setup or verification, record a completion event when `aplc` is available. Use `PASS` when setup checks pass, `WARN` when optional integrations or local scanner coverage are degraded, `SKIP` with `skip_reason` when setup was not run, and `ERROR` with `error_reason` for unexpected failures. Keep the event compact and omit artifact paths.

```bash
cat <<'JSON' | bash scripts/aplc.sh record --event-json @- --evidence plc-evidence.jsonl --repo-root .
{"pillar":"coding","check":"setup-agentic-plc","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory"}
JSON
```

```powershell
@'
{"pillar":"coding","check":"setup-agentic-plc","tool":"agentic-plc-skill","status":"WARN","runtime_decision":"warn","authority":"advisory"}
'@ | powershell -ExecutionPolicy Bypass -File scripts/aplc.ps1 record --event-json '@-' --evidence plc-evidence.jsonl --repo-root .
```

End by telling the user: "Agentic PLC setup is complete. Restart your IDE so hooks configuration reflect; if your IDE prompts to review hook changes, approve them from `/hooks`. Agentic PLC collects local telemetry and aggregate signals (skill invocations, installs, scanner run metadata) to analyze how effectively it improves coding agent workflows; publishing is best-effort and can be opted out by setting `AGENTIC_PLC_TELEMETRY=0`."
