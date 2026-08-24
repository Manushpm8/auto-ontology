Create a new API client module in the GSF frontend for a backend router.

Router/resource name: $ARGUMENTS
(e.g. `users` — matches a backend router at `/api/users`)

## Instructions

Read `frontend/api/datasources.ts` and `frontend/api/requests.ts` first to understand the existing pattern, then create `frontend/api/<name>.ts` following the same conventions:

- Use the `requests` wrapper from `frontend/api/requests.ts` — do **not** import or call axios directly. The wrapper already handles base URL (`PYTHON_API_URL`), error catching, and typed responses.
- Export a named object — `export const <name>Api = { ... }` (the prevailing convention: `termsApi`, `connectionsApi`, `zonesApi`) — with one method per endpoint.
- Use the `ResponseWithCount<T>` and `ResponseWithError<T>` wrapper types from `frontend/api/types.ts`.
- All functions must be typed end-to-end (input params and return type).

There is **no** `frontend/api` barrel — do not create one. Consumers import the concrete module: `import { <name>Api } from '@/api/<name>';`

Then add the Next.js route handler the client will call. `frontend/next.config.ts` deliberately declares **no rewrites**, so a backend route with no handler under `frontend/app/api/**` is unreachable from the browser. Create `frontend/app/api/<name>/route.ts` (and `[<id>]/route.ts` for item routes), following `frontend/app/api/terms/route.ts`:

```ts
import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

export const GET = withPermission({ catalog: ['read'] })((req) => proxyToBackend(req));
```

Pick the permission scope that matches the resource; use `withPublic` only for genuinely unauthenticated routes. Then regenerate the committed frontend spec — CI (`.github/workflows/ci-openapi.yml`) fails if `docs/openapi/gsf-api.json` drifts:

```bash
cd frontend && pnpm openapi
```

Formatting is Prettier's job, not yours: run `cd frontend && pnpm format` when you're done (tabs, single quotes, semicolons, trailing commas, 100-char width).

Report the files created and modified.
