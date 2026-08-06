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
	// Fields gated on another field's value (the Databricks credential sets) are only
	// rendered when that value matches, so the form shows one mode's inputs at a time.
	const fields = CONNECTION_FIELDS[connectionType].filter((field) =>
		field.visibleWhen == null
			? true
			: field.visibleWhen.values.includes(values[field.visibleWhen.key] ?? ''),
	);

	return (
		<div className="flex flex-1 flex-col gap-4 p-2">
			<p className="text-sm text-zinc-600 dark:text-zinc-400">
				Connect to {connectionDisplayName[connectionType]}
			</p>
			<div className="flex w-full flex-col gap-3">
				{fields.map((field) => {
					const onChange = ({ target }: ChangeEvent<HTMLInputElement>) =>
						onFieldChange(field.key, target.value);

					// Choice fields render as a dropdown; like every other field the
					// modal stores the selected value as a plain string.
					if (field.choices != null) {
						return (
							<label key={field.key} className="flex flex-col gap-1.5">
								<span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
									{field.label}
								</span>
								<select
									value={values[field.key] ?? field.defaultValue ?? ''}
									onChange={({ target }) =>
										onFieldChange(field.key, target.value)
									}
									data-testid={`connection-field-${field.key}`}
									className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
								>
									{field.choices.map((choice) => (
										<option key={choice.value} value={choice.value}>
											{choice.label}
										</option>
									))}
								</select>
								{field.hint != null && (
									<span className="text-xs text-zinc-500 dark:text-zinc-400">
										{field.hint}
									</span>
								)}
							</label>
						);
					}

					// Boolean fields render as a checkbox; the modal stores every
					// value as a string, so 'true'/'' is the on/off representation.
					if (field.boolean) {
						return (
							<label key={field.key} className="flex flex-col gap-1.5">
								<span className="flex items-center gap-2 text-sm font-medium text-zinc-800 dark:text-zinc-200">
									<input
										type="checkbox"
										checked={values[field.key] === 'true'}
										onChange={({ target }) =>
											onFieldChange(field.key, target.checked ? 'true' : '')
										}
										data-testid={`connection-field-${field.key}`}
										className="h-4 w-4 rounded border-zinc-300 accent-[#76b900] dark:border-zinc-600"
									/>
									{field.label}
								</span>
								{field.hint != null && (
									<span className="text-xs text-zinc-500 dark:text-zinc-400">
										{field.hint}
									</span>
								)}
							</label>
						);
					}

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
								value={values[field.key] ?? field.defaultValue ?? ''}
								onChange={onChange}
								placeholder={field.placeholder}
								autoComplete={field.secret ? 'new-password' : 'off'}
								className="rounded-lg border border-zinc-300 bg-white px-3 py-2 font-mono text-sm text-zinc-900 outline-none focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
							/>
							{field.hint != null && (
								<span className="text-xs text-zinc-500 dark:text-zinc-400">
									{field.hint}
								</span>
							)}
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
