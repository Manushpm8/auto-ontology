'use client';

import { Suspense, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { Icon, IconName } from '@/common/icons';
import { deliverLoopbackCallback, isSafeMcpRedirect } from '@/auth/oauth-loopback';

const callbackError = (target: string | null): string | null => {
	if (!target) return 'Missing callback.';
	try {
		if (!isSafeMcpRedirect(new URL(target))) return 'Invalid callback.';
	} catch {
		return 'Invalid callback.';
	}
	return null;
};

const HandoffStatus = () => {
	const params = useSearchParams();
	const target = params.get('url');
	const error = callbackError(target);

	useEffect(() => {
		if (error || !target) return undefined;
		let cancelled = false;
		void (async () => {
			if (await deliverLoopbackCallback(target)) return;
			if (!cancelled) window.location.assign(target);
		})();
		return () => {
			cancelled = true;
		};
	}, [error, target]);

	if (error) {
		return <p className="text-center text-sm text-red-500">{error}</p>;
	}

	return (
		<div className="flex w-full max-w-md flex-col gap-3 text-center">
			<h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
				Back to Cursor
			</h1>
			<p className="text-sm text-zinc-600 dark:text-zinc-400">
				Access granted. You can close this tab.
			</p>
		</div>
	);
};

const HandoffPage = () => (
	<div className="flex h-full w-full items-center justify-center bg-white p-6 dark:bg-zinc-950">
		<div className="flex flex-col items-center gap-6">
			<div className="flex items-center gap-2">
				<Icon name={IconName.NvidiaLogo} className="h-6 w-6" />
				<span className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">GSF</span>
			</div>
			<Suspense fallback={null}>
				<HandoffStatus />
			</Suspense>
		</div>
	</div>
);

export default HandoffPage;
