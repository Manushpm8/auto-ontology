#!/usr/bin/env python3
"""plc_setup_log.py - append one decision line to the Agentic PLC setup log.

Cross-platform replacement for the bash `date`/`git`/`printf` one-liner. Use
this from any shell (PowerShell, bash, zsh) so the JSONL is written as plain
UTF-8 with no BOM and no shell-quoting hazards.

Usage:
  python plc_setup_log.py --step docker --status declined --side-effect "pulse skipped"

`--status` is one of: ok | declined | error | skip. `--side-effect` is optional
free text (empty for ok). The repo field is resolved automatically (git root,
then Perforce client root, then "_global").
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
from pathlib import Path

LOG_PATH = Path.home() / ".agentic-plc" / "plc-setup-log.jsonl"


def resolve_repo() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    if shutil.which("p4"):
        proc = subprocess.run(
            ["p4", "-ztag", "info"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("... clientRoot "):
                    return line[len("... clientRoot "):].strip()
    return "_global"


def main() -> int:
    parser = argparse.ArgumentParser(description="Append one Agentic PLC setup-log line.")
    parser.add_argument("--step", required=True)
    parser.add_argument("--status", required=True, choices=["ok", "declined", "error", "skip"])
    # nargs="?" + const="" so `--side-effect ""` survives PowerShell, which drops
    # an empty-string argument to a native exe and would otherwise leave the flag
    # value-less. Bare `--side-effect`, omitted, or an explicit value all work.
    parser.add_argument("--side-effect", nargs="?", default="", const="")
    parser.add_argument("--repo", default=None, help="Override the resolved repo field.")
    args = parser.parse_args()

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": args.repo or resolve_repo(),
        "step": args.step,
        "status": args.status,
        "side_effect": args.side_effect,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry) + "\n")
    print(json.dumps(entry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
