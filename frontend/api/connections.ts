// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { parseConnectionDatabaseName } from '@/lib/parseConnectionDatabaseName';
import { requests } from './requests';
import type { Connection } from '@/types/connection';
import type { ConnectionInput } from '@/types/connectionInput';
import type { ApiError, ResponseWithCount, ResponseWithError } from './types';

type ListResponse = ResponseWithError<ResponseWithCount<Connection[]>>;
type CreateResponse = ApiError | { data: Connection };
type DeleteResponse = ResponseWithError<{ data: { id: string } }>;
type TestResponse = ApiError | { data: ConnectionTestResult[] };

export type ConnectionTestResult = {
	db_name: string;
	schemas: string[];
};

type ConnectionPayload = {
	name: string;
	type: string;
	connectionString: string;
};

const toPayload = (input: ConnectionInput): ConnectionPayload | ApiError => {
	const connectionString = input.connectionString.trim();
	const name = parseConnectionDatabaseName(connectionString);
	if (!name) {
		return {
			error: true,
			message: 'Could not determine database name from connection string.',
		};
	}

	return {
		name,
		type: input.type,
		connectionString,
	};
};

export const connectionsApi = {
	getAll: (): Promise<ListResponse> =>
		requests.get<ResponseWithCount<Connection[]>>('connections'),

	create: async (input: ConnectionInput): Promise<CreateResponse> => {
		const payload = toPayload(input);
		if ('error' in payload && payload.error === true) {
			return { error: true, message: payload.message };
		}
		return requests.post<{ data: Connection }>('connections', payload);
	},

	test: async (input: ConnectionInput): Promise<TestResponse> => {
		const connectionString = input.connectionString.trim();
		if (!connectionString) {
			return { error: true, message: 'Connection string is required.' };
		}
		return requests.post<{ data: ConnectionTestResult[] }>('connections/test', {
			type: input.type,
			connectionString,
		});
	},

	delete: (id: string): Promise<DeleteResponse> =>
		requests.delete<{ data: { id: string } }>(`connections/${encodeURIComponent(id)}`),
};
