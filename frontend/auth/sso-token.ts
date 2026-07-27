// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Resolve the caller's SSO token so the backend can exchange it for a Databricks
// access token and run their queries under their own Unity Catalog grants.
//
// Whether that exchange actually happens is a per-connection setting the backend
// owns ("Authenticate as signed-in user"). We just supply the token when one is
// available; the backend decides whether it is required.
//
// Two kinds of caller reach the chat API:
//   * Service callers (e.g. AI-Q) already present `Authorization: Bearer <jwt>` —
//     that token is forwarded verbatim.
//   * Browser users authenticate with a Better Auth session cookie and carry no
//     bearer token, so we read the JWT Better Auth stored on their SSO account
//     link at sign-in.
//
// Password (`credential`) accounts have no SSO token by construction, so an
// admin signed in with email+password gets none — with the feature enabled the
// chat request is refused rather than run under the connection's stored PAT.

import { getPrisma } from '@/lib/prisma';

/** Better Auth's providerId for local email+password accounts (never an SSO link). */
const CREDENTIAL_PROVIDER = 'credential';

const extractBearer = (headers: Headers): string | null => {
	const header = headers.get('authorization');
	if (!header) return null;
	const [scheme, token] = header.split(' ');
	if (scheme?.toLowerCase() !== 'bearer' || !token) return null;
	return token.trim() || null;
};

/**
 * Return the SSO JWT for the current request, or null when none is available.
 *
 * Prefers a forwarded bearer token, then the id token Better Auth persisted on
 * the user's SSO account link, then that account's access token.
 */
export async function resolveSubjectToken(
	headers: Headers,
	userId: string,
): Promise<string | null> {
	const forwarded = extractBearer(headers);
	if (forwarded) return forwarded;

	const account = await getPrisma().account.findFirst({
		where: { userId, providerId: { not: CREDENTIAL_PROVIDER } },
		select: { idToken: true, accessToken: true, accessTokenExpiresAt: true },
		orderBy: { updatedAt: 'desc' },
	});
	if (!account) return null;

	if (account.idToken) return account.idToken;

	// Only offer the access token while it is still valid — an expired one would
	// just fail the exchange downstream with a less obvious error.
	const expired =
		account.accessTokenExpiresAt != null &&
		account.accessTokenExpiresAt.getTime() <= Date.now();
	return !expired && account.accessToken ? account.accessToken : null;
}
