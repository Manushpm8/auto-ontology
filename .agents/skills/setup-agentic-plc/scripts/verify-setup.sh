#!/usr/bin/env bash
# verify-setup.sh — one-shot verification that Agentic PLC is usable on this
# machine. No global installs, no auth, no user prompts.
#
# The agent invokes this after walking the install + auth checklist in
# SKILL.md. Output is human-readable + machine-parseable
# (PASS/FAIL/WARN/SKIP per line).
#
# Exit codes:
#   0  no hard failures (degraded coverage allowed; review SKIP/WARN lines above)
#   1  smoke fixture failed or an unexpected verifier internal error
# Network reachability failures do NOT cause exit 1; they are reported as
# WARN/SKIP with `degraded` accounting.
# CI consumers that want to gate on "no degraded coverage" should grep for
# the literal string `degraded local scanner coverage` in stdout.

set -u

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd -P)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
SEMGREP_RULES_ARCHIVE="$SKILL_ROOT/plc-security-scan/assets/semgrep-rules.tar.gz"
SEMGREP_SMOKE_RULE="semgrep-rules/python/lang/security/audit/eval-detected.yaml"
REPO_ROOT="$(git -C "${PWD}" rev-parse --show-toplevel 2>/dev/null || pwd -P)"
PLC_TOOLS_DIR="$REPO_ROOT/.plc/tools"
UV_CACHE_DIR_VALUE="$PLC_TOOLS_DIR/uv-cache"
FIXTURE_DIR="$PLC_TOOLS_DIR/verify-fixture"
PULSE_IMAGE="gitlab-master.nvidia.com:5005/pstooling/pulse-group/pulse-secret-scanner:1.70"
pulse_ready=0

