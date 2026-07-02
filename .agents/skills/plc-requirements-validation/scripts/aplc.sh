#!/usr/bin/env bash

warn() {
  printf 'warning: Agentic PLC aplc shim skipped: %s\n' "$1" >&2
}

aplc=""
if [ -x ".plc/tools/python-venv/bin/aplc" ]; then
  aplc=".plc/tools/python-venv/bin/aplc"
elif [ -n "${HOME:-}" ] && [ -x "$HOME/.agentic-plc/tools/python-venv/bin/aplc" ]; then
  aplc="$HOME/.agentic-plc/tools/python-venv/bin/aplc"
else
  warn "aplc CLI not found"
  exit 0
fi

exec "$aplc" "$@"
