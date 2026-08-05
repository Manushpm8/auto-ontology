// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useMemo, useState } from 'react';

import type { TablePagination } from '@/types/table';

/** Rows per page every paginated `Table` in the app uses. */
export const DEFAULT_PAGE_SIZE = 10;

type UsePaginationResult<T> = {
	/** The slice of `rows` belonging to the current page. */
	pageRows: T[];
	/** Ready to hand to `Table`'s `pagination` prop. */
	pagination: TablePagination;
};

/**
 * Slices `rows` into pages and keeps the current one, for a `Table` that would
 * otherwise render every row it was given.
 *
 * `resetKey` identifies what the rows belong to — the term a modal was opened
 * on, say. When it changes the paging starts over, since the page the previous
 * object was left on says nothing about this one. It is derived rather than
 * pushed through an effect, so switching costs no extra render.
 */
export function usePagination<T>(
	rows: T[],
	pageSize: number,
	resetKey: string | null = null,
): UsePaginationResult<T> {
	const [paged, setPaged] = useState({ key: resetKey, page: 1 });

	const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
	// Rows can also shrink under a page that is still current — a list narrowed
	// by a filter — which leaves the stored page out of range.
	const page = Math.min(paged.key === resetKey ? paged.page : 1, pageCount);

	const pageRows = useMemo(
		() => rows.slice((page - 1) * pageSize, page * pageSize),
		[rows, page, pageSize],
	);

	const onPageChange = useCallback(
		(next: number) => setPaged({ key: resetKey, page: next }),
		[resetKey],
	);

	return {
		pageRows,
		pagination: { page, pageSize, totalItems: rows.length, onPageChange },
	};
}
