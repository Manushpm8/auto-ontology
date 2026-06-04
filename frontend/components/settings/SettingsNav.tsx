// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export const SETTINGS_NAV_ITEMS = [
	{ label: 'Custom Properties', href: '/settings/custom-properties' },
	{ label: 'Connections', href: '/settings/connections' },
] as const;

function rowClassName(selected: boolean) {
	return `flex min-h-9 items-center rounded-lg px-3 text-sm no-underline transition-colors ${
		selected
			? 'bg-[#76b900]/15 font-medium text-zinc-900 shadow-sm ring-1 ring-[#76b900]/30 dark:text-zinc-100'
			: 'text-zinc-800 hover:bg-zinc-100/90 dark:text-zinc-200 dark:hover:bg-zinc-800/70'
	}`;
}

export const SettingsNav = () => {
	const pathname = usePathname();

	return (
		<nav
			className="flex min-h-0 flex-1 flex-col gap-y-1 overflow-y-auto px-3 py-2 sm:px-4"
			aria-label="Settings sections"
		>
			{SETTINGS_NAV_ITEMS.map((item) => {
				const selected = pathname === item.href || pathname.startsWith(`${item.href}/`);
				return (
					<Link key={item.href} href={item.href} className={rowClassName(selected)}>
						{item.label}
					</Link>
				);
			})}
		</nav>
	);
};
