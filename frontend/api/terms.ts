// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type {
	ColumnAttribute,
	RelatedTermCount,
	SqlAttribute,
	Term,
	TermCount,
	TermDetail,
} from '@/types/terms';
import type { ResponseWithError } from './types';

type AttributeListResult = { data: ColumnAttribute[]; count: number };
type AttributeListResponse = ResponseWithError<AttributeListResult>;

type SqlAttributeListResult = { data: SqlAttribute[]; count: number };
type SqlAttributeListResponse = ResponseWithError<SqlAttributeListResult>;

type SingleResult = { data: TermDetail };
type SingleResponse = ResponseWithError<SingleResult>;
type UpdateResult = {
	data: {
		id: string;
		name: string;
		description: string | null;
		sample_values?: string[] | null;
	};
};
type UpdateResponse = ResponseWithError<UpdateResult>;
type TermUpdatePayload = { name?: string; description?: string | null };
type ColumnAttributeUpdatePayload = {
	name?: string;
	description?: string | null;
	sample_values?: string[];
};

type RelatedCountsResult = { data: RelatedTermCount[]; count: number };
type AttributeCountsResult = { data: TermCount[]; count: number };

type ListResult = {
	data: Term[];
	count: number;
	sql_attributes: SqlAttributeListResult;
	column_attribute_counts: AttributeCountsResult;
	sql_attribute_counts: AttributeCountsResult;
	related_counts: RelatedCountsResult;
};
type ListResponse = ResponseWithError<ListResult>;

export type TermsListParams = {
	/** Case-insensitive substring filter on the term name. */
	q?: string;
};

export const termsApi = {
	list: (params?: TermsListParams): Promise<ListResponse> =>
		requests.get<ListResult>('terms', params?.q ? { q: params.q } : {}),
	get: (id: string): Promise<SingleResponse> => requests.get<SingleResult>(`terms/${id}`),
	update: (id: string, payload: TermUpdatePayload): Promise<UpdateResponse> =>
		requests.patch<UpdateResult>(`terms/${encodeURIComponent(id)}`, payload),
	getColumnAttributes: (id: string): Promise<AttributeListResponse> =>
		requests.get<AttributeListResult>(`terms/${id}/column-attributes`),
	updateColumnAttribute: (
		termId: string,
		attrId: string,
		payload: ColumnAttributeUpdatePayload,
	): Promise<UpdateResponse> =>
		requests.patch<UpdateResult>(
			`terms/${encodeURIComponent(termId)}/column-attributes/${encodeURIComponent(attrId)}`,
			payload,
		),
	getSqlAttributes: (id: string): Promise<SqlAttributeListResponse> =>
		requests.get<SqlAttributeListResult>(`terms/${id}/sql-attributes`),
};
