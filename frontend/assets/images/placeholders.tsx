// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export const Placeholders = {
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
