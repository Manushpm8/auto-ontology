// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import type { ChangeEvent } from 'react';
import { Spinner } from '@nvidia/foundations-react-core';
import { Button } from '@/common/Button';
import { Size, ButtonTheme } from '@/enums/button';
import {
	CONNECTION_FIELDS,
	connectionDisplayName,
	type ConnectionFieldKey,
	type ConnectionType,
} from '@/enums/connection';

export type ConnectionConnectStepProps = {
	connectionType: ConnectionType;
	values: Partial<Record<ConnectionFieldKey, string>>;
	onFieldChange: (key: ConnectionFieldKey, value: string) => void;
	loading?: boolean;
	testSuccessMessage?: string | null;
	onTestConnection?: () => void;
	testDisabled?: boolean;
	testingConnection?: boolean;
};

export const ConnectionConnectStep = ({
	connectionType,
	values,
	onFieldChange,
	loading = false,
	testSuccessMessage = null,
	onTestConnection,
	testDisabled = false,
	testingConnection = false,
}: ConnectionConnectStepProps) => {
	if (loading) {
		return (
			<div className="flex flex-1 items-center justify-center">
				<Spinner aria-label="Loading connection" className="h-10 w-10" />
			</div>
		);
	}

	const testButtonDisabled = testDisabled || testingConnection;
	const fields = CONNECTION_FIELDS[connectionType];

	return (
		<div className="flex flex-1 flex-col gap-4 p-2">
			<p className="text-sm text-zinc-600 dark:text-zinc-400">
				Connect to {connectionDisplayName[connectionType]}
			</p>
			<div className="flex w-full flex-col gap-3">
				{fields.map((field) => {
					const onChange = ({ target }: ChangeEvent<HTMLInputElement>) =>
						onFieldChange(field.key, target.value);

					return (
						<label key={field.key} className="flex flex-col gap-1.5">
							<span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
								{field.label}
								{field.optional ? null : (
									<span className="ml-0.5 text-red-500">*</span>
								)}
							</span>
							<input
								type={field.secret ? 'password' : 'text'}
								value={values[field.key] ?? ''}
								onChange={onChange}
								placeholder={field.placeholder}
								autoComplete={field.secret ? 'new-password' : 'off'}
								className="rounded-lg border border-zinc-300 bg-white px-3 py-2 font-mono text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
							/>
						</label>
					);
				})}
			</div>
			<div className="mt-auto flex flex-col items-start">
				{onTestConnection != null && (
					<Button
						theme={ButtonTheme.Outline}
						size={Size.REGULAR}
						type="button"
						onClick={onTestConnection}
						disabled={testButtonDisabled}
					>
						{testingConnection ? 'Test Connection…' : 'Test Connection'}
					</Button>
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
