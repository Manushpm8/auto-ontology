// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { CustomAnalysis } from '@/types/analysis';
import type { ResponseWithCount, ResponseWithError } from './types';

type ListResponse = ResponseWithError<ResponseWithCount<CustomAnalysis[]>>;

export type CustomAnalysisCreatePayload = {
	name: string;
	description: string;
	sql: string;
};

type CreateResponse = ResponseWithError<{ data: CustomAnalysis }>;
type UpdateResponse = ResponseWithError<{ data: CustomAnalysis }>;
type DeleteResponse = ResponseWithError<{ data: { id: string } }>;
type ValidateResponse = ResponseWithError<{ data: { valid: boolean; sql: string } }>;

export const analyses = {
	list: (): Promise<ListResponse> =>
		requests.get<ResponseWithCount<CustomAnalysis[]>>('custom-analyses'),
	validate: (sql: string): Promise<ValidateResponse> =>
		requests.post<{ data: { valid: boolean; sql: string } }>('custom-analyses/validate', {
			sql,
		}),
	create: (payload: CustomAnalysisCreatePayload): Promise<CreateResponse> =>
		requests.post<{ data: CustomAnalysis }>('custom-analyses', payload),
	update: (id: string, payload: CustomAnalysisCreatePayload): Promise<UpdateResponse> =>
		requests.put<{ data: CustomAnalysis }>(
			`custom-analyses/${encodeURIComponent(id)}`,
			payload,
		),
	delete: (id: string): Promise<DeleteResponse> =>
		requests.delete<{ data: { id: string } }>(`custom-analyses/${encodeURIComponent(id)}`),
};
