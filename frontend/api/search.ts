// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import { TextMatchOption } from '@/enums/search';
import type { GlobalSearchItem, GlobalSearchRequest } from '@/types/search';
import type { ResponseWithCount, ResponseWithError } from './types';

type GlobalSearchListResponse = ResponseWithError<ResponseWithCount<GlobalSearchItem[]>>;
type GlobalSearchCountResponse = ResponseWithError<{ data: Record<string, number> }>;

const isRecord = (value: unknown): value is Record<string, unknown> =>
	value != null && typeof value === 'object' && !Array.isArray(value);

const readCountMap = (value: unknown): Record<string, number> => {
	if (!isRecord(value)) return {};
	if (isRecord(value.data)) {
		const nested = readCountMap(value.data);
		if (Object.keys(nested).length > 0) return nested;
	}
	const out: Record<string, number> = {};
	Object.entries(value).forEach(([key, raw]) => {
		if (key === 'data' || key === 'count' || key === 'error' || key === 'message') return;
		if (typeof raw === 'number' && Number.isFinite(raw)) out[key] = raw;
	});
	return out;
};

/** Unwrap the list envelope (or a nested `{ data }` copy) into hit rows. */
export const globalSearchItemsFromResponse = (
	response: GlobalSearchListResponse,
): GlobalSearchItem[] => {
	if (response.error) return [];
	const payload: unknown = response.data;
	if (Array.isArray(payload)) return payload;
	if (isRecord(payload) && Array.isArray(payload.data)) return payload.data as GlobalSearchItem[];
	return [];
};

/** Unwrap `{ data: { type: n } }` from `/search/global-search/count`. */
export const globalSearchCountsFromResponse = (
	response: GlobalSearchCountResponse,
): Record<string, number> => {
	if (response.error) return {};
	const fromData = readCountMap(response.data);
	if (Object.keys(fromData).length > 0) return fromData;
	return readCountMap(response);
};

const withDefaults = (payload: GlobalSearchRequest): GlobalSearchRequest => ({
	text_match_option: TextMatchOption.Contains,
	...payload,
	filters: { description: true, ...payload.filters },
});

export const searchApi = {
	globalSearch: (
		payload: GlobalSearchRequest,
		abortController?: AbortController,
	): Promise<GlobalSearchListResponse> =>
		requests.post<ResponseWithCount<GlobalSearchItem[]>>(
			'search/global-search',
			withDefaults(payload),
			abortController,
		),
	globalSearchCount: (
		payload: GlobalSearchRequest,
		abortController?: AbortController,
	): Promise<GlobalSearchCountResponse> =>
		requests.post<{ data: Record<string, number> }>(
			'search/global-search/count',
			withDefaults(payload),
			abortController,
		),
};
