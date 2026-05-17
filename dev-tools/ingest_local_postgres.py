# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingest the local docker-compose Postgres into the pgvector embeddings store.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    uv run --no-sync python dev-tools/ingest_local_postgres.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ``dev-tools`` is not a package and the project sets ``[tool.uv] package =
# false``, so running this file directly (``python dev-tools/...``) puts
# ``dev-tools/`` on sys.path instead of the repo root. Prepend the repo root
# so ``from gsf...`` imports resolve regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env", override=False)

from nemo_retriever.graph import Graph  # noqa: E402
from nemo_retriever.graph.tabular_schema_extract_operator import (  # noqa: E402
    TabularSchemaExtractOp,
)
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (  # noqa: E402
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.text_embed.operators import _BatchEmbedActor  # noqa: E402
from nemo_retriever.retriever import Retriever  # noqa: E402
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import (  # noqa: E402
    get_agent_response,
)
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import (  # noqa: E402
    AgentPayload,
)
from nemo_retriever.vdb import IngestVdbOperator  # noqa: E402
from nemo_retriever.params import EmbedParams, TabularExtractParams  # noqa: E402
from gsf.vdb import get_vdb  # noqa: E402
from gsf.connectors.postgres import PostgresDatabase  # noqa: E402

logger = logging.getLogger("dev-tools.ingest_local_postgres")

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

if not _NVIDIA_API_KEY:
    raise EnvironmentError(
        "NVIDIA_API_KEY is not set. "
        "Export it before running:\n\n"
        "    export NVIDIA_API_KEY='nvapi-...'\n\n"
        "Get your key at https://build.nvidia.com"
    )


# Remote NIM embedding endpoint — no local GPU required.
# Model hosted on build.nvidia.com; billed against your NVIDIA API key.
# Configured via EMBED_ENDPOINT / EMBED_MODEL env vars so the API server
# (gsf/server/chat/router.py) and this script always agree.
EMBED_PARAMS = EmbedParams(
    embed_invoke_url=_EMBED_ENDPOINT,
    model_name=_EMBED_MODEL,
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)

# Remote source DB to extract tabular schema/embeddings from. Kept separate
# from the local POSTGRES_* vars (which point at the pgvector store).
_CONNECTOR_URL = os.environ.get("CONNECTOR_URL", "")
if not _CONNECTOR_URL:
    raise EnvironmentError(
        "CONNECTOR_URL is not set. Add it to your .env, e.g.:\n\n"
        "    CONNECTOR_URL=postgresql://user:password@host:5432/dbname"
    )

TABULAR_PARAMS = TabularExtractParams(
    connector=PostgresDatabase(_CONNECTOR_URL),
)


def run_ingest() -> None:
    """Build the tabular ingest graph, run it, and write embeddings to pgvector."""
    graph = (
        Graph()
        >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
        >> TabularFetchEmbeddingsOp(
            database_name=TABULAR_PARAMS.connector.database_name
        )
        >> _BatchEmbedActor(params=EMBED_PARAMS)
    )

    results = graph.execute(None)
    result_df = results[0] if results else None

    if result_df is not None and not result_df.empty:
        ingest_op = IngestVdbOperator(
            vdb=get_vdb(database_name=TABULAR_PARAMS.connector.database_name)
        )
        ingest_op(result_df.to_dict(orient="records"))
        print(
            "Tabular ingest result:",
            len(result_df),
            "rows written to pgvector)",
        )
    else:
        print("Tabular ingest result: no rows produced")


def run_retrieve(question: str = "List actors") -> None:
    """Run the text-to-SQL agent against the previously ingested pgvector store."""
    retriever = Retriever(
        top_k=15,
        vdb_kwargs={"vdb": get_vdb()},
        embed_kwargs={
            "model_name": _EMBED_MODEL,
            "embed_invoke_url": _EMBED_ENDPOINT,
            "api_key": _NVIDIA_API_KEY,
        },
    )

    payload: AgentPayload = {
        "question": question,
        "retriever": retriever,
        "connector": TABULAR_PARAMS.connector,
        "path_state": {},
        "custom_prompts": "",
        "acronyms": "",
    }

    agent_result = get_agent_response(payload)
    print("get_agent_response result:", agent_result)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Ingest the source DB (CONNECTOR_URL) into the local pgvector "
            "store and optionally smoke-test the text-to-SQL agent."
        )
    )
    parser.add_argument(
        "--retrieve",
        action="store_true",
        help=(
            "After ingest, run a single text-to-SQL query as a smoke test. "
            "Disabled by default because it costs LLM tokens and may loop on "
            "questions whose entities aren't in the schema."
        ),
    )
    parser.add_argument(
        "--question",
        default="List actors",
        help="Question for --retrieve (default: %(default)r).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_ingest()
    if args.retrieve:
        run_retrieve(args.question)


if __name__ == "__main__":
    _main()
