'use client';

import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useRef,
	useState,
	type ReactNode,
} from 'react';
import { usePathname } from 'next/navigation';
import type { BreadcrumbItem } from '@/components/Breadcrumbs';

const PATH_LABELS: Record<string, BreadcrumbItem> = {
	'/chat': { label: 'Chat', href: '/chat' },
	'/data': { label: 'All Data', href: '/data' },
	'/settings': { label: 'Settings', href: '/settings' },
};

const labelForPath = (path: string): BreadcrumbItem | null => {
	const match = Object.entries(PATH_LABELS).find(([prefix]) => path.startsWith(prefix));
	return match ? match[1] : null;
};

type BreadcrumbContextValue = {
	items: BreadcrumbItem[];
	rightSlot: ReactNode;
	setRightSlot: (node: ReactNode) => void;
};

const BreadcrumbContext = createContext<BreadcrumbContextValue>({
	items: [],
	rightSlot: null,
	setRightSlot: () => {},
});

export const BreadcrumbProvider = ({ children }: { children: ReactNode }) => {
	const pathname = usePathname();
	const prevPathRef = useRef<string | null>(null);
	const [items, setItems] = useState<BreadcrumbItem[]>([]);
	const [rightSlot, setRightSlotRaw] = useState<ReactNode>(null);

	const setRightSlot = useCallback((node: ReactNode) => setRightSlotRaw(node), []);

	useEffect(() => {
		const current = labelForPath(pathname);
		if (!current) return;

		const prev = prevPathRef.current;
		const parentCrumb = prev ? labelForPath(prev) : null;

		if (parentCrumb && parentCrumb.href !== current.href) {
			setItems([parentCrumb, { label: current.label }]);
		} else {
			setItems([{ label: current.label }]);
		}

		prevPathRef.current = pathname;
	}, [pathname]);

	const value = useMemo(
		() => ({ items, rightSlot, setRightSlot }),
		[items, rightSlot, setRightSlot],
	);

	return <BreadcrumbContext.Provider value={value}>{children}</BreadcrumbContext.Provider>;
};

export const useBreadcrumbs = () => useContext(BreadcrumbContext);
