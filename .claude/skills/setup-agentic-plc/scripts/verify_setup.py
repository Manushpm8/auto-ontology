#!/usr/bin/env python3
"""verify_setup.py - one-shot verification that Agentic PLC is usable on this machine.

Cross-platform sibling of verify-setup.sh. Same checks, same output contract,
but pure Python so it behaves identically on Windows (native PowerShell),
macOS, Linux, and WSL without depending on bash, tar, find, or grep.

No global installs, no auth, no user prompts.

Exit codes:
  0  no hard failures (degraded coverage allowed; review SKIP/WARN lines above)
  1  smoke fixture failed or an unexpected verifier internal error

Network reachability failures do NOT cause exit 1; they are reported as
WARN/SKIP with `degraded` accounting. CI consumers that want to gate on
"no degraded coverage" should look for the literal string
`degraded local scanner coverage` in stdout.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
SEMGREP_RULES_ARCHIVE = SKILL_ROOT.parent / "plc-security-scan" / "assets" / "semgrep-rules.tar.gz"
SEMGREP_SMOKE_RULE = "semgrep-rules/python/lang/security/audit/eval-detected.yaml"
PULSE_IMAGE = "gitlab-master.nvidia.com:5005/pstooling/pulse-group/pulse-secret-scanner:1.70"

# Mutable run state.
failures = 0
degraded = 0


def result(label: str, status: str) -> None:
    print(f"{label:<40} {status}")


def mark_degraded() -> None:
    global degraded
    degraded += 1


def has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        **kwargs,
    )


def detect_repo_root() -> Path:
    proc = run(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    if has_cmd("p4"):
        proc = run(["p4", "-ztag", "info"])
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("... clientRoot "):
                    return Path(line[len("... clientRoot "):].strip())
    return Path.cwd()


REPO_ROOT = detect_repo_root()
PLC_TOOLS_DIR = REPO_ROOT / ".plc" / "tools"
UV_CACHE_DIR = PLC_TOOLS_DIR / "uv-cache"
FIXTURE_DIR = PLC_TOOLS_DIR / "verify-fixture"


def venv_bin(name: str) -> Path | None:
    """Resolve a venv-installed tool across the Unix (bin/) and Windows (Scripts/) layouts."""
    candidates = [
        PLC_TOOLS_DIR / "python-venv" / "bin" / name,
        PLC_TOOLS_DIR / "python-venv" / "Scripts" / f"{name}.exe",
        PLC_TOOLS_DIR / "python-venv" / "Scripts" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# -- Pulse image available ---------------------------------------------------
def check_pulse() -> None:
    if not has_cmd("docker"):
        result("pulse-image", "SKIP (docker not installed)")
        mark_degraded()
        return
    if run(["docker", "info"]).returncode != 0:
        result(
            "pulse-image",
            "WARN (docker daemon not running - start Docker Desktop to enable Pulse secret scanning)",
        )
        mark_degraded()
        return
    if run(["docker", "image", "inspect", PULSE_IMAGE]).returncode == 0:
        result("pulse-image", "PASS")
        check_pulse.ready = True
    elif run(["docker", "pull", PULSE_IMAGE]).returncode == 0:
        result("pulse-image", "PASS (pulled)")
        check_pulse.ready = True
    else:
        result(
            "pulse-image",
            "SKIP (pulse registry auth missing - run 'docker login gitlab-master.nvidia.com:5005' "
            "with a GitLab PAT that has 'read_registry' scope from "
            "https://gitlab-master.nvidia.com/-/profile/personal_access_tokens)",
        )
        mark_degraded()


check_pulse.ready = False


# -- osv-scanner -------------------------------------------------------------
def check_osv_scanner() -> None:
    for name in ("osv-scanner", "osv-scanner.exe"):
        binary = PLC_TOOLS_DIR / "bin" / name
        if binary.is_file():
            if run([str(binary), "--version"]).returncode == 0:
                result("osv-scanner", "PASS (.plc/tools/bin)")
            else:
                result("osv-scanner", f"WARN (.plc/tools/bin/{name} errored)")
                mark_degraded()
            return
    result("osv-scanner", "SKIP (not installed under .plc/tools/bin)")
    mark_degraded()


# -- Smoke scan --------------------------------------------------------------
def semgrep_runner() -> str | None:
    if has_cmd("uvx"):
        return "uvx"
    binary = venv_bin("semgrep")
    return str(binary) if binary else None


def extract_smoke_rule(target: Path) -> bool:
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(SEMGREP_RULES_ARCHIVE, "r:gz") as archive:
            member = archive.getmember(SEMGREP_SMOKE_RULE)
            archive.extract(member, target)
    except (tarfile.TarError, KeyError, OSError) as exc:
        result("smoke/semgrep", "FAIL (bundled smoke rule extract failed)")
        print(exc, file=sys.stderr)
        return False
    if not (target / SEMGREP_SMOKE_RULE).is_file():
        result("smoke/semgrep", "FAIL (bundled smoke rule missing after extract)")
        return False
    return True


def run_semgrep(config: Path, target: Path, runner: str) -> subprocess.CompletedProcess:
    if runner == "uvx":
        env = dict(os.environ, UV_CACHE_DIR=str(UV_CACHE_DIR))
        cmd = [
            "uvx", "--from", "semgrep==1.157.0", "semgrep", "scan",
            f"--config={config}", "--quiet", "--metrics=off", "--disable-version-check", str(target),
        ]
        return run(cmd, env=env)
    cmd = [
        runner, "scan", f"--config={config}", "--quiet",
        "--metrics=off", "--disable-version-check", str(target),
    ]
    return run(cmd)


def smoke_scan() -> bool:
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / "app.py").write_text(
        'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\ndef run(x): eval(x)\n',
        encoding="utf-8",
    )

    ok = True
    runner = semgrep_runner()
    if runner:
        if not SEMGREP_RULES_ARCHIVE.is_file():
            result("smoke/semgrep", "FAIL (bundled rules missing)")
            shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
            return False
        rules_dir = FIXTURE_DIR / "semgrep-smoke-rules"
        if not extract_smoke_rule(rules_dir):
            shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
            return False
        proc = run_semgrep(rules_dir / SEMGREP_SMOKE_RULE, FIXTURE_DIR, runner)
        if proc.returncode == 0 or "finding" in proc.stdout.lower():
            where = "project-local uvx cache" if runner == "uvx" else "project-local venv"
            result("smoke/semgrep", f"PASS (bundled smoke rule, {where})")
        else:
            result("smoke/semgrep", "FAIL (semgrep inconclusive)")
            shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
            return False
    else:
        result("smoke/semgrep", "SKIP (uvx unavailable and .plc/tools/python-venv semgrep missing)")
        mark_degraded()

    if getattr(check_pulse, "ready", False):
        proc = run([
            "docker", "run", "--rm",
            "-v", f"{FIXTURE_DIR}:{FIXTURE_DIR}", "-w", str(FIXTURE_DIR),
            PULSE_IMAGE, "filesystem", ".", "--json",
        ])
        if proc.returncode == 0:
            result("smoke/pulse", "PASS")
        else:
            result("smoke/pulse", "FAIL (pulse scan errored on fixture)")
            ok = False
    else:
        result("smoke/pulse", "SKIP (Pulse image unavailable; see pulse-image status above)")
        mark_degraded()

    shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
    return ok


def main() -> int:
    global failures
    # No options today, but define a parser so `--help` exits cleanly (the CI
    # `--help` smoke loop and users both rely on this) instead of falling through
    # and running the full verifier.
    argparse.ArgumentParser(
        description="Verify Agentic PLC local scanner readiness (no args; prints PASS/FAIL/WARN/SKIP).",
    ).parse_args()
    PLC_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    UV_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    check_pulse()
    check_osv_scanner()
    if not smoke_scan():
        failures += 1

    print()
    if failures == 0:
        if degraded > 0:
            print("verify-setup: OK with degraded local scanner coverage (review SKIP/WARN lines above)")
        else:
            print("verify-setup: OK (local scanner checks passed)")
        return 0
    print(f"verify-setup: {failures} check(s) failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
