// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export const Placeholders = {
	NoConnections: function NoConnections() {
		return (
			<div
				className="flex h-40 w-56 flex-col items-center justify-center rounded-xl border border-dashed border-zinc-300/90 bg-zinc-50/80 dark:border-zinc-600 dark:bg-zinc-900/40"
				aria-hidden
			>
				<svg
					className="h-12 w-12 text-zinc-300 dark:text-zinc-600"
					viewBox="0 0 48 48"
					fill="none"
					xmlns="http://www.w3.org/2000/svg"
				>
					<rect
						x="8"
						y="12"
						width="32"
						height="24"
						rx="4"
						stroke="currentColor"
						strokeWidth="2"
					/>
					<path
						d="M16 20h16M16 26h10"
						stroke="currentColor"
						strokeWidth="2"
						strokeLinecap="round"
					/>
				</svg>
			</div>
		);
	},
	NoResults: function NoResults() {
		return (
			<div
				className="flex h-32 w-48 items-center justify-center rounded-lg border border-dashed border-zinc-300 text-sm text-zinc-400 dark:border-zinc-600 dark:text-zinc-500"
				aria-hidden
			>
				No results
			</div>
		);
	},
};
