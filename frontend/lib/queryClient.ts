// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { QueryClient } from '@tanstack/react-query';

const SECOND = 1000;
const MINUTE = 60 * SECOND;

// Cache tuning constants. Centralised so we don't sprinkle magic numbers
// across query hooks and to make the freshness/eviction policy easy to
// audit in one place.
export const CACHE = {
	// How long server-state stays "fresh" by default — within this window
	// React Query serves the cache and skips background refetches.
	LIST_STALE: 30 * SECOND,
	// How long detail snapshots linger in memory after the last consumer
	// unmounts before being garbage collected.
	DETAIL_GC: 5 * MINUTE,
} as const;

const makeQueryClient = (): QueryClient =>
	new QueryClient({
		defaultOptions: {
			queries: {
				staleTime: CACHE.LIST_STALE,
				refetchOnWindowFocus: false,
				retry: 1,
			},
		},
	});

// Next.js App Router runs client components on the server during SSR/RSC
// too, so a module-scoped ``new QueryClient()`` would be shared between
// concurrent server requests — leaking cache between users. The canonical
// fix: build a fresh client per request on the server and a single client
// per tab in the browser. Non-React callers (e.g. the chat store) should
// always call ``getQueryClient()`` to grab the same browser instance the
// React tree uses.
let browserQueryClient: QueryClient | undefined;

export const getQueryClient = (): QueryClient => {
	if (typeof window === 'undefined') {
		return makeQueryClient();
	}
	if (!browserQueryClient) {
		browserQueryClient = makeQueryClient();
	}
	return browserQueryClient;
};
