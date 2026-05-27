// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from 'react';
import { Spinner } from '@nvidia/foundations-react-core';

export type LoadingSize = 'small' | 'medium' | 'large';

export type LoadingProps = {
	/** Spinner size. Defaults to `medium`. */
	size?: LoadingSize;
	/** Accessible label for the spinner. Defaults to `"Loading"`. Ignored if `caption` is set. */
	label?: string;
	/** Optional caption shown below the spinner. Doubles as the accessible label. */
	caption?: ReactNode;
	/**
	 * Layout mode for the wrapper.
	 * - `fill`: fills the available space and centers the spinner (default).
	 * - `block`: centers in current block with vertical padding (compact, inline lists).
	 * - `inline`: no wrapper — caller controls placement.
	 */
	variant?: 'fill' | 'block' | 'inline';
	/** Extra classes for the wrapper (ignored when `variant="inline"`). */
	className?: string;
};

const WRAPPER_CLASSES: Record<'fill' | 'block', string> = {
	fill: 'flex h-full w-full flex-1 items-center justify-center',
	block: 'flex w-full items-center justify-center py-6',
};

const cn = (...parts: Array<string | false | null | undefined>): string =>
	parts.filter((p): p is string => Boolean(p)).join(' ');

export const Loading = ({
	size = 'medium',
	label = 'Loading',
	caption,
	variant = 'fill',
	className,
}: LoadingProps) => {
	const spinner =
		caption != null && caption !== '' ? (
			<Spinner size={size} slotDescription={caption} />
		) : (
			<Spinner size={size} aria-label={label} />
		);

	if (variant === 'inline') {
		return spinner;
	}

	return <div className={cn(WRAPPER_CLASSES[variant], className)}>{spinner}</div>;
};
