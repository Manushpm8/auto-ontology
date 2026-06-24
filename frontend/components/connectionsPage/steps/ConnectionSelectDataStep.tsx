// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import type { ChangeEvent } from 'react';

export type ConnectionSelectDataStepProps = {
	availableDatabases: string[];
	selectedDatabases: string[];
	onSelectionChange: (databases: string[]) => void;
};

export const ConnectionSelectDataStep = ({
	availableDatabases,
	selectedDatabases,
	onSelectionChange,
}: ConnectionSelectDataStepProps) => {
	if (availableDatabases.length === 0) {
		return (
			<div className="flex min-h-[360px] flex-col items-center justify-center gap-2 p-6 text-center">
				<p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
					No databases to select
				</p>
				<p className="max-w-sm text-sm text-zinc-500 dark:text-zinc-400">
					No databases were discovered for this connection. You can still create it and
					run ingest later.
				</p>
			</div>
		);
	}

	const toggle =
		(name: string) =>
		({ target }: ChangeEvent<HTMLInputElement>) => {
			if (target.checked) {
				onSelectionChange([...selectedDatabases, name]);
			} else {
				onSelectionChange(selectedDatabases.filter((d) => d !== name));
			}
		};

	return (
		<div className="flex flex-col gap-2 p-2">
			<p className="text-sm text-zinc-600 dark:text-zinc-400">
				Select databases or schemas to include in the catalog.
			</p>
			<ul className="flex max-h-[360px] flex-col gap-1 overflow-y-auto rounded-lg border border-zinc-200 p-2 dark:border-zinc-700">
				{availableDatabases.map((name) => (
					<li key={name}>
						<label className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-800/80">
							<input
								type="checkbox"
								checked={selectedDatabases.includes(name)}
								onChange={toggle(name)}
								className="h-4 w-4 rounded border-zinc-300 text-[#76b900] focus:ring-[#76b900]/30"
							/>
							<span className="text-sm text-zinc-800 dark:text-zinc-200">{name}</span>
						</label>
					</li>
				))}
			</ul>
		</div>
	);
};
