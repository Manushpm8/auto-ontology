// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import { TextMatchOption } from '@/enums/search';
import type { DiscoverySearchItem, DiscoverySearchRequest } from '@/types/search';
import type { ResponseWithCount, ResponseWithError } from './types';

type DiscoveryListResponse = ResponseWithError<ResponseWithCount<DiscoverySearchItem[]>>;
type DiscoveryCountResponse = ResponseWithError<{ data: Record<string, number> }>;

/** Unwrap the list envelope (or a nested `{ data }` copy) into hit rows. */
export const discoveryItemsFromResponse = (
	response: DiscoveryListResponse,
): DiscoverySearchItem[] => {
	if (response.error) return [];
	const payload: unknown = response.data;
	if (Array.isArray(payload)) return payload;
	if (payload != null && typeof payload === 'object' && 'data' in payload) {
		const nested = (payload as { data?: unknown }).data;
		if (Array.isArray(nested)) return nested;
	}
	return [];
};

/** Unwrap `{ data: { type: n } }` from `/search/discovery/count`. */
export const discoveryCountsFromResponse = (
	response: DiscoveryCountResponse,
): Record<string, number> => {
	if (response.error) return {};
	const payload: unknown = response.data;
	if (payload == null || typeof payload !== 'object' || Array.isArray(payload)) return {};
	const out: Record<string, number> = {};
	Object.entries(payload as Record<string, unknown>).forEach(([key, value]) => {
		if (typeof value === 'number') out[key] = value;
	});
	return out;
};

const withDefaults = (payload: DiscoverySearchRequest): DiscoverySearchRequest => ({
	text_match_option: TextMatchOption.Contains,
	...payload,
	filters: { description: true, ...payload.filters },
});

export const searchApi = {
	discovery: (
		payload: DiscoverySearchRequest,
		abortController?: AbortController,
	): Promise<DiscoveryListResponse> =>
		requests.post<ResponseWithCount<DiscoverySearchItem[]>>(
			'search/discovery',
			withDefaults(payload),
			abortController,
		),
	discoveryCount: (
		payload: DiscoverySearchRequest,
		abortController?: AbortController,
	): Promise<DiscoveryCountResponse> =>
		requests.post<{ data: Record<string, number> }>(
			'search/discovery/count',
			withDefaults(payload),
			abortController,
		),
};
