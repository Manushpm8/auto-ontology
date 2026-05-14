# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application entry-point — middleware, routers, lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from nemo_retriever.tabular_data.neo4j import neo4j_connection

from gsf.server.env import load_server_env

load_server_env()

from gsf.server.chat.router import router as chat_router  # noqa: E402
from gsf.server.datasources.router import router as datasources_router  # noqa: E402
from gsf.server.health.router import router as health_router  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if neo4j_connection._conn is not None:
        neo4j_connection._conn.close()
        neo4j_connection._conn = None


app = FastAPI(title="GSF API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    datasources_router, prefix="/api", tags=["datasources", "connectors"]
)
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(health_router, prefix="/api", tags=["health"])
