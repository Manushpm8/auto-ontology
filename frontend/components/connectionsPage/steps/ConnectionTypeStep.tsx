// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { Icon, IconName } from '@/components/icons';
import { ConnectionType, connectionDisplayName } from '@/enums/connection';

export type ConnectionTypeStepProps = {
	onSelect: (type: ConnectionType) => void;
	disabledTypes?: ConnectionType[];
};

const CONNECTOR_TYPES = Object.values(ConnectionType).sort((a, b) =>
	connectionDisplayName[a].localeCompare(connectionDisplayName[b]),
);

export const ConnectionTypeStep = ({ onSelect, disabledTypes = [] }: ConnectionTypeStepProps) => {
	const disabledSet = new Set(disabledTypes);

	return (
		<div className="flex flex-col gap-3 p-2">
			{disabledTypes.length === CONNECTOR_TYPES.length ? (
				<p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-200">
					All connector types already have a connection. Remove an existing connection
					before creating a new one.
				</p>
			) : null}
			<div className="grid grid-cols-2 gap-3">
				{CONNECTOR_TYPES.map((type) => {
					const isDisabled = disabledSet.has(type);

					return (
						<button
							key={type}
							type="button"
							data-testid={`connection-type-${type}`}
							disabled={isDisabled}
							aria-disabled={isDisabled}
							onClick={() => {
								if (!isDisabled) {
									onSelect(type);
								}
							}}
							className={`flex h-[150px] flex-col items-center justify-center gap-2 rounded-lg border px-4 py-6 shadow-sm transition-colors ${
								isDisabled
									? 'cursor-not-allowed border-zinc-200 bg-zinc-100 opacity-50 dark:border-zinc-700 dark:bg-zinc-800/60'
									: 'cursor-pointer border-zinc-200 bg-white hover:border-[#76b900]/50 hover:bg-[#76b900]/5 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-[#76b900]/40'
							}`}
						>
							<Icon
								name={IconName.Database}
								className={`h-8 w-8 ${isDisabled ? 'text-zinc-400' : 'text-[#76b900]'}`}
							/>
							<span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
								{connectionDisplayName[type]}
							</span>
							{isDisabled ? (
								<span className="text-xs text-zinc-500 dark:text-zinc-400">
									Already connected
								</span>
							) : null}
						</button>
					);
				})}
			</div>
		</div>
	);
};
