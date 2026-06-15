// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { Connection } from '@/types/connection';
import type { ConnectionInput } from '@/types/connection';
import type { ApiError, ApiResponse, ResponseWithCount } from './types';

type CreateResponse = ApiError | { data: Connection };
type TestResponse = ApiError | { success: true };

type CreateInput = ConnectionInput & { database: string };

const err = (message: string): ApiError => ({ error: true, message });

export const connectionsApi = {
	getAll: (): Promise<ApiResponse<Connection[]>> =>
		requests.get<ResponseWithCount<Connection[]>>('connections'),

	create: async (input: CreateInput): Promise<CreateResponse> => {
		return requests.post<{ data: Connection }>('connections', {
			type: input.type,
			connectionString: input.connectionString,
			database: input.database,
		});
	},

	test: async (input: ConnectionInput): Promise<TestResponse> => {
		const res = await requests.post<{ success: boolean }>('connections/test', {
			type: input.type,
			connectionString: input.connectionString,
		});

		if (res.error || res.success !== true) {
			return err(res.message ?? 'Connection test failed.');
		}

		return { success: true };
	},

	delete: (databaseName: string) =>
		requests.delete<{ data: { database_name: string } }>(
			`connections/${encodeURIComponent(databaseName)}`,
		),
};
