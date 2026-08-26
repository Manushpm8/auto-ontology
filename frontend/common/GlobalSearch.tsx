// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import { Placeholders } from '@/assets/images/placeholders';
import { EmptyState } from '@/common/EmptyState';
import { Modal } from '@/common/modal';
import { SearchInput } from '@/common/SearchInput';
import { EmptyStateVariant } from '@/enums/emptyState';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

export const GlobalSearch = () => {
	const [open, setOpen] = useState(false);
	const [query, setQuery] = useState('');
	const debouncedQuery = useDebouncedValue(query, 1000);

	useEffect(() => {
		if (!open) return;
		console.log(debouncedQuery);
	}, [debouncedQuery, open]);

	const handleClose = () => {
		setOpen(false);
		setQuery('');
	};

	return (
		<>
			<button
				type="button"
				onClick={() => setOpen(true)}
				aria-label="Search GSF"
				className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-full bg-blue-50 px-3 text-sm text-blue-700 transition-colors hover:bg-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/40 dark:bg-blue-950/40 dark:text-blue-300 dark:hover:bg-blue-900/50"
			>
				<svg
					className="h-4 w-4 shrink-0"
					viewBox="0 0 20 20"
					fill="currentColor"
					aria-hidden
				>
					<path
						fillRule="evenodd"
						d="M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11ZM2 9a7 7 0 1 1 12.452 4.391l3.328 3.329a.75.75 0 1 1-1.06 1.06l-3.329-3.328A7 7 0 0 1 2 9Z"
						clipRule="evenodd"
					/>
				</svg>
				Search GSF
			</button>
			<Modal
				open={open}
				onClose={handleClose}
				align="top"
				overlayClassName="px-[200px] pb-4 pt-16"
				className="flex h-[min(40rem,80vh)] w-full flex-col overflow-hidden"
			>
				<div className="shrink-0 p-2">
					<SearchInput
						value={query}
						onChange={setQuery}
						placeholder="Search…"
						aria-label="Search GSF"
						autoFocus
						className="w-full"
					/>
				</div>
				<div className="flex min-h-0 flex-1 flex-col">
					<EmptyState
						variant={EmptyStateVariant.Borderless}
						illustration={<Placeholders.NoResults />}
						title="No Results Match Your Search"
					/>
				</div>
			</Modal>
		</>
	);
};
