// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { betterAuth } from 'better-auth';
import { prismaAdapter } from 'better-auth/adapters/prisma';
import { admin } from 'better-auth/plugins/admin';
import { nextCookies } from 'better-auth/next-js';
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

// Origins of external IdPs the SSO plugin is allowed to fetch OIDC discovery
// documents from. Better Auth always trusts the app's own baseURL; these are
// additional trusted origins. NVIDIA login is included by default; add more
// (comma-separated origins) via SSO_TRUSTED_ORIGINS.
const ssoTrustedOrigins = [
	'https://login.nvidia.com',
	...(process.env.SSO_TRUSTED_ORIGINS?.split(',')
		.map((origin) => origin.trim())
		.filter(Boolean) ?? []),
];

export const auth = betterAuth({
	// Project-scoped env var names, wired explicitly so they aren't tied to
	// Better Auth's BETTER_AUTH_* defaults.
	secret: process.env.AUTH_SECRET ?? (isBuildPhase ? 'next-build-time-placeholder' : undefined),
	baseURL: process.env.APP_URL,
	// Trust external IdP origins (e.g. NVIDIA login) so the SSO plugin may fetch
	// their OIDC discovery endpoints. The app's own baseURL is always trusted.
	trustedOrigins: ssoTrustedOrigins,
	database: prismaAdapter(prisma, { provider: 'postgresql' }),
	// Email/password sign-IN is enabled, but self-service sign-UP is disabled:
	// the only credential account is the bootstrap admin seeded from
	// GSF_ADMIN_EMAIL / GSF_ADMIN_PASSWORD (see lib/seed-admin.ts). Further users
	// are added by an admin or provisioned via SSO.
	emailAndPassword: { enabled: true, disableSignUp: true },
	plugins: [
		// Two roles only: `admin` (user management) and `viewer` (everything else).
		admin({ ac, roles, adminRoles: [Role.Admin], defaultRole: Role.Viewer }),
		// OIDC providers are registered at runtime via the admin UI and stored in
		// the `ssoProvider` table — there are no SSO env vars.
		sso(),
		// Must be the last plugin so it can set cookies on outgoing responses.
		nextCookies(),
	],
	databaseHooks: {
		user: {
			create: {
				// First account ever created becomes the admin and is marked
				// email-verified (so it can later link an SSO identity to the same
				// account); everyone else is a viewer. The unique email constraint
				// guards against a duplicate-account race; the count check assigns
				// the role.
				before: async (user) => {
					const isFirstUser = (await prisma.user.count()) === 0;
					return {
						data: isFirstUser
							? { ...user, role: Role.Admin, emailVerified: true }
							: { ...user, role: Role.Viewer },
					};
				},
			},
		},
	},
});
