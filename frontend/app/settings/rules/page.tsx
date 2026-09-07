// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useState } from 'react';

import { Placeholders } from '@/assets/images/placeholders';
import { EmptyState } from '@/common/EmptyState';
import { GlobalSearchModal } from '@/common/GlobalSearch';
import { EmptyStateVariant } from '@/enums/emptyState';

/**
 * Rules are not authored here: they are built from a global search, over the
 * items the search returns. So this screen only lists them — and until there is
 * a list, it hands people the search instead of a create button that would have
 * nothing to act on.
 */
export default function RulesSettingsPage() {
	const [searchOpen, setSearchOpen] = useState(false);

	return (
		<main className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-[linear-gradient(180deg,rgba(255,255,255,1)_0%,rgba(250,250,250,0.6)_100%)] px-7 py-6 sm:px-10 sm:py-7 dark:bg-[linear-gradient(180deg,rgba(9,9,11,1)_0%,rgba(24,24,27,0.5)_100%)]">
			<div className="w-full space-y-5">
				<h1 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">Rules</h1>

				<EmptyState
					variant={EmptyStateVariant.Dashed}
					illustration={<Placeholders.NoRules />}
					title="No Rules Created Yet"
					description="Rules are created from the global search. Search for the items you would like to tag, then create a new rule from the results."
					action={{
						label: 'Open Global Search',
						onClick: () => setSearchOpen(true),
					}}
				/>
			</div>

			<GlobalSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
		</main>
	);
}
