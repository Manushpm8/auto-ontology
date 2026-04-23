'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Icon, IconName } from '@/components/icons';

type NavItem = {
	icon: IconName;
	href: string;
	label: string;
};

const navItems: NavItem[] = [
	{ icon: IconName.ChatBubble, href: '/chat', label: 'Chat' },
	{ icon: IconName.Database, href: '/data', label: 'Data Catalog' },
];

export const NAV_PREV_PATH_KEY = 'gsf:prevPath';
const NAV_CURRENT_PATH_KEY = 'gsf:currentPath';

export const NavRail = () => {
	const pathname = usePathname();
	const prevPathnameRef = useRef(pathname);

	useEffect(() => {
		if (pathname !== prevPathnameRef.current) {
			sessionStorage.setItem(NAV_PREV_PATH_KEY, prevPathnameRef.current);
			prevPathnameRef.current = pathname;
		}
		sessionStorage.setItem(NAV_CURRENT_PATH_KEY, pathname);
	}, [pathname]);

	return (
		<nav className="flex h-screen w-12 shrink-0 flex-col items-center border-r border-zinc-200 bg-white py-3 dark:border-zinc-800 dark:bg-zinc-950">
			<div className="flex flex-1 flex-col items-center gap-2">
				{navItems.map((item) => {
					const isActive = pathname.startsWith(item.href);

					return (
						<Link
							key={item.href}
							href={item.href}
							title={item.label}
							className={`flex h-9 w-9 items-center justify-center rounded-lg transition-colors ${
								isActive
									? 'bg-[#76b900]/15 text-[#76b900]'
									: 'text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200'
							}`}
						>
							<Icon name={item.icon} className="h-5 w-5" />
						</Link>
					);
				})}
			</div>
		</nav>
	);
};
