// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useRef, useState } from 'react';
import { ColorSwatch } from '@/components/settings/ColorSwatch';
import { Size } from '@/enums/button';

export type ColorOption = {
	value: string;
	label: string;
	colorClass: string;
};

type ColorPickerProps = {
	colors: readonly ColorOption[];
	value: string;
	onChange: (next: string) => void;
	fallbackColorClass?: string;
};

export const ColorPicker = ({
	colors,
	value,
	onChange,
	fallbackColorClass = 'bg-zinc-400',
}: ColorPickerProps) => {
	const [open, setOpen] = useState(false);
	const ref = useRef<HTMLDivElement>(null);
	const selectedColorClass =
		colors.find((color) => color.value === value)?.colorClass ?? fallbackColorClass;

	useEffect(() => {
		if (!open) return;
		const handleClick = (event: MouseEvent) => {
			if (ref.current && !ref.current.contains(event.target as Node)) {
				setOpen(false);
			}
		};
		document.addEventListener('mousedown', handleClick);
		return () => document.removeEventListener('mousedown', handleClick);
	}, [open]);

	return (
		<div ref={ref} className="relative shrink-0">
			<ColorSwatch
				colorClass={selectedColorClass}
				size={Size.LARGE}
				onClick={() => setOpen((prev) => !prev)}
				aria-label="Pick color"
				title="Pick color"
			/>
			{open ? (
				<div className="absolute right-0 top-full z-20 mt-2 w-40 rounded-lg border border-zinc-200 bg-white p-2 shadow-lg dark:border-zinc-700 dark:bg-zinc-800">
					<div className="grid grid-cols-3 gap-2">
						{colors.map((color) => {
							const selected = value === color.value;
							return (
								<ColorSwatch
									key={color.value}
									colorClass={color.colorClass}
									selected={selected}
									onClick={() => {
										onChange(color.value);
										setOpen(false);
									}}
									title={color.label}
									aria-label={`Color ${color.label}`}
								/>
							);
						})}
					</div>
				</div>
			) : null}
		</div>
	);
};
