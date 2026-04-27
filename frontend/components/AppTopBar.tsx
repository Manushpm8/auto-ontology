'use client';

import { Icon, IconName } from '@/components/icons';
import { Breadcrumbs } from '@/components/Breadcrumbs';
import { useBreadcrumbs } from '@/contexts/BreadcrumbContext';

export const AppTopBar = () => {
	const { items, rightSlot } = useBreadcrumbs();

	return (
		<header className="flex h-[52px] shrink-0 items-center border-b border-zinc-200 bg-white px-4 dark:border-zinc-800 dark:bg-zinc-950">
			<Icon name={IconName.NvidiaLogo} className="mr-3 h-5 w-5 shrink-0" />
			<Breadcrumbs items={items} />
			{rightSlot ? <div className="ml-auto flex items-center gap-2">{rightSlot}</div> : null}
		</header>
	);
};