result() { printf '%-40s %s\n' "$1" "$2"; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

extract_bundled_semgrep_smoke_rule() {
  local archive="$1" target="$2" tar_output tar_status
  mkdir -p "$target"
  tar_output="$(tar -xzf "$archive" -C "$target" "$SEMGREP_SMOKE_RULE" 2>&1)"
  tar_status=$?
  if [ "$tar_status" -ne 0 ]; then
    result "smoke/semgrep" "FAIL (bundled smoke rule extract failed)"
    printf "%s\n" "$tar_output" >&2
    return 1
  fi

  # Some archives created on macOS contain AppleDouble/xattr metadata that
  # Semgrep later tries to parse as YAML. Remove it defensively after extract.
  find "$target" \( -name '._*' -o -name '.DS_Store' \) -exec rm -rf {} + 2>/dev/null || true
  find "$target" -type d -name '__MACOSX' -prune -exec rm -rf {} + 2>/dev/null || true

  if [ ! -f "$target/$SEMGREP_SMOKE_RULE" ]; then
    result "smoke/semgrep" "FAIL (bundled smoke rule missing after extract)"
    return 1
  fi
}

# ── Pulse image available ─────────────────────────────────────────────────
check_pulse() {
  if ! has_cmd docker; then result "pulse-image" "SKIP (docker not installed)"; degraded=$((degraded + 1)); return 0; fi
  if ! docker info >/dev/null 2>&1;  then result "pulse-image" "WARN (docker daemon not running — start Docker Desktop to enable Pulse secret scanning)"; degraded=$((degraded + 1)); return 0; fi
  if docker image inspect "$PULSE_IMAGE" >/dev/null 2>&1; then
    result "pulse-image" "PASS"
    pulse_ready=1
  elif docker pull "$PULSE_IMAGE" >/dev/null 2>&1; then
    result "pulse-image" "PASS (pulled)"
    pulse_ready=1
  else
    result "pulse-image" "SKIP (pulse registry auth missing — run 'docker login gitlab-master.nvidia.com:5005' with a GitLab PAT that has 'read_registry' scope from https://gitlab-master.nvidia.com/-/profile/personal_access_tokens)"
    degraded=$((degraded + 1))
    return 0
  fi
}

# ── Smoke scan ─────────────────────────────────────────────────────────────
semgrep_source() {
  if has_cmd uvx; then
    printf "uvx"
    return 0
  fi
  if [ -x "$PLC_TOOLS_DIR/python-venv/bin/semgrep" ]; then
    printf "%s" "$PLC_TOOLS_DIR/python-venv/bin/semgrep"
    return 0
  fi
  if [ -x "$PLC_TOOLS_DIR/python-venv/Scripts/semgrep.exe" ]; then
    printf "%s" "$PLC_TOOLS_DIR/python-venv/Scripts/semgrep.exe"
    return 0
  fi
  return 1
}

run_semgrep_scan() {
  local config="$1" target="$2" source="$3"
  if [ "$source" = "uvx" ]; then
    UV_CACHE_DIR="$UV_CACHE_DIR_VALUE" uvx --from 'semgrep==1.157.0' semgrep scan \
      --config="$config" --quiet --metrics=off --disable-version-check "$target"
  else
    "$source" scan --config="$config" --quiet --metrics=off --disable-version-check "$target"
  fi
}

smoke_scan() {
  rm -rf "$FIXTURE_DIR"
  mkdir -p "$FIXTURE_DIR"
  cat > "$FIXTURE_DIR/app.py" <<'EOF'
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
def run(x): eval(x)
EOF

  semgrep_runner="$(semgrep_source || true)"
  if [ -n "$semgrep_runner" ]; then
    if [ ! -f "$SEMGREP_RULES_ARCHIVE" ]; then
      result "smoke/semgrep" "FAIL (bundled rules missing)"
      rm -rf "$FIXTURE_DIR"
      return 1
    fi
    rules_dir="$FIXTURE_DIR/semgrep-smoke-rules"
    if ! extract_bundled_semgrep_smoke_rule "$SEMGREP_RULES_ARCHIVE" "$rules_dir"; then
      rm -rf "$FIXTURE_DIR"
      return 1
    fi
    semgrep_output="$(run_semgrep_scan "$rules_dir/$SEMGREP_SMOKE_RULE" "$FIXTURE_DIR" "$semgrep_runner" 2>&1)"
    semgrep_status=$?
    if [ "$semgrep_status" -eq 0 ] || printf "%s\n" "$semgrep_output" | grep -qi "finding"; then
      if [ "$semgrep_runner" = "uvx" ]; then
        result "smoke/semgrep" "PASS (bundled smoke rule, project-local uvx cache)"
      else
        result "smoke/semgrep" "PASS (bundled smoke rule, project-local venv)"
      fi
    else
      result "smoke/semgrep" "FAIL (semgrep inconclusive)"
      rm -rf "$FIXTURE_DIR"
      return 1
    fi
  else
    result "smoke/semgrep" "SKIP (uvx unavailable and .plc/tools/python-venv semgrep missing)"
    degraded=$((degraded + 1))
  fi

  if [ "$pulse_ready" -eq 1 ]; then
    if docker run --rm -v "$FIXTURE_DIR":"$FIXTURE_DIR" -w "$FIXTURE_DIR" \
        "$PULSE_IMAGE" filesystem . --json >/dev/null 2>&1; then
      result "smoke/pulse" "PASS"
    else
      result "smoke/pulse" "FAIL (pulse scan errored on fixture)"
      rm -rf "$FIXTURE_DIR"
      return 1
    fi
  else
    result "smoke/pulse" "SKIP (Pulse image unavailable; see pulse-image status above)"
    degraded=$((degraded + 1))
  fi

  rm -rf "$FIXTURE_DIR"
}

check_osv_scanner() {
  if [ -x "$PLC_TOOLS_DIR/bin/osv-scanner" ]; then
    if "$PLC_TOOLS_DIR/bin/osv-scanner" --version >/dev/null 2>&1; then
      result "osv-scanner" "PASS (.plc/tools/bin)"
    else
      result "osv-scanner" "WARN (.plc/tools/bin/osv-scanner errored)"
      degraded=$((degraded + 1))
    fi
  elif [ -x "$PLC_TOOLS_DIR/bin/osv-scanner.exe" ]; then
    if "$PLC_TOOLS_DIR/bin/osv-scanner.exe" --version >/dev/null 2>&1; then
      result "osv-scanner" "PASS (.plc/tools/bin)"
    else
      result "osv-scanner" "WARN (.plc/tools/bin/osv-scanner.exe errored)"
      degraded=$((degraded + 1))
    fi
  else
    result "osv-scanner" "SKIP (not installed under .plc/tools/bin)"
    degraded=$((degraded + 1))
  fi
}

# ── Run all checks ────────────────────────────────────────────────────────
failures=0
degraded=0
mkdir -p "$PLC_TOOLS_DIR" "$UV_CACHE_DIR_VALUE"
check_pulse                                             || failures=$((failures + 1))
check_osv_scanner                                       || failures=$((failures + 1))
smoke_scan                                              || failures=$((failures + 1))

echo
if [ "$failures" = "0" ]; then
  if [ "$degraded" -gt 0 ]; then
    echo "verify-setup: OK with degraded local scanner coverage (review SKIP/WARN lines above)"
  else
    echo "verify-setup: OK (local scanner checks passed)"
  fi
  exit 0
else
  echo "verify-setup: $failures check(s) failed"
  exit 1
fi
