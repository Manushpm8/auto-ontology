// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useActiveChat } from '@/stores/chatStore';

// Renders both hard stream errors and soft, user-initiated notices
// ("Generation stopped.", "Cancelled because you sent a new message in
// another conversation."). Two visually distinct styles keep the banner
// informative without crying wolf for benign interruptions.
export const ChatError = () => {
	const { error, notice } = useActiveChat();
	if (!error && !notice) return null;

	return (
		<div className="mx-auto w-full max-w-3xl space-y-2 px-4 py-2">
			{error && (
				<p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
					{error}
				</p>
			)}
			{notice && (
				<p className="rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300">
					{notice}
				</p>
			)}
		</div>
	);
};
