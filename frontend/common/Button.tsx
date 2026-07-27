// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

import { ButtonSize, ButtonVariant } from '@/enums/button';

const variantClasses: Record<ButtonVariant, string> = {
	[ButtonVariant.Primary]: 'bg-[#76b900] text-white hover:bg-[#5e9400]',
	[ButtonVariant.Secondary]:
		'border border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800',
	[ButtonVariant.Outline]:
		'border border-[#76b900] bg-white text-[#5e9400] hover:bg-[#76b900]/10 dark:bg-zinc-900 dark:hover:bg-[#76b900]/20',
	[ButtonVariant.Danger]:
		'bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-500',
	[ButtonVariant.Teal]:
		'bg-teal-600 text-white hover:bg-teal-700 dark:bg-teal-600 dark:hover:bg-teal-500',
	[ButtonVariant.Ghost]:
		'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100',
	[ButtonVariant.Icon]:
		'text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-700 dark:hover:text-zinc-300',
	[ButtonVariant.Unstyled]: '',
};

const sizeClasses: Record<ButtonSize, string> = {
	[ButtonSize.Small]: 'rounded-md px-3 py-1.5 text-sm',
	[ButtonSize.Medium]: 'rounded-lg px-4 py-2 text-sm',
	[ButtonSize.Icon]: 'rounded-md p-1',
	[ButtonSize.Unstyled]: '',
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
	children: ReactNode;
	variant?: ButtonVariant;
	size?: ButtonSize;
	loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
	(
		{
			children,
			variant = ButtonVariant.Unstyled,
			size = ButtonSize.Unstyled,
			loading = false,
			disabled = false,
			className = '',
			type = 'button',
			...props
		},
		ref,
	) => (
		<button
			{...props}
			ref={ref}
			type={type}
			disabled={disabled || loading}
			aria-busy={loading || undefined}
			className={`cursor-pointer disabled:cursor-default ${variant === ButtonVariant.Unstyled ? '' : 'font-medium transition-colors disabled:opacity-50'} ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
		>
			{children}
		</button>
	),
);

Button.displayName = 'Button';
