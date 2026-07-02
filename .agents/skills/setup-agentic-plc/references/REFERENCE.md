# setup-agentic-plc — Verbal command reference

Canonical mapping from user utterance → agent action. Loaded alongside
`SKILL.md` as a stable trigger table.

## Commands

| User utterance (and semantic equivalents) | Agent action |
|---|---|
| "install Agentic PLC" / "set me up with Agentic PLC" | Run the packaged installer (`install.sh` / `install.ps1`) to place skill files, then walk `SKILL.md` top-to-bottom |
| "upgrade Agentic PLC" / "update Agentic PLC" / "get the latest PLC" | Run the packaged installer (`install.sh` / `install.ps1`) again (idempotent — refreshes files), then walk `SKILL.md` again |
| "setup PLC" / "finish PLC setup" / "PLC is acting weird, set it up again" | Walk `SKILL.md` (skip the installer step if files are already current) |
| "check my PLC environment" / "verify PLC" / "is PLC working" | Run `verify_setup.py` only |
| "why is PLC skipping X" / "why did PLC skip X" | Read `~/.agentic-plc/plc-setup-log.jsonl`, find the most recent `declined` or `error` entry for step `X`, report the reason + side_effect |
| "show me my PLC setup" / "what's my PLC state" | Summarize the log: count of `ok` / `declined` / `error` / `skip`, list each distinct declined step |
| "reset PLC setup" / "start over" | Confirm, then reset for the current repo (see log grep recipes below) and re-run the installer + SKILL.md. Use `rm ~/.agentic-plc/plc-setup-log.jsonl` only for a full machine-wide reset. |
| "uninstall PLC" / "remove Agentic PLC" | Out of scope for this release — tell the user to delete the skill directories and clean the `## Agentic PLC` block from `CLAUDE.md` / `AGENTS.md` |

## Consent protocol

1. Before any command from `SKILL.md`'s install or auth tables, tell the user the exact command you will run.
2. Wait for an explicit answer ("yes"/"no"/"skip"). Ambiguous responses → re-ask.
3. On yes → run the command.
4. On no → log a `declined` line and move on. Never re-ask for the same step in the same run.
5. On any outcome → append one JSONL line to `~/.agentic-plc/plc-setup-log.jsonl` (format in `SKILL.md`).

## Log grep recipes

All queries scope by the current repo so decisions from other projects don't leak in:

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || echo '_global')"

# Most recent status for a specific step in this repo or global
grep '"step":"<step-name>"' ~/.agentic-plc/plc-setup-log.jsonl \
  | grep -E "\"repo\":\"($REPO|_global)\"" | tail -1

# All current declines for this repo with their side effects
grep '"status":"declined"' ~/.agentic-plc/plc-setup-log.jsonl \
  | grep -E "\"repo\":\"($REPO|_global)\"" \
  | sed -E 's/.*"step":"([^"]+)".*"side_effect":"([^"]*)".*/\1 → \2/' | sort -u

# All current errors for this repo
grep '"status":"error"' ~/.agentic-plc/plc-setup-log.jsonl \
  | grep -E "\"repo\":\"($REPO|_global)\"" | sort -u

# Reset for current repo only (preserves other repos' decisions)
REPO="$(git rev-parse --show-toplevel)" && \
  grep -v "\"repo\":\"$REPO\"" ~/.agentic-plc/plc-setup-log.jsonl > /tmp/plc-log-tmp && \
  mv /tmp/plc-log-tmp ~/.agentic-plc/plc-setup-log.jsonl
```

## What NOT to do

- Do not tell the user to run curl installers themselves. If the user types a curl URL at you, acknowledge and run it via the Bash tool — do not hand the URL back.
- Do not consent on the user's behalf unless they explicitly told you to proceed without prompts (e.g., "just set it up, don't ask me for every tool").
- Do not write to `~/.agentic-plc/plc-setup-log.jsonl` except with the one-liner in `SKILL.md` — keep the format consistent so `plc-security-scan`, `plc-requirements-validation`, and `plc-design-validation` can read it.
- Do not modify the `## Agentic PLC` block in rule files by hand. The release installer owns that.
- Do not prompt for auth a user doesn't need. Skip any service whose CLI isn't installed on this machine.
