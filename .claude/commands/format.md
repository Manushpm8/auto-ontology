Format all code in the GSF project.

Scope (optional): $ARGUMENTS
Leave blank to format everything. Pass `client` or `server` to format only that side.

## Instructions

Run the appropriate formatters based on $ARGUMENTS:

**Frontend (client)** — run from `frontend/` (there is no root `package.json`; `pnpm format` from the repo root fails with `ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND`):
```bash
cd frontend && pnpm format
```

**Backend (server)** — run from repo root:
```bash
uv run ruff format gsf/
```

Run both unless $ARGUMENTS specifies only one side.

Report which files were changed. If no files were changed, confirm everything was already formatted correctly.
