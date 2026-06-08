// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import type { ChangeEvent } from 'react';
import { connectionDisplayName } from '@/enums/connection';
import { parseConnectionDatabaseName } from '@/lib/parseConnectionDatabaseName';
import type { ConnectionInput } from '@/types/connectionInput';

export type ConnectionConnectStepProps = {
	connectionInput: ConnectionInput;
	onChange: (patch: Partial<ConnectionInput>) => void;
	loading?: boolean;
	testSuccessMessage?: string | null;
	onTestConnection?: () => void;
	testDisabled?: boolean;
	testingConnection?: boolean;
};

const connectionStringPlaceholder: Record<ConnectionInput['type'], string> = {
	postgresql: 'postgresql://user:password@host:5432/database',
	snowflake: 'snowflake://user:password@account?warehouse=COMPUTE_WH&database=MY_DATABASE',
};

export const ConnectionConnectStep = ({
	connectionInput,
	onChange,
	loading = false,
	testSuccessMessage = null,
	onTestConnection,
	testDisabled = false,
	testingConnection = false,
}: ConnectionConnectStepProps) => {
	const databaseName = parseConnectionDatabaseName(connectionInput.connectionString);

	const onConnectionStringChange = ({ target }: ChangeEvent<HTMLInputElement>) => {
		onChange({ connectionString: target.value });
	};

	if (loading) {
		return (
			<div className="flex flex-1 items-center justify-center">
				<div
					className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900] dark:border-zinc-700"
					role="status"
					aria-label="Loading connection"
				/>
			</div>
		);
	}

	const testButtonDisabled = testDisabled || testingConnection;

	return (
		<div className="flex flex-1 flex-col gap-4 p-2">
			<p className="text-sm text-zinc-600 dark:text-zinc-400">
				Connect to {connectionDisplayName[connectionInput.type]}
			</p>
			<label className="flex flex-col gap-1.5">
				<span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
					Connection string
				</span>
				<input
					type="text"
					value={connectionInput.connectionString}
					onChange={onConnectionStringChange}
					placeholder={connectionStringPlaceholder[connectionInput.type]}
					className="rounded-lg border border-zinc-300 bg-white px-3 py-2 font-mono text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
				/>
			</label>
			{databaseName ? (
				<p className="text-sm text-zinc-600 dark:text-zinc-400">
					Connection name:{' '}
					<span className="font-medium text-zinc-800 dark:text-zinc-200">
						{databaseName}
					</span>
				</p>
			) : (
				connectionInput.connectionString.trim().length > 0 && (
					<p className="text-sm text-red-600 dark:text-red-400">
						Could not determine database name from connection string.
					</p>
				)
			)}
			<div className="mt-auto flex flex-col items-start">
				{onTestConnection != null && (
					<button
						type="button"
						data-testid="stepper-button-Test Connection"
						onClick={onTestConnection}
						disabled={testButtonDisabled}
						className="cursor-pointer rounded-lg border border-[#76b900] bg-white px-4 py-2 text-sm font-medium text-[#5e9400] transition-colors hover:bg-[#76b900]/10 disabled:cursor-default disabled:border-zinc-300 disabled:text-zinc-400 dark:border-[#76b900] dark:bg-zinc-900 dark:hover:bg-[#76b900]/20 disabled:dark:border-zinc-600"
					>
						{testingConnection ? 'Test Connection…' : 'Test Connection'}
					</button>
				)}
				{testSuccessMessage != null && (
					<p className="text-sm text-[#5e9400] dark:text-[#8fd100]">
						{testSuccessMessage}
					</p>
				)}
			</div>
		</div>
	);
};
