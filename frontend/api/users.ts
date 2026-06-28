// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { authClient } from '@/lib/auth-client';
import { Role } from '@/enums/auth';

export type ManagedUser = {
	id: string;
	name: string;
	email: string;
	role: Role;
};

type ListResult = { users: ManagedUser[]; error: string | null };

export const usersApi = {
	list: async (): Promise<ListResult> => {
		const result = await authClient.admin.listUsers({ query: { limit: 200 } });
		if (result.error) {
			return { users: [], error: result.error.message ?? 'Failed to load users.' };
		}
		const users: ManagedUser[] = (result.data?.users ?? []).map((user) => ({
			id: user.id,
			name: user.name,
			email: user.email,
			// Every user has a role (the admin plugin assigns defaultRole on
			// create); fall back to Viewer defensively if it's ever missing.
			role: (user.role as Role) ?? Role.Viewer,
		}));
		return { users, error: null };
	},

	setRole: (userId: string, role: Role) => authClient.admin.setRole({ userId, role }),

	remove: (userId: string) => authClient.admin.removeUser({ userId }),
};
