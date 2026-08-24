Create a new FastAPI router in the GSF project.

Router name: $ARGUMENTS
(e.g. `users` — the routes become `/api/users`, `/api/users/{user_id}`)

## Instructions

1. Parse the router name from $ARGUMENTS.

2. Create the package `gsf/server/<name>/` with `__init__.py`, `router.py` and — whenever the handler does more than a single DAL call — a sibling `service.py` for the orchestration. This mirrors every existing router (`gsf/server/terms/`, `gsf/server/datasources/`, `gsf/server/connections/`). Read `gsf/server/terms/router.py` and `gsf/server/terms/service.py` first and follow them.

   `router.py` skeleton:
   ```python
   # SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
   # All rights reserved.
   # SPDX-License-Identifier: Apache-2.0

   """API route handlers for <name>."""

   from __future__ import annotations

   import logging

   from fastapi import APIRouter, HTTPException, Path

   from gsf.server.pagination import LIMIT_QUERY, SKIP_QUERY
   from gsf.server.responses import <Name>PageResponse, <Name>Response
   from gsf.server.<name> import service as <name>_service

   router = APIRouter()
   logger = logging.getLogger(__name__)


   @router.get("/<name>", response_model=<Name>PageResponse)
   def list_<name>(
       skip: int = SKIP_QUERY,
       limit: int | None = LIMIT_QUERY,
   ) -> dict:
       """Return <name> rows, one page of the full order."""
       return <name>_service.list_<name>_page(skip=skip, limit=limit)


   @router.get("/<name>/{item_id}", response_model=<Name>Response)
   def get_<singular>(item_id: str = Path(description="…")) -> dict:
       row = <name>_service.get_<singular>(item_id)
       if row is None:
           raise HTTPException(status_code=404, detail="Not found")
       return {"data": row}
   ```

   - **The resource segment lives in the decorator path, not the prefix.** Every router is mounted with `prefix="/api"`; `@router.get("/")` would produce `/api/` and collide.
   - Handlers are plain `def`. Use `async def` only when the handler genuinely awaits async I/O — reading a request body or upload, or streaming a response (see `gsf/server/model_interchange/router.py` and `gsf/server/chat/router.py`). The neo4j/DAL layer is synchronous, so most handlers are not async.
   - Every handler needs a return type annotation and, in almost all cases, a `response_model=` (58 of 62 existing routes have one). Add the Pydantic response model to `gsf/server/responses.py` rather than declaring it inline.
   - List endpoints take `skip`/`limit` from `gsf.server.pagination`.
   - Helper/private functions are prefixed with `_`. Raise `HTTPException` for error responses.
   - Keep the SPDX header and a module docstring — every existing router has both.

3. Register the router in `gsf/server/__main__.py` (there is no `gsf/app/` and no `main.py`). Add the import with the other router imports, which sit below `load_env()` and therefore need the noqa:
   ```python
   from gsf.server.<name>.router import router as <name>_router  # noqa: E402
   ```
   and the mount inside `create_app()`:
   ```python
   app.include_router(<name>_router, prefix="/api", tags=["<name>"])
   ```

4. Regenerate the committed OpenAPI spec and commit it — CI (`.github/workflows/ci-openapi.yml`) fails if it drifts:
   ```bash
   uv run python -m dev_tools.generate_backend_openapi
   ```

5. Run `uv run ruff check gsf/` and `uv run ruff format gsf/` from the repo root and fix any issues.

6. If the frontend will call this route, it also needs a permission-gated handler under `frontend/app/api/**` — `next.config.ts` has no rewrites, so an unproxied backend route is unreachable from the UI. See `/new-api-client`.

7. Report the files created and modified.
