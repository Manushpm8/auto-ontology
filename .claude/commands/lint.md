Run all linters for the GSF project and report results.

Scope (optional): $ARGUMENTS
Leave blank to lint everything. Pass `client` or `server` to lint only that side.

## Instructions

Run the appropriate commands based on $ARGUMENTS:

**Frontend (client)** — run from `frontend/` (there is no root `package.json`; `pnpm lint` from the repo root fails with `ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND`):
```bash
cd frontend && pnpm lint
```

**Backend (server)** — run from repo root:
```bash
uv run ruff check gsf/
```

Run both unless $ARGUMENTS specifies only one side.

After running:
- Report any errors and warnings clearly, grouped by file.
- If there are fixable issues, ask the user whether to auto-fix them (`cd frontend && pnpm lint --fix` for JS/TS, `uv run ruff check --fix gsf/` for Python — a bare `eslint` is not on PATH, and `--fix .` would widen the scope beyond what CI checks).
- Do not auto-fix without confirmation.
