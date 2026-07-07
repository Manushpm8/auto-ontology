// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { SqlAttribute } from '@/types/terms';
import type { ResponseWithError } from './types';

type SingleResult = { data: SqlAttribute };
type SingleResponse = ResponseWithError<SingleResult>;

export type SqlAttributeCreatePayload = {
	name: string;
	description: string;
	expression: string;
	term_id: string;
	source?: string;
};

export type SqlAttributeValidatePayload = {
	expression: string;
};

type ValidateResult = { data: { valid: boolean; expression: string } };
type ValidateResponse = ResponseWithError<ValidateResult>;
type CreateResponse = ResponseWithError<SingleResult>;

export const sqlAttributesApi = {
	get: (id: string): Promise<SingleResponse> =>
		requests.get<SingleResult>(`sql-attributes/${id}`),
	validate: (payload: SqlAttributeValidatePayload): Promise<ValidateResponse> =>
		requests.post<ValidateResult>('sql-attributes/validate', payload),
	create: (payload: SqlAttributeCreatePayload): Promise<CreateResponse> =>
		requests.post<SingleResult>('sql-attributes', payload),
};
