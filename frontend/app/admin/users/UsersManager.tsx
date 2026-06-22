// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import { authClient } from '@/lib/auth-client';
import { Role } from '@/enums/auth';

type ManagedUser = {
	id: string;
	name: string;
	email: string;
	role: string | null | undefined;
	banned: boolean | null | undefined;
};

type ListResult = { users: ManagedUser[]; error: string | null };

const fetchUsers = async (): Promise<ListResult> => {
	const result = await authClient.admin.listUsers({ query: { limit: 200 } });
	if (result.error) {
		return { users: [], error: result.error.message ?? 'Failed to load users.' };
	}
	return { users: (result.data?.users ?? []) as ManagedUser[], error: null };
};

export const UsersManager = () => {
	const [users, setUsers] = useState<ManagedUser[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [busyId, setBusyId] = useState<string | null>(null);

	const adminCount = users.filter((user) => user.role === Role.Admin && !user.banned).length;

	useEffect(() => {
		fetchUsers().then((result) => {
			setUsers(result.users);
			setError(result.error);
			setLoading(false);
		});
	}, []);

	const runAction = async (id: string, action: () => Promise<{ error?: unknown }>) => {
		setBusyId(id);
		setError(null);
		const actionResult = await action();
		if (actionResult.error) {
			const message =
				typeof actionResult.error === 'object' &&
				actionResult.error &&
				'message' in actionResult.error
					? String((actionResult.error as { message?: string }).message)
					: 'Action failed.';
			setError(message);
		}
		const listed = await fetchUsers();
		setUsers(listed.users);
		if (listed.error) setError(listed.error);
		setBusyId(null);
	};

	const toggleRole = (user: ManagedUser) => {
		const nextRole = user.role === Role.Admin ? Role.Viewer : Role.Admin;
		return runAction(user.id, () =>
			authClient.admin.setRole({ userId: user.id, role: nextRole }),
		);
	};

	const toggleBan = (user: ManagedUser) =>
		runAction(user.id, () =>
			user.banned
				? authClient.admin.unbanUser({ userId: user.id })
				: authClient.admin.banUser({ userId: user.id }),
		);

	return (
		<div className="h-full overflow-auto p-6">
			<div className="mx-auto max-w-3xl">
				<h1 className="mb-1 text-lg font-semibold text-zinc-900 dark:text-zinc-100">
					Users
				</h1>
				<p className="mb-4 text-xs text-zinc-500">
					Manage roles and access. Admins manage users; viewers can access all other
					pages.
				</p>

				{error ? <p className="mb-3 text-xs text-red-500">{error}</p> : null}

				{loading ? (
					<p className="text-sm text-zinc-500">Loading…</p>
				) : (
					<div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
						<table className="w-full text-left text-sm">
							<thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
								<tr>
									<th className="px-4 py-2 font-medium">User</th>
									<th className="px-4 py-2 font-medium">Role</th>
									<th className="px-4 py-2 font-medium">Status</th>
									<th className="px-4 py-2 text-right font-medium">Actions</th>
								</tr>
							</thead>
							<tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
								{users.map((user) => {
									const isAdmin = user.role === Role.Admin;
									// Never let the last active admin be demoted or banned.
									const isLastAdmin = isAdmin && !user.banned && adminCount <= 1;
									const busy = busyId === user.id;
									return (
										<tr
											key={user.id}
											className="text-zinc-700 dark:text-zinc-300"
										>
											<td className="px-4 py-2">
												<div className="font-medium">
													{user.name || '—'}
												</div>
												<div className="text-xs text-zinc-400">
													{user.email}
												</div>
											</td>
											<td className="px-4 py-2">
												{isAdmin ? 'Admin' : 'Viewer'}
											</td>
											<td className="px-4 py-2">
												{user.banned ? (
													<span className="text-red-500">Disabled</span>
												) : (
													<span className="text-[#76b900]">Active</span>
												)}
											</td>
											<td className="px-4 py-2">
												<div className="flex justify-end gap-2">
													<button
														type="button"
														disabled={busy || isLastAdmin}
														onClick={() => toggleRole(user)}
														className="cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-600 dark:text-zinc-400 dark:hover:bg-zinc-800"
													>
														{isAdmin ? 'Make viewer' : 'Make admin'}
													</button>
													<button
														type="button"
														disabled={busy || isLastAdmin}
														onClick={() => toggleBan(user)}
														className="cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-600 dark:text-zinc-400 dark:hover:bg-zinc-800"
													>
														{user.banned ? 'Enable' : 'Disable'}
													</button>
												</div>
											</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				)}
			</div>
		</div>
	);
};
