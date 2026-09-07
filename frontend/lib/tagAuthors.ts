// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Resolves the Better Auth user ids in a tag's `created_by` / `modified_by` to
 * the names the Tags settings page renders.
 *
 * Here, in the gateway, for two reasons. FastAPI cannot do it: the accounts live
 * in the `frontend` schema Prisma owns, which the backend has no model for and
 * deliberately does not reach into. And the browser should not: the admin
 * `listUsers` call is a second round trip returning a *page* of users, so every
 * author past that page would render as "Unknown" — which is also what a
 * deleted account renders as, making the two indistinguishable.
 *
 * The same join as the analytics report (`lib/apiSelects.ts`), for the same
 * reason: the row stores an id, and only the User table knows the name.
 */

import { getPrisma } from '@/lib/prisma';
import { SYSTEM_ACTOR } from '@/constants/tags';

/** Only what a row needs: a name to print, and an email to fall back to. */
const authorSelect = { id: true, name: true, email: true } as const;

type Row = Record<string, unknown>;

/** The tags in a `{ data }` envelope — one from a write, a list from a read. */
const tagsIn = (payload: Row): Row[] => {
	const rows = Array.isArray(payload.data) ? payload.data : [payload.data];
	return rows.filter((row): row is Row => typeof row === 'object' && row !== null);
};

/**
 * The id to look up, or null when there is nothing to look up: no author
 * recorded, or `SYSTEM_ACTOR`, which names no account and is rendered from the
 * sentinel itself.
 */
const lookupId = (value: unknown): string | null =>
	typeof value === 'string' && value !== '' && value !== SYSTEM_ACTOR ? value : null;

const respond = (body: string, upstream: Response): Response =>
	new Response(body, {
		status: upstream.status,
		headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
	});

/**
 * The backend's answer with `created_by_user` / `modified_by_user` added beside
 * the ids it stored.
 *
 * Every branch that cannot add them answers with the body unchanged rather than
 * failing: an error response, a body that is not the expected envelope, or a
 * failed user lookup. A tag whose author will not resolve still reads correctly
 * as "Unknown", whereas a 500 here would lose the whole list over a decoration.
 */
export const withTagAuthors = async (upstream: Response): Promise<Response> => {
	const body = await upstream.text();
	if (!upstream.ok) return respond(body, upstream);

	let payload: unknown;
	try {
		payload = JSON.parse(body);
	} catch {
		return respond(body, upstream);
	}
	if (typeof payload !== 'object' || payload === null) return respond(body, upstream);

	// Mutated in place below, so the envelope keeps whatever else it carries —
	// a list's `count`, a tag detail's `items`.
	const rows = tagsIn(payload as Row);
	const ids = [
		...new Set(
			rows
				.flatMap((row) => [lookupId(row.created_by), lookupId(row.modified_by)])
				.filter((id): id is string => id !== null),
		),
	];
	if (ids.length === 0) return respond(body, upstream);

	let authors: Map<string, unknown>;
	try {
		const users = await getPrisma().user.findMany({
			where: { id: { in: ids } },
			select: authorSelect,
		});
		authors = new Map(users.map((user) => [user.id, user]));
	} catch {
		return respond(body, upstream);
	}

	for (const row of rows) {
		const createdBy = lookupId(row.created_by);
		const modifiedBy = lookupId(row.modified_by);
		row.created_by_user = createdBy === null ? null : (authors.get(createdBy) ?? null);
		row.modified_by_user = modifiedBy === null ? null : (authors.get(modifiedBy) ?? null);
	}

	return respond(JSON.stringify(payload), upstream);
};
