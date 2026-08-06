// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Connector kinds supported in the new-connection wizard. */
export enum ConnectionType {
	DATABRICKS = 'databricks',
	POSTGRESQL = 'postgresql',
	SNOWFLAKE = 'snowflake',
	HEAVYDB = 'heavydb',
}

export const connectionDisplayName: Record<ConnectionType, string> = {
	[ConnectionType.DATABRICKS]: 'Databricks',
	[ConnectionType.POSTGRESQL]: 'PostgreSQL',
	[ConnectionType.SNOWFLAKE]: 'Snowflake',
	[ConnectionType.HEAVYDB]: 'HeavyDB',
};

export const isConnectionType = (value: string | null | undefined): value is ConnectionType =>
	typeof value === 'string' && (Object.values(ConnectionType) as string[]).includes(value);

export type ConnectionFieldKey =
	| 'host'
	| 'http_path'
	| 'port'
	| 'account'
	| 'warehouse'
	| 'user'
	| 'password'
	| 'database'
	| 'protocol'
	| 'schema'
	| 'auth_mode'
	| 'ssa_client_id'
	| 'ssa_client_secret'
	| 'databricks_client_id'
	| 'ssa_token_url'
	| 'ssa_scope'
	| 'ssa_audience'
	| 'sso_federation';

/** How a Databricks connection obtains its access token. */
export enum DatabricksAuthMode {
	/** A stored personal access token, entered directly. */
	TOKEN = 'token',
	/** Minted on demand from NVIDIA SSA service-account credentials. */
	SSA = 'ssa',
}

/** Defaults for the SSA flow, per the Kratos CI/CD guide. Editable per connection. */
export const DEFAULT_SSA_TOKEN_URL =
	'https://w6rojyggn16dpp37xuunjjnvczxdhjrkobq393rkkae.ssa.nvidia.com/token';
export const DEFAULT_SSA_SCOPE = 'pipelines-write';

export type ConnectionField = {
	key: ConnectionFieldKey;
	label: string;
	placeholder?: string;
	secret?: boolean;
	/** Optional fields are not required to enable Test/Create. */
	optional?: boolean;
	/** Sent with the connection test only; stripped before the connection is created. */
	testOnly?: boolean;
	/** Rendered as a checkbox and sent as a boolean rather than a string. */
	boolean?: boolean;
	/** Rendered as a dropdown of these choices rather than a free-text input. */
	choices?: { value: string; label: string }[];
	/** Prefilled when the form opens; the user can still change it. */
	defaultValue?: string;
	/**
	 * Only rendered when another field holds one of these values — used to reveal the
	 * credential set that matches the chosen authentication mode. Hidden fields are
	 * also exempt from the required-field check, so the other mode's inputs cannot
	 * block Test/Create.
	 */
	visibleWhen?: { key: ConnectionFieldKey; values: string[] };
	/** Helper text shown under the field. */
	hint?: string;
};

/** Form fields rendered per connector type. `database` is the connection identity. */
export const CONNECTION_FIELDS: Record<ConnectionType, ConnectionField[]> = {
	[ConnectionType.DATABRICKS]: [
		{
			key: 'host',
			label: 'Server hostname',
			placeholder: 'dbc-a1b2345c-d6e7.cloud.databricks.com',
		},
		{
			key: 'http_path',
			label: 'HTTP path',
			placeholder: '/sql/1.0/warehouses/a1b234c567d8e9fa',
		},
		{
			key: 'auth_mode',
			label: 'Authentication',
			choices: [
				{ value: DatabricksAuthMode.TOKEN, label: 'Access token' },
				{ value: DatabricksAuthMode.SSA, label: 'Service account (SSA)' },
			],
			defaultValue: DatabricksAuthMode.TOKEN,
			hint: 'A stored access token, or NVIDIA SSA service-account credentials that mint a short-lived token on demand.',
		},
		{
			key: 'password',
			label: 'Access token',
			secret: true,
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.TOKEN] },
		},
		{
			key: 'ssa_client_id',
			label: 'SSA client ID',
			placeholder: 'nvssa-prd-…',
			hint: 'Issued by the Kratos team when the service account is registered.',
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.SSA] },
		},
		{
			key: 'ssa_client_secret',
			label: 'SSA client secret',
			placeholder: 'ssap-…',
			secret: true,
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.SSA] },
		},
		{
			key: 'databricks_client_id',
			label: 'Databricks client ID',
			hint: 'Shared by the Kratos team after registration; identifies the workspace application the SSA token is exchanged for.',
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.SSA] },
		},
		{
			key: 'ssa_token_url',
			label: 'SSA token URL',
			defaultValue: DEFAULT_SSA_TOKEN_URL,
			optional: true,
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.SSA] },
		},
		{
			key: 'ssa_scope',
			label: 'SSA scope',
			defaultValue: DEFAULT_SSA_SCOPE,
			optional: true,
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.SSA] },
		},
		{
			key: 'ssa_audience',
			label: 'SSA audience',
			placeholder: 'Leave empty unless your workspace requires one',
			optional: true,
			visibleWhen: { key: 'auth_mode', values: [DatabricksAuthMode.SSA] },
		},
		{ key: 'database', label: 'Catalog', placeholder: 'main' },
		{
			key: 'schema',
			label: 'Schema',
			placeholder: 'Leave empty to choose from a list',
			hint: 'Ingest only this schema. The connection test verifies it exists, and the schema selection step is skipped. Leave empty to pick schemas from a list instead.',
			optional: true,
			testOnly: true,
		},
		{
			key: 'sso_federation',
			label: 'Authenticate as signed-in user (SSO)',
			hint: 'Chat queries run with the signed-in user’s own Databricks privileges instead of the access token above. Requires a Databricks federation policy trusting your SSO issuer. Ingestion always uses the access token.',
			optional: true,
			boolean: true,
		},
	],
	[ConnectionType.POSTGRESQL]: [
		{ key: 'host', label: 'Host', placeholder: 'localhost' },
		{ key: 'port', label: 'Port', placeholder: '5432' },
		{ key: 'user', label: 'User', placeholder: 'postgres' },
		{ key: 'password', label: 'Password', secret: true },
		{ key: 'database', label: 'Database', placeholder: 'my_database' },
	],
	[ConnectionType.SNOWFLAKE]: [
		{ key: 'account', label: 'Account', placeholder: 'xy12345.us-east-1' },
		{ key: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH' },
		{ key: 'user', label: 'User' },
		{ key: 'password', label: 'Password', secret: true },
		{ key: 'database', label: 'Database', placeholder: 'MY_DATABASE' },
	],
	[ConnectionType.HEAVYDB]: [
		{ key: 'host', label: 'Host', placeholder: 'localhost' },
		{ key: 'port', label: 'Port', placeholder: '6274', optional: true },
		{ key: 'user', label: 'User', placeholder: 'admin' },
		{ key: 'password', label: 'Password', secret: true },
		{ key: 'database', label: 'Database', placeholder: 'heavyai' },
		{ key: 'protocol', label: 'Protocol', placeholder: 'binary', optional: true },
	],
};
