// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useMemo, useState } from 'react';
import type { ParsedTable } from '@/lib/parseSqlResponse';

const PAGE_SIZE = 10;

type DynamicTableProps = {
	table: ParsedTable;
};

export const DynamicTable = ({ table }: DynamicTableProps) => {
	const { columns, rows } = table;
	const [pageIndex, setPageIndex] = useState(0);
	const [prevRows, setPrevRows] = useState(rows);

	// Reset pagination during render when the rows reference changes,
	// per React guidance on adjusting state in response to prop changes.
	if (rows !== prevRows) {
		setPrevRows(rows);
		setPageIndex(0);
	}

	const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));

	const paginatedRows = useMemo(() => {
		const start = pageIndex * PAGE_SIZE;
		return rows.slice(start, start + PAGE_SIZE);
	}, [rows, pageIndex]);

	if (columns.length === 0 || rows.length === 0) {
		return <p className="text-xs text-zinc-400">No data available</p>;
	}

	const rangeStart = pageIndex * PAGE_SIZE + 1;
	const rangeEnd = Math.min(rows.length, (pageIndex + 1) * PAGE_SIZE);

	return (
		<div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900">
			<div className="max-h-[478px] overflow-auto">
				<table className="w-full border-collapse text-xs">
					<thead className="sticky top-0 bg-zinc-50 dark:bg-zinc-800">
						<tr>
							{columns.map((col) => (
								<th
									key={col}
									className="border-b border-zinc-200 px-3 py-2 text-left font-semibold text-zinc-700 dark:border-zinc-700 dark:text-zinc-200"
								>
									{col}
								</th>
							))}
						</tr>
					</thead>
					<tbody>
						{paginatedRows.map((row, rowIdx) => (
							<tr
								key={rowIdx}
								className="border-b border-zinc-100 last:border-b-0 dark:border-zinc-800"
							>
								{columns.map((col) => (
									<td
										key={col}
										className="max-w-[240px] truncate px-3 py-1.5 text-zinc-700 dark:text-zinc-300"
										title={row[col]}
									>
										{row[col]}
									</td>
								))}
							</tr>
						))}
					</tbody>
				</table>
			</div>
			{rows.length > PAGE_SIZE && (
				<div className="flex items-center justify-between border-t border-zinc-200 bg-zinc-50 px-3 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
					<span>
						{rangeStart}–{rangeEnd} of {rows.length}
					</span>
					<div className="flex items-center gap-1">
						<button
							type="button"
							disabled={pageIndex === 0}
							onClick={() => setPageIndex((i) => Math.max(0, i - 1))}
							className="rounded px-2 py-0.5 text-zinc-600 hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40 dark:text-zinc-300 dark:hover:bg-zinc-700"
						>
							Prev
						</button>
						<span className="tabular-nums">
							{pageIndex + 1} / {pageCount}
						</span>
						<button
							type="button"
							disabled={pageIndex >= pageCount - 1}
							onClick={() => setPageIndex((i) => Math.min(pageCount - 1, i + 1))}
							className="rounded px-2 py-0.5 text-zinc-600 hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40 dark:text-zinc-300 dark:hover:bg-zinc-700"
						>
							Next
						</button>
					</div>
				</div>
			)}
		</div>
	);
};
