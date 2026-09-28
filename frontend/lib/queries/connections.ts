// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Where the configured connections live in the query cache.
 *
 * Not keyed by account, unlike zones: the list route strips credentials and
 * answers every role the same, so one entry serves whoever is signed in.
 */

import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { connectionsApi } from '@/api/connections';
import type { Connection } from '@/types/connection';

const LIST_KEY = ['connections', 'list'] as const;

export const connectionQueries = {
	list: () =>
		queryOptions({
			queryKey: LIST_KEY,
			queryFn: async (): Promise<Connection[]> => {
				const res = await connectionsApi.getAll();
				if (res.error) throw new Error(res.message ?? 'Failed to load connections.');
				return res.data ?? [];
			},
		}),
};

/**
 * Rewrites the connections held, for a caller that has just written one.
 *
 * What the SSO toggle updates ahead of its response and puts back when the
 * write is refused, and what a delete drops the row through.
 *
 * A read already in flight is cancelled first, because it would otherwise
 * land after this and put back what it was told before the write: closing the
 * connection wizard invalidates this key, so a toggle clicked while that
 * refetch is still out is an ordinary sequence rather than a contrived one.
 * Callers must await it — the point is to have won the race before the
 * request that follows starts.
 */
export const patchConnectionList = async (
	queryClient: QueryClient,
	update: (connections: Connection[]) => Connection[],
): Promise<void> => {
	await queryClient.cancelQueries({ queryKey: LIST_KEY });
	queryClient.setQueryData(LIST_KEY, (held: Connection[] | undefined) => update(held ?? []));
};

/**
 * Marks the connections stale.
 *
 * What the create/edit wizard calls: it answers with the one connection it
 * wrote, while the list also carries state the backend derives — so re-reading
 * is the honest way to redraw it.
 */
export const invalidateConnectionList = (queryClient: QueryClient): Promise<void> =>
	queryClient.invalidateQueries({ queryKey: LIST_KEY });
