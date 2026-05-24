// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';

import { Icon, IconName } from '@/components/icons';
import { SqlBlock } from '@/components/SqlBlock';
import { analyses } from '@/api/analyses';
import type { CustomAnalysis } from '@/types/analysis';

export type AnalysisViewProps = Record<string, never>;

export const AnalysisView = () => {
	const [items, setItems] = useState<CustomAnalysis[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;

		(async () => {
			setLoading(true);
			const res = await analyses.list();
			if (cancelled) return;
			if (res.error === true) {
				setError(res.message ?? 'Failed to load custom analyses');
				setItems([]);
			} else {
				setError(null);
				setItems(res.data ?? []);
			}
			setLoading(false);
		})();

		return () => {
			cancelled = true;
		};
	}, []);

	return (
		<div className="flex h-full w-full flex-col bg-white dark:bg-zinc-950">
			<header className="flex items-center gap-3 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
				<Icon
					name={IconName.ChartBar}
					className="h-5 w-5 text-emerald-600 dark:text-emerald-400"
				/>
				<h1 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
					Custom analyses
				</h1>
				{!loading && error == null && (
					<span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
						{items.length} {items.length === 1 ? 'item' : 'items'}
					</span>
				)}
			</header>

			<div className="flex-1 overflow-y-auto px-6 py-6">
				{loading && (
					<div className="flex h-full items-center justify-center">
						<div
							className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-200 border-t-emerald-600 dark:border-zinc-700 dark:border-t-emerald-400"
							role="status"
							aria-label="Loading custom analyses"
						/>
					</div>
				)}

				{!loading && error != null && (
					<div className="mx-auto max-w-lg rounded-2xl border border-red-200/80 bg-white/90 px-8 py-10 text-center shadow-xl shadow-red-100/50 dark:border-red-900/50 dark:bg-zinc-950/80 dark:shadow-none">
						<h2 className="text-lg font-semibold tracking-tight text-red-800 dark:text-red-300">
							Couldn&apos;t load custom analyses
						</h2>
						<pre className="mt-4 max-w-full overflow-x-auto rounded-lg border border-red-100 bg-red-50/80 p-3 text-left text-xs text-red-900/80 dark:border-red-900/40 dark:bg-red-950/40 dark:text-red-200">
							{error}
						</pre>
					</div>
				)}

				{!loading && error == null && items.length === 0 && (
					<div className="flex h-full min-h-[40dvh] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-zinc-300/80 bg-white/60 p-12 text-center dark:border-zinc-600 dark:bg-zinc-950/40">
						<p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
							No custom analyses found
						</p>
					</div>
				)}

				{!loading && error == null && items.length > 0 && (
					<ul className="flex flex-col gap-4">
						{items.map((a) => (
							<li
								key={a.id}
								className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
							>
								<h2 className="text-base font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
									{a.name}
								</h2>
								{a.description != null && a.description.trim() !== '' && (
									<p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">
										{a.description}
									</p>
								)}
								{a.sql != null && a.sql.trim() !== '' && (
									<SqlBlock sql={a.sql} className="mt-4" />
								)}
							</li>
						))}
					</ul>
				)}
			</div>
		</div>
	);
};
