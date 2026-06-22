// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { betterAuth } from 'better-auth';
import { prismaAdapter } from 'better-auth/adapters/prisma';
import { admin } from 'better-auth/plugins/admin';
import { nextCookies } from 'better-auth/next-js';
import { APIError, createAuthMiddleware } from 'better-auth/api';
import { sso } from '@better-auth/sso';
import { getPrisma } from '@/lib/prisma';
import { ac, roles } from '@/lib/auth-access';
import { Role } from '@/enums/auth';

const prisma = getPrisma();

// During `next build` the module is evaluated but no auth request is handled,
// so a real secret/origin isn't needed. Fall back to a build-only placeholder
// for the secret so construction doesn't fail; runtime values come from env.
const isBuildPhase = process.env.NEXT_PHASE === 'phase-production-build';

/** True once at least one SSO provider has been registered. */
export const isSsoConfigured = async (): Promise<boolean> => (await prisma.ssoProvider.count()) > 0;

export const auth = betterAuth({
	// Project-scoped env var names, wired explicitly so they aren't tied to
	// Better Auth's BETTER_AUTH_* defaults.
	secret: process.env.AUTH_SECRET ?? (isBuildPhase ? 'next-build-time-placeholder' : undefined),
	baseURL: process.env.APP_URL,
	database: prismaAdapter(prisma, { provider: 'postgresql' }),
	emailAndPassword: { enabled: true },
	plugins: [
		// Two roles only: `admin` (user management) and `viewer` (everything else).
		admin({ ac, roles, adminRoles: [Role.Admin], defaultRole: Role.Viewer }),
		// OIDC providers are registered at runtime via the admin UI and stored in
		// the `ssoProvider` table — there are no SSO env vars.
		sso(),
		// Must be the last plugin so it can set cookies on outgoing responses.
		nextCookies(),
	],
	hooks: {
		// Once SSO is configured, SSO becomes the only way in: disable local
		// email/password sign-up. (Local sign-up stays open beforehand so the
		// first admin can be bootstrapped.) Sign-in and SSO provisioning are
		// unaffected.
		before: createAuthMiddleware(async (ctx) => {
			if (ctx.path === '/sign-up/email' && (await isSsoConfigured())) {
				throw new APIError('FORBIDDEN', {
					message:
						'Sign-up is disabled because SSO is configured. Please sign in with SSO.',
				});
			}
		}),
	},
	databaseHooks: {
		user: {
			create: {
				// First account ever created becomes the admin; everyone else is a
				// viewer. The unique email constraint guards against a duplicate-account
				// race; the count check assigns the role.
				before: async (user) => {
					const count = await prisma.user.count();
					return {
						data: { ...user, role: count === 0 ? Role.Admin : Role.Viewer },
					};
				},
			},
		},
	},
});
