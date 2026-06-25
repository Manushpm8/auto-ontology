# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# WWI (Wide World Importers) demo-data seed image
# ---------------------------------------------------------------------------
# Bakes the community PostgreSQL port of Microsoft's WideWorldImporters sample
# database (a `pg_restore` custom-format dump) into a Postgres-client image, so
# the deployment seed Job can restore it into the in-cluster Postgres WITHOUT
# any public-internet egress (Astra clusters are network-restricted).
#
# Base image matches the deployed Postgres (pg17) so client tool versions align.
# Build: docker build -t gsf-wwi-seed deploy/wwi-seed
# ---------------------------------------------------------------------------
ARG PG_BASE=pgvector/pgvector:pg17
FROM ${PG_BASE}

# Community PG conversion of Microsoft's WideWorldImporters (custom-format dump).
ARG WWI_DUMP_URL=https://raw.githubusercontent.com/Azure/azure-postgresql/master/samples/databases/wide-world-importers/wide_world_importers_pg.dump
ADD ${WWI_DUMP_URL} /dump/wwi.dump

COPY entrypoint.sh /usr/local/bin/wwi-seed.sh
RUN chmod 0755 /usr/local/bin/wwi-seed.sh && chmod 0644 /dump/wwi.dump

ENTRYPOINT ["/usr/local/bin/wwi-seed.sh"]
