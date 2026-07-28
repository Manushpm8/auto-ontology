// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ButtonHTMLAttributes } from 'react';

export type ToggleButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
	checked: boolean;
};

export const ToggleButton = ({
	checked,
	className = '',
	type = 'button',
	...props
}: ToggleButtonProps) => (
	<button
		{...props}
		type={type}
		role="switch"
		aria-checked={checked}
		className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
			checked ? 'bg-[#76b900]' : 'bg-zinc-300 dark:bg-zinc-600'
		} ${className}`}
	>
		<span
			className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
				checked ? 'translate-x-5' : 'translate-x-0.5'
			}`}
		/>
	</button>
);
