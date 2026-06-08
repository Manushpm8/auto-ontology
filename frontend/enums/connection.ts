// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Connector kinds supported in the new-connection wizard. */
export enum ConnectionType {
	POSTGRESQL = 'postgresql',
	SNOWFLAKE = 'snowflake',
}

export const connectionDisplayName: Record<ConnectionType, string> = {
	[ConnectionType.POSTGRESQL]: 'PostgreSQL',
	[ConnectionType.SNOWFLAKE]: 'Snowflake',
};

export const isConnectionType = (value: string | null | undefined): value is ConnectionType =>
	typeof value === 'string' && (Object.values(ConnectionType) as string[]).includes(value);
