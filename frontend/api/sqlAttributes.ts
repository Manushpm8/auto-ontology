// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { SqlAttribute } from '@/types/terms';
import type { ResponseWithError } from './types';

type SingleResult = { data: SqlAttribute };
type SingleResponse = ResponseWithError<SingleResult>;

export const sqlAttributesApi = {
	get: (id: string): Promise<SingleResponse> =>
		requests.get<SingleResult>(`sql-attributes/${id}`),
};
