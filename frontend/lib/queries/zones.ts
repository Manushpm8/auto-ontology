// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Where a user's zones live in the query cache.
 *
 * Keyed by the account they were read for, unlike the tag vocabulary: `zones`
 * answers with what that user may see, so two accounts sharing a browser
 * profile — or one signing out and another in — must not be shown each other's
 * list. The id in the key is what makes them separate entries rather than one
 * that the second user silently inherits.
 */

import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { zonesApi } from '@/api/zones';
import type { Zone } from '@/types/zones';

const listKey = (userId: string) => ['zones', 'list', userId] as const;

export const zoneQueries = {
	/**
	 * Works from both sides of the render: `requests` reaches the backend
	 * directly on the server and the permission-gated `/api/zones` route in the
	 * browser, and `uid` is sent either way — so the layout can prefetch
	 * exactly what the page then subscribes to.
	 */
	list: (userId: string) =>
		queryOptions({
			queryKey: listKey(userId),
			queryFn: async (): Promise<Zone[]> => {
				const res = await zonesApi.getAll(userId);
				if (res.error) throw new Error(res.message ?? 'Failed to load zones.');
				return res.data ?? [];
			},
		}),
};

/**
 * Rewrites the zones held for a user, for a caller that has just written one
 * and knows what changed. `zones` defaults to empty so a caller can splice
 * into a list that has not arrived without checking first.
 */
export const patchZoneList = (
	queryClient: QueryClient,
	userId: string,
	update: (zones: Zone[]) => Zone[],
): void => {
	queryClient.setQueryData(listKey(userId), (held: Zone[] | undefined) => update(held ?? []));
};

/** Marks a user's zones stale — for a write whose result is not worth guessing. */
export const invalidateZoneList = (queryClient: QueryClient, userId: string): Promise<void> =>
	queryClient.invalidateQueries({ queryKey: listKey(userId) });
