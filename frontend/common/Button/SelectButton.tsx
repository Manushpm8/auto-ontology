// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { SelectButtonVariant } from '@/enums/button';

const variantClasses: Record<SelectButtonVariant, string> = {
	[SelectButtonVariant.Avatar]:
		'flex h-8 w-8 items-center justify-center rounded-full bg-[#76b900] text-xs font-semibold text-white transition-colors hover:bg-[#5e9400]',
	[SelectButtonVariant.Card]:
		'flex h-[150px] cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-6 shadow-sm transition-colors hover:border-[#76b900]/50 hover:bg-[#76b900]/5 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-[#76b900]/40',
	[SelectButtonVariant.ListItem]:
		'flex w-full cursor-pointer items-center text-left text-sm transition-colors',
	[SelectButtonVariant.Segmented]: 'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
	[SelectButtonVariant.Swatch]: 'cursor-pointer rounded-full border-2 transition hover:scale-105',
	[SelectButtonVariant.Trigger]:
		'flex min-h-10 w-full cursor-pointer items-center justify-between rounded-lg border border-zinc-300 bg-white px-3 py-2 text-left text-sm text-zinc-700 transition-colors hover:border-zinc-400 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200',
};

const getStateClasses = (
	variant: SelectButtonVariant,
	selected: boolean,
	danger: boolean,
): string => {
	if (variant === SelectButtonVariant.Segmented && selected) {
		return 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-zinc-100';
	}
	if (variant === SelectButtonVariant.ListItem && selected) {
		return 'bg-zinc-100 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100';
	}
	if (variant === SelectButtonVariant.ListItem && danger) {
		return 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40';
	}
	return '';
};

export type SelectButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
	children: ReactNode;
	variant: SelectButtonVariant;
	selected?: boolean;
	danger?: boolean;
};

export const SelectButton = ({
	children,
	variant,
	selected = false,
	danger = false,
	className = '',
	type = 'button',
	...props
}: SelectButtonProps) => (
	<button
		{...props}
		type={type}
		aria-pressed={selected || undefined}
		className={[variantClasses[variant], getStateClasses(variant, selected, danger), className]
			.filter(Boolean)
			.join(' ')}
	>
		{children}
	</button>
);
