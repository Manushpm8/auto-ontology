'use client';

import {
	createContext,
	useCallback,
	useContext,
	useMemo,
	useState,
	type ReactNode,
} from 'react';
import type { BreadcrumbItem } from '@/components/Breadcrumbs';

type BreadcrumbContextValue = {
	items: BreadcrumbItem[];
	setItems: (items: BreadcrumbItem[]) => void;
	rightSlot: ReactNode;
	setRightSlot: (node: ReactNode) => void;
};

const BreadcrumbContext = createContext<BreadcrumbContextValue>({
	items: [],
	setItems: () => {},
	rightSlot: null,
	setRightSlot: () => {},
});

export const BreadcrumbProvider = ({ children }: { children: ReactNode }) => {
	const [items, setItemsRaw] = useState<BreadcrumbItem[]>([]);
	const [rightSlot, setRightSlotRaw] = useState<ReactNode>(null);

	const setItems = useCallback((next: BreadcrumbItem[]) => setItemsRaw(next), []);
	const setRightSlot = useCallback((node: ReactNode) => setRightSlotRaw(node), []);

	const value = useMemo(
		() => ({ items, setItems, rightSlot, setRightSlot }),
		[items, setItems, rightSlot, setRightSlot],
	);

	return <BreadcrumbContext.Provider value={value}>{children}</BreadcrumbContext.Provider>;
};

export const useBreadcrumbs = () => useContext(BreadcrumbContext);
