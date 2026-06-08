// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { Icon, IconName } from '@/components/icons';
import { PopoverMenu } from '@/components/PopoverMenu';
import { formatConnectionTimestamp } from '@/lib/formatConnectionTimestamp';
import type { Connection } from '@/types/connection';

export type ConnectionInfoCardProps = {
	connection: Connection;
	onDelete?: (id: string, name: string) => void;
	disabled?: boolean;
};

export const ConnectionInfoCard = ({
	connection,
	onDelete,
	disabled = false,
}: ConnectionInfoCardProps) => {
	const menuDisabled = disabled || !onDelete;

	return (
		<article className="flex h-fit flex-col rounded-lg border border-zinc-200/90 bg-white shadow-sm dark:border-zinc-700/90 dark:bg-zinc-950">
			<header className="flex w-full items-center justify-between gap-2 px-4 py-5">
				<div className="flex min-w-0 items-center gap-1">
					<Icon name={IconName.Database} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<h3 className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
						{connection.name}
					</h3>
				</div>
				<div className="relative shrink-0">
					{menuDisabled ? (
						<Icon
							name={IconName.DotsVertical}
							className="h-4 w-4 text-zinc-300 dark:text-zinc-600"
						/>
					) : (
						<PopoverMenu
							className="relative"
							items={[
								{
									label: 'Remove',
									icon: <Icon name={IconName.Trash} className="h-3.5 w-3.5" />,
									onClick: () => onDelete(connection.id, connection.name),
									danger: true,
								},
							]}
							trigger={({ toggle }) => (
								<button
									type="button"
									onClick={toggle}
									className="cursor-pointer rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
									aria-label={`Actions for ${connection.name}`}
								>
									<Icon name={IconName.DotsVertical} className="h-4 w-4" />
								</button>
							)}
						/>
					)}
				</div>
			</header>
			<div className="flex w-full flex-col gap-2 border-t border-zinc-200/90 px-5 pb-5 pt-0 dark:border-zinc-700/90">
				<div className="flex h-8 w-full items-center justify-between gap-4">
					<span className="text-sm text-zinc-500 dark:text-zinc-400">Created Date</span>
					<span className="text-sm text-zinc-800 dark:text-zinc-200">
						{formatConnectionTimestamp(connection.create_date)}
					</span>
				</div>
				<div className="flex h-8 w-full items-center justify-between gap-4">
					<span className="text-sm text-zinc-500 dark:text-zinc-400">Last Pulled</span>
					<span className="text-sm text-zinc-800 dark:text-zinc-200">
						{formatConnectionTimestamp(connection.last_pulled)}
					</span>
				</div>
			</div>
		</article>
	);
};
