// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createAccessControl } from 'better-auth/plugins/access';
import { adminAc, defaultStatements } from 'better-auth/plugins/admin/access';
import { Role } from '@/enums/auth';

/**
 * Access-control statements + roles shared by the Better Auth server and client.
 * `admin` and `viewer` are the only roles. On top of the built-in `user` /
 * `session` resources (`defaultStatements`) we declare GSF's own resources, then
 * assign actions per role. Every protected API/page authorizes against these
 * (see `auth/permissions.ts`), so authorization is data here rather than scattered
 * `role === 'admin'` checks.
 */
const statement = {
	...defaultStatements,
	analytics: ['read'],
	acronym: ['read', 'create', 'update', 'delete'],
	prompt: ['read', 'create', 'update', 'delete'],
	sso: ['read', 'manage'],
	conversation: ['read', 'write', 'delete'],
	chat: ['use'],
} as const;

export const ac = createAccessControl(statement);

export const roles = {
	// Admin: full user/session management plus every GSF resource.
	[Role.Admin]: ac.newRole({
		...adminAc.statements,
		analytics: ['read'],
		acronym: ['read', 'create', 'update', 'delete'],
		prompt: ['read', 'create', 'update', 'delete'],
		sso: ['read', 'manage'],
		conversation: ['read', 'write', 'delete'],
		chat: ['use'],
	}),
	// Viewer: read-only on glossary/prompts, full control of their own
	// conversations, and may run chat. No analytics, SSO, or user management.
	[Role.Viewer]: ac.newRole({
		acronym: ['read'],
		prompt: ['read'],
		conversation: ['read', 'write', 'delete'],
		chat: ['use'],
	}),
};

/**
 * Shape accepted by a role's `authorize()` — a partial map of resource → the
 * actions being requested, e.g. `{ analytics: ['read'] }`. Both roles share the
 * same access-control statements, so either role's request type works.
 */
export type PermissionRequest = Parameters<(typeof roles)[Role.Admin]['authorize']>[0];
