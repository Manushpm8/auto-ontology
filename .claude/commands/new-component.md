Create a new React component for the GSF project.

Component name and optional subfolder: $ARGUMENTS

## Instructions

1. Determine the component name and target directory from $ARGUMENTS.
   - Feature components go in a page subfolder: `frontend/components/<featurePage>/<ComponentName>.tsx` (e.g. `dataPage/`, `termsPage/`, `connectionsPage/`). Nothing sits directly in `frontend/components/` — its top level is all directories.
   - Genuinely shared/generic components go in `frontend/common/` instead, and are imported as `@/common/<Name>`.

2. Create `<ComponentName>.tsx` following this exact pattern:
   - Include `"use client";` at the top only if the component uses browser APIs, event handlers, useState, useEffect, or other client-side hooks. Omit it for purely presentational components that could be server-rendered.
   - Define an exported `type <ComponentName>Props = { ... }` — the repo uses `type` for props, never `interface` (31 of 31 under `frontend/components/`). JSDoc the props whose meaning isn't obvious from the name.
   - Export the component as a named export (not default).
   - Style exclusively with Tailwind CSS classes — no inline styles.
   - Use `useCallback` for event handlers, `useMemo` for expensive derived values.

3. If the component lives inside a subfolder that has an `index.ts` barrel file, add the named export and type export to it.

4. Formatting is Prettier's job, not yours: run `cd frontend && pnpm format` when you're done (tabs, single quotes, semicolons, trailing commas, 100-char width).

5. Report the files created and any barrel exports updated.
