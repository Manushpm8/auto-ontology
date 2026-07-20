// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { ConnectionType } from '@/enums/connection';

export type DatabricksConnectionParams = {
	type: ConnectionType.DATABRICKS;
	host: string;
	http_path: string;
	password: string;
	database: string;
	/** Optional ingestion allowlist: only these schemas are ingested. Empty/absent = all. */
	schemas?: string[];
};

export type PostgresConnectionParams = {
	type: ConnectionType.POSTGRESQL;
	host: string;
	port: string;
	user: string;
	password: string;
	database: string;
};

export type SnowflakeConnectionParams = {
	type: ConnectionType.SNOWFLAKE;
	account: string;
	warehouse: string;
	user: string;
	password: string;
	database: string;
	/** Optional ingestion allowlist: only these schemas are ingested. Empty/absent = all. */
	schemas?: string[];
};

export type HeavyDBConnectionParams = {
	type: ConnectionType.HEAVYDB;
	host: string;
	port: string;
	user: string;
	password: string;
	database: string;
	protocol: string;
};

export type VastConnectionParams = {
	type: ConnectionType.VAST;
	/** Full VAST endpoint URL (http://host:port) or bare host[:port]. */
	endpoint: string;
	access_key: string;
	secret_key: string;
	/** VAST bucket; also the connection identity. */
	database: string;
	/** Optional end-user identity for impersonation. */
	end_user?: string;
	/** Optional ingestion allowlist: only these schemas are ingested. Empty/absent = all. */
	schemas?: string[];
};

/** Structured connection form fields, discriminated by `type`. */
export type ConnectionParams =
	| DatabricksConnectionParams
	| PostgresConnectionParams
	| SnowflakeConnectionParams
	| HeavyDBConnectionParams
	| VastConnectionParams;

/** A stored connection returned by the API. */
export type Connection = {
	database_name: string;
	connection: ConnectionParams;
};

/** User-provided fields for testing or creating a connection. */
export type ConnectionInput = ConnectionParams;
