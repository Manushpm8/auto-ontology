// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { CopyButton } from '@/common/Button';

export type PropertyRowProps = {
	label: string;
	value: string;
	monospace?: boolean;
	className?: string;
};

/**
 * Labeled value with a copy button, styled after `SqlBlock`'s header/body
 * split — a dark card with the label + copy control up top and the value
 * below it. Used for node-inspector-style panels (e.g. exploration's node
 * details panel) where every field is a single copyable value like a name,
 * description, or id.
 */
export const PropertyRow = ({ label, value, monospace = false, className }: PropertyRowProps) => (
	<div
		className={`group relative overflow-hidden rounded-lg bg-zinc-900 dark:bg-zinc-950 ${className ?? ''}`}
	>
		<div className="flex items-center justify-between border-b border-zinc-700 px-3 py-1.5">
			<span className="text-xs font-medium text-zinc-400">{label}</span>
			{value !== '' && <CopyButton text={value} />}
		</div>
		<p
			className={`p-3 text-xs leading-relaxed wrap-anywhere text-zinc-100 ${
				monospace ? 'font-mono' : ''
			}`}
		>
			{value || '—'}
		</p>
	</div>
);
