// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import {
	globalSearchCountsFromResponse,
	globalSearchItemsFromResponse,
	searchApi,
} from '@/api/search';
import { Placeholders } from '@/assets/images/placeholders';
import { EmptyState } from '@/common/EmptyState';
import { searchObjectTypeFromHit } from '@/common/globalSearchMeta';
import { GlobalSearchResults, GlobalSearchResultsSkeleton } from '@/common/GlobalSearchResults';
import {
	GlobalSearchTabs,
	GlobalSearchTabsSkeleton,
	isSearchObjectType,
	totalGlobalSearchCount,
} from '@/common/GlobalSearchTabs';
import { Modal } from '@/common/modal';
import { SearchInput } from '@/common/SearchInput';
import { EmptyStateVariant } from '@/enums/emptyState';
import { GLOBAL_SEARCH_ALL_TAB, GLOBAL_SEARCH_LIST_LIMIT, TextMatchOption } from '@/enums/search';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import type { GlobalSearchItem } from '@/types/search';

export const GlobalSearch = () => {
	const [open, setOpen] = useState(false);
	const [query, setQuery] = useState('');
	const [selectedTab, setSelectedTab] = useState(GLOBAL_SEARCH_ALL_TAB);
	const [items, setItems] = useState<GlobalSearchItem[]>([]);
	const [counts, setCounts] = useState<Record<string, number>>({});
	const [resultKey, setResultKey] = useState('');
	const [countsKey, setCountsKey] = useState('');
	const debouncedQuery = useDebouncedValue(query, 1000);
	const trimmedQuery = debouncedQuery.trim();
	const liveQuery = query.trim();
	const searching = open && trimmedQuery.length >= 2;
	const listKey = `${trimmedQuery}::${selectedTab}`;
	const loading = searching && resultKey !== listKey;
	const queryChanging = open && liveQuery.length >= 2 && liveQuery !== trimmedQuery;
	const awaitingSearch = queryChanging || loading;
	const countsReady = searching && countsKey === trimmedQuery;

	useEffect(() => {
		if (!open || trimmedQuery.length < 2) return;

		const abort = new AbortController();
		const objects =
			selectedTab !== GLOBAL_SEARCH_ALL_TAB && isSearchObjectType(selectedTab)
				? [selectedTab]
				: undefined;
		void searchApi
			.globalSearch(
				{
					search_term: trimmedQuery,
					text_match_option: TextMatchOption.Contains,
					filters: { description: true, objects },
				},
				abort,
			)
			.then((response) => {
				if (abort.signal.aborted) return;
				setItems(globalSearchItemsFromResponse(response));
				setResultKey(`${trimmedQuery}::${selectedTab}`);
			});

		return () => abort.abort();
	}, [open, trimmedQuery, selectedTab]);

	useEffect(() => {
		if (!open || trimmedQuery.length < 2) return;

		const abort = new AbortController();
		void searchApi
			.globalSearchCount(
				{
					search_term: trimmedQuery,
					text_match_option: TextMatchOption.Contains,
					filters: { description: true },
				},
				abort,
			)
			.then((response) => {
				if (abort.signal.aborted) return;
				setCounts(globalSearchCountsFromResponse(response));
				setCountsKey(trimmedQuery);
			});

		return () => abort.abort();
	}, [open, trimmedQuery]);

	const handleQueryChange = (value: string) => {
		setQuery(value);
		setSelectedTab(GLOBAL_SEARCH_ALL_TAB);
		if (value.trim().length < 2) {
			setItems([]);
			setCounts({});
			setResultKey('');
			setCountsKey('');
		}
	};

	const handleClose = () => {
		setOpen(false);
		setQuery('');
		setSelectedTab(GLOBAL_SEARCH_ALL_TAB);
		setItems([]);
		setCounts({});
		setResultKey('');
		setCountsKey('');
	};

	const queryActive = liveQuery.length >= 2;
	const visibleItems = searching && !awaitingSearch && queryActive ? items : [];
	const showEmpty = searching && !awaitingSearch && queryActive && visibleItems.length === 0;
	const showPlaceholder = !queryActive;
	const itemCounts = visibleItems.reduce<Record<string, number>>((acc, item) => {
		const kind = searchObjectTypeFromHit(item);
		acc[kind] = (acc[kind] ?? 0) + 1;
		return acc;
	}, {});
	const tabCounts = totalGlobalSearchCount(counts) > 0 ? counts : itemCounts;
	const tabTotal =
		selectedTab === GLOBAL_SEARCH_ALL_TAB
			? totalGlobalSearchCount(tabCounts)
			: (tabCounts[selectedTab] ?? 0);
	const showTabs =
		queryActive && searching && !queryChanging && totalGlobalSearchCount(tabCounts) > 0;
	const showTabSkeleton = queryActive && awaitingSearch && !showTabs;
	const showResultSkeleton = queryActive && awaitingSearch;
	const showLimitBanner =
		visibleItems.length > 0 &&
		(visibleItems.length >= GLOBAL_SEARCH_LIST_LIMIT ||
			(countsReady && tabTotal > GLOBAL_SEARCH_LIST_LIMIT));

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
						onChange={handleQueryChange}
						placeholder="Search…"
						aria-label="Search GSF"
						autoFocus
						className="w-full"
					/>
				</div>
				{showTabs ? (
					<div className="shrink-0 border-b border-zinc-100 dark:border-zinc-800">
						<GlobalSearchTabs
							counts={tabCounts}
							selected={selectedTab}
							onSelect={setSelectedTab}
						/>
					</div>
				) : showTabSkeleton ? (
					<div className="shrink-0 border-b border-zinc-100 dark:border-zinc-800">
						<GlobalSearchTabsSkeleton />
					</div>
				) : null}
				{showLimitBanner ? (
					<p className="shrink-0 px-3 py-2 text-sm text-zinc-500 dark:text-zinc-400">
						Viewing top 200 results - Try filtering to get a more accurate search
						results
					</p>
				) : null}
				<div className="min-h-0 flex-1 overflow-y-auto" aria-busy={showResultSkeleton}>
					{showResultSkeleton ? <GlobalSearchResultsSkeleton /> : null}
					{visibleItems.length > 0 ? (
						<GlobalSearchResults
							items={visibleItems}
							query={trimmedQuery}
							onNavigate={handleClose}
						/>
					) : null}
					{showEmpty || showPlaceholder ? (
						<EmptyState
							variant={EmptyStateVariant.Borderless}
							illustration={<Placeholders.NoResults />}
							title="No Results Match Your Search"
						/>
					) : null}
				</div>
			</Modal>
		</>
	);
};
