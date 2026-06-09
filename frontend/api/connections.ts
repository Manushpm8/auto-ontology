// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { parseConnectionDatabaseName } from '@/lib/parseConnectionDatabaseName';
import { requests } from './requests';
import type { Connection } from '@/types/connection';
import type { ConnectionInput } from '@/types/connectionInput';
import type { ApiError, ApiResponse, ResponseWithCount } from './types';

type CreateResponse = ApiError | { data: Connection };
type TestResponse = ApiError | { success: true };

const err = (message: string): ApiError => ({ error: true, message });

const validatedConnectionString = (raw: string): string | ApiError => {
	const connectionString = raw.trim();
	if (!connectionString) return err('Connection string is required.');
	return connectionString;
};

const toCreatePayload = (input: ConnectionInput) => {
	const connectionString = validatedConnectionString(input.connectionString);
	if (typeof connectionString !== 'string') return connectionString;

	if (!input.tested) return err('Connection must be tested before creating.');

	const databaseName = parseConnectionDatabaseName(connectionString);
	if (!databaseName) return err('Could not determine database name from connection string.');

	return {
		name: databaseName,
		type: input.type,
		connectionString,
		database: { dbName: databaseName },
	};
};

export const connectionsApi = {
	getAll: (): Promise<ApiResponse<Connection[]>> =>
		requests.get<ResponseWithCount<Connection[]>>('connections'),

	create: async (input: ConnectionInput): Promise<CreateResponse> => {
		const payload = toCreatePayload(input);
		if ('error' in payload) return payload;
		return requests.post<{ data: Connection }>('connections', payload);
	},

	test: async (input: ConnectionInput): Promise<TestResponse> => {
		const connectionString = validatedConnectionString(input.connectionString);
		if (typeof connectionString !== 'string') return connectionString;

		const res = await requests.post<{ success: boolean }>('connections/test', {
			type: input.type,
			connectionString,
		});

		if (res.error || res.success !== true) {
			return err(res.message ?? 'Connection test failed.');
		}

		return { success: true };
	},

	delete: (id: string) =>
		requests.delete<{ data: { id: string } }>(`connections/${encodeURIComponent(id)}`),
};
