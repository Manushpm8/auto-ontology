// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
	type ReactNode,
} from 'react';
import { usePathname } from 'next/navigation';
import type { BreadcrumbItem } from '@/common/Breadcrumbs';

const SECTION_LABELS: Record<string, string> = {
	'/chat': 'Chat',
	'/terms': 'Terms',
	'/analysis': 'Analysis',
	'/exploration': 'Exploration',
	'/data': 'All Data',
	'/analytics': 'Analytics',
	'/settings': 'Settings',
};

// Settings keeps every section under one route prefix, so the section a reader
// is actually looking at exists only in the path, not in `SECTION_LABELS`.
const SETTINGS_SECTION_LABELS: Record<string, string> = {
	connections: 'Connections',
	zones: 'Zones',
	tags: 'Tags',
	rules: 'Rules',
	'semantic-input': 'Semantic Input',
	'semantic-compilation': 'Semantic Compilation',
	'agent-settings': 'Agent Settings',
	users: 'Users',
	sso: 'Single Sign-On',
	'import-export': 'Import / Export',
};

const EMPTY_TRAIL: BreadcrumbItem[] = [];

const sectionForPath = (path: string): BreadcrumbItem | null => {
	const href = Object.keys(SECTION_LABELS).find(
		(prefix) => path === prefix || path.startsWith(`${prefix}/`),
	);
	const label = href == null ? undefined : SECTION_LABELS[href];
	return href != null && label != null ? { label, href } : null;
};

const crumbsForPath = (path: string): BreadcrumbItem[] => {
	const section = sectionForPath(path);
	if (section == null) return EMPTY_TRAIL;
	if (section.href !== '/settings') return [section];

	const slug = path.split('/')[2];
	const label = slug == null ? undefined : SETTINGS_SECTION_LABELS[slug];
	return label == null ? [section] : [section, { label, href: `/settings/${slug}` }];
};

/**
 * Identity of a trail by value. Pages rebuild their crumbs on every render, so
 * both the setter and the publishing hook compare content rather than the
 * array they were handed.
 */
const trailKey = (items: BreadcrumbItem[]): string =>
	items.map((item) => `${item.label}\u0000${item.href ?? ''}`).join('\u0001');

type BreadcrumbContextValue = {
	items: BreadcrumbItem[];
	setTrail: (items: BreadcrumbItem[]) => void;
	rightSlot: ReactNode;
	setRightSlot: (node: ReactNode) => void;
};

const BreadcrumbContext = createContext<BreadcrumbContextValue>({
	items: EMPTY_TRAIL,
	setTrail: () => {},
	rightSlot: null,
	setRightSlot: () => {},
});

export const BreadcrumbProvider = ({ children }: { children: ReactNode }) => {
	const pathname = usePathname();
	const [prevPath, setPrevPath] = useState<string | null>(null);
	const [routeCrumbs, setRouteCrumbs] = useState<BreadcrumbItem[]>(EMPTY_TRAIL);
	const [trail, setTrailRaw] = useState<BreadcrumbItem[]>(EMPTY_TRAIL);
	const [rightSlot, setRightSlotRaw] = useState<ReactNode>(null);

	const setRightSlot = useCallback((node: ReactNode) => setRightSlotRaw(node), []);

	const setTrail = useCallback((next: BreadcrumbItem[]) => {
		setTrailRaw((prev) => (trailKey(prev) === trailKey(next) ? prev : next));
	}, []);

	if (pathname !== prevPath) {
		const current = crumbsForPath(pathname);
		const parent = prevPath == null ? null : sectionForPath(prevPath);
		const first = current[0];
		setRouteCrumbs(
			first != null && parent != null && parent.href !== first.href
				? [parent, ...current]
				: current,
		);
		// The page owns everything below its section, and the page being left
		// has already stopped rendering by the time the next one publishes.
		setTrailRaw(EMPTY_TRAIL);
		setPrevPath(pathname);
	}

	const items = useMemo(
		() => (trail.length === 0 ? routeCrumbs : [...routeCrumbs, ...trail]),
		[routeCrumbs, trail],
	);

	const value = useMemo(
		() => ({ items, setTrail, rightSlot, setRightSlot }),
		[items, setTrail, rightSlot, setRightSlot],
	);

	return <BreadcrumbContext.Provider value={value}>{children}</BreadcrumbContext.Provider>;
};

export const useBreadcrumbs = () => useContext(BreadcrumbContext);

/**
 * Names the caller's position below its section crumb — the focused term, the
 * opened tag, the catalog node. The top bar is the only place breadcrumbs are
 * drawn, so a page states where it is instead of rendering its own trail.
 *
 * Pass an empty array from a page that is showing its list rather than one
 * entity; the trail is also dropped when the caller unmounts.
 */
export const useBreadcrumbTrail = (items: BreadcrumbItem[]) => {
	const { setTrail } = useBreadcrumbs();

	// Callers build the array inline, so there is no stable identity to key an
	// effect on. Republishing after every render is what keeps the trail in
	// step with names that arrive late; `setTrail` settles when the content is
	// unchanged, so the repeat costs a string compare and no render.
	useEffect(() => {
		setTrail(items);
	});

	useEffect(() => () => setTrail(EMPTY_TRAIL), [setTrail]);
};
