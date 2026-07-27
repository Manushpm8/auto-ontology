// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

export type PopoverMenuItem = {
	label: string;
	icon?: ReactNode;
	onClick: () => void;
	danger?: boolean;
};

type PopoverMenuProps = {
	items: PopoverMenuItem[];
	trigger: (props: { open: boolean; toggle: (e: React.MouseEvent) => void }) => ReactNode;
	className?: string;
};

/**
 * Dropdown menu anchored under the right edge of its trigger.
 *
 * The menu is rendered via a portal into `document.body` and positioned with
 * `fixed` coordinates rather than as an `absolute` child of the trigger: an
 * absolutely positioned menu is clipped by any `overflow-hidden`/`overflow-auto`
 * ancestor, which hid it entirely when the trigger lived inside a scrolling
 * table (see the certification column in `SinglePageComposer`'s data table).
 * `TruncatedText` portals its popover for the same reason.
 */
export const PopoverMenu = ({ items, trigger, className = '' }: PopoverMenuProps) => {
	// Coordinates are captured together with `open` so the menu never paints a
	// frame at a previous trigger's position.
	const [menu, setMenu] = useState<{ top: number; right: number } | null>(null);
	const anchorRef = useRef<HTMLDivElement>(null);
	const menuRef = useRef<HTMLDivElement>(null);
	const open = menu != null;

	useEffect(() => {
		if (!open) return;

		const handleMouseDown = (e: MouseEvent) => {
			const target = e.target as Node;
			if (anchorRef.current?.contains(target)) return;
			if (menuRef.current?.contains(target)) return;
			setMenu(null);
		};
		// Fixed coordinates don't follow the trigger, so dismiss instead of
		// letting the menu drift away from it. Capture phase catches scrolling
		// in nested containers, not just the window.
		const handleReflow = () => setMenu(null);
		document.addEventListener('mousedown', handleMouseDown);
		window.addEventListener('scroll', handleReflow, true);
		window.addEventListener('resize', handleReflow);
		return () => {
			document.removeEventListener('mousedown', handleMouseDown);
			window.removeEventListener('scroll', handleReflow, true);
			window.removeEventListener('resize', handleReflow);
		};
	}, [open]);

	const toggle = (e: React.MouseEvent) => {
		e.stopPropagation();
		const rect = e.currentTarget.getBoundingClientRect();
		setMenu((prev) =>
			prev != null ? null : { top: rect.bottom + 4, right: window.innerWidth - rect.right },
		);
	};

	return (
		<div ref={anchorRef} className={className}>
			{trigger({ open, toggle })}
			{menu != null &&
				createPortal(
					<div
						ref={menuRef}
						style={{ top: menu.top, right: menu.right }}
						className="fixed z-[1000] w-max min-w-32 whitespace-nowrap rounded-lg border border-zinc-200 bg-white py-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-800"
					>
						{items.map((item) => (
							<button
								key={item.label}
								type="button"
								onClick={() => {
									setMenu(null);
									item.onClick();
								}}
								className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
									item.danger
										? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40'
										: 'text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-700'
								}`}
							>
								{item.icon}
								{item.label}
							</button>
						))}
					</div>,
					document.body,
				)}
		</div>
	);
};
