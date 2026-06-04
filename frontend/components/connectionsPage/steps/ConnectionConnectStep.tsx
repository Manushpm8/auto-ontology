// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import type { ChangeEvent } from 'react';
import { connectionDisplayName } from '@/enums/connection';
import type { ConnectionDraft } from '@/types/connectionDraft';

export type ConnectionConnectStepProps = {
	draft: ConnectionDraft;
	onChange: (patch: Partial<ConnectionDraft>) => void;
	loading?: boolean;
};

const connectionStringPlaceholder: Record<ConnectionDraft['type'], string> = {
	postgresql: 'postgresql://user:password@host:5432/database',
	snowflake: 'snowflake://user:password@account/database',
};

export const ConnectionConnectStep = ({
	draft,
	onChange,
	loading = false,
}: ConnectionConnectStepProps) => {
	const onField =
		(field: keyof ConnectionDraft) =>
		({ target }: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
			onChange({ [field]: target.value });
		};

	if (loading) {
		return (
			<div className="flex min-h-[360px] items-center justify-center">
				<div
					className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900] dark:border-zinc-700"
					role="status"
					aria-label="Loading connection"
				/>
			</div>
		);
	}

	return (
		<div className="flex flex-col gap-4 p-2">
			<p className="text-sm text-zinc-600 dark:text-zinc-400">
				Connect to {connectionDisplayName[draft.type]}
			</p>
			<label className="flex flex-col gap-1.5">
				<span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
					Connection name
				</span>
				<input
					type="text"
					value={draft.name}
					onChange={onField('name')}
					placeholder="My warehouse connection"
					className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
				/>
			</label>
			<label className="flex flex-col gap-1.5">
				<span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
					Description
					<span className="font-normal text-zinc-500"> (optional)</span>
				</span>
				<textarea
					value={draft.description}
					onChange={onField('description')}
					rows={2}
					placeholder="Short description for this connection"
					className="resize-none rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
				/>
			</label>
			<label className="flex flex-col gap-1.5">
				<span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
					Connection string
				</span>
				<input
					type="text"
					value={draft.connectionString}
					onChange={onField('connectionString')}
					placeholder={connectionStringPlaceholder[draft.type]}
					className="rounded-lg border border-zinc-300 bg-white px-3 py-2 font-mono text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
				/>
			</label>
		</div>
	);
};
