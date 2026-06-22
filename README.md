# GSF

Generative Semantic Fabric adds the structured-data ontology layer to any partner or NVidia agent harness  interface, like NVIDIA AI-Q Claws, etc

> **Licensing & contributions.** GSF is distributed under the
> [Apache License 2.0](./LICENSE). Third-party open-source components
> bundled, linked, or otherwise used by this project are listed in
> [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md). **This project
> is currently not accepting external contributions.**

## Deployment

### Kubernetes

To deploy GSF on a Kubernetes cluster, see [`DEPLOYMENT.md`](./DEPLOYMENT.md).

### Local (Docker Compose)

To run the full stack locally, clone the repo and use Docker Compose:

1. Clone the repository:

   ```bash
   git clone <repo-url> gsf && cd gsf
   ```

2. Create your environment file (.env) from the template and fill in the values
   (Postgres/Neo4j credentials, `NVIDIA_API_KEY`, `CONNECTION_STRINGS`, etc.).
   See [`.env.example`](./.env.example) for the full list of variables:

   ```bash
   cp .env.example .env
   # edit .env
   ```

3. Build the images and start the stack:

   ```bash
   docker compose up -d --build
   ```

   This builds the backend (`gsf`) and frontend (`gsf-frontend`) images, brings
   up Postgres, Neo4j, and pgAdmin, runs the one-shot `frontend-migrate` job to
   sync the database schema, and starts the app.

4. Open the UI at <http://localhost:3000> (the backend API is on `:3001`,
   pgAdmin on `:5050`).


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
