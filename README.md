# GSF

Generative semantic fabric.

> **Licensing & contributions.** GSF is distributed under the
> [Apache License 2.0](./LICENSE). Third-party open-source components
> bundled, linked, or otherwise used by this project are listed in
> [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md). **This project
> is currently not accepting external contributions.**

## Running locally

The **frontend** (`frontend/`) and the **Python API** (`gsf/`) must both be running.

### One-time setup

```bash
cp .env.example .env   # then edit credentials as needed
pnpm install
uv sync
```

### Start development

Full stack (infrastructure + Next.js + FastAPI):

```bash
./scripts/setup_env.sh
```

Or infrastructure only (Postgres, pgAdmin, Neo4j):

```bash
./scripts/setup_env.sh --dev
```

Then start the app manually:

```bash
pnpm dev        # Next.js on port 3000
pnpm dev:api    # FastAPI on port 3001
```

Open **http://localhost:3000**.

See [scripts/README.md](scripts/README.md) for all available flags.

### Optional

- Override the API URL: set **`PYTHON_API_URL`** (used by Next rewrites and server-side API calls).

## License

GSF is licensed under the [Apache License, Version 2.0](./LICENSE).
SPDX identifier: `Apache-2.0`.

Each NVIDIA-authored source file in this repository carries an SPDX header
of the form:

```text
SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
```

Third-party open-source components used by GSF are enumerated, with their
upstream licenses and project URLs, in
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

## Contributing

**This project is currently not accepting contributions.** Issues, pull
requests, and patches submitted from outside the GSF maintainer team will
not be reviewed or merged. Security-relevant reports should follow the
process described in [`SECURITY.md`](./SECURITY.md).
