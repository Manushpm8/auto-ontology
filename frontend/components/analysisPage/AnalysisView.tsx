// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { Icon, IconName } from '@/components/icons';

export type AnalysisViewProps = Record<string, never>;

export const AnalysisView = () => (
	<div className="flex h-full w-full flex-col bg-white dark:bg-zinc-950">
		<main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
			<header className="flex flex-col gap-2">
				<h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
					Analysis
				</h1>
				<p className="text-sm text-zinc-500 dark:text-zinc-400">
					Insights and metrics across your semantic fabric.
				</p>
			</header>

			<section className="flex min-h-[min(60dvh,480px)] flex-1 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-zinc-300/80 bg-white/60 p-12 text-center dark:border-zinc-700 dark:bg-zinc-950/40">
				<div
					className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[#76b900]/10 text-[#76b900]"
					aria-hidden
				>
					<Icon name={IconName.ChartBar} className="h-6 w-6" />
				</div>
				<p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
					Nothing to show yet
				</p>
				<p className="max-w-md text-xs text-zinc-500 dark:text-zinc-400">
					This page is a placeholder. Charts, metrics, and reports will live here.
				</p>
			</section>
		</main>
	</div>
);
