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

/** BI connectors skip the third wizard step in illumex. */
export const CONNECTION_TYPES_WITHOUT_SELECT_DATA: ConnectionType[] = [];
