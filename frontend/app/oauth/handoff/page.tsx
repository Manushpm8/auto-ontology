'use client';

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Icon, IconName } from '@/common/icons';
import { deliverLoopbackCallback, isLoopbackCallback } from '@/auth/oauth-loopback';

// The page is public and takes its target from the query string, so it only
// ever talks to a loopback listener. Anything else is refused rather than
// navigated to; the server-side rewrite never sends other URLs here.
const callbackError = (target: string | null): string | null => {
	if (!target) return 'Missing callback.';
	try {
		if (!isLoopbackCallback(new URL(target))) return 'Invalid callback.';
	} catch {
		return 'Invalid callback.';
	}
	return null;
};

const HandoffStatus = () => {
	const params = useSearchParams();
	const target = params.get('url');
	const [unreachable, setUnreachable] = useState(false);
	const error = callbackError(target);

	useEffect(() => {
		if (error || !target) return undefined;
		let cancelled = false;
		void (async () => {
			const delivered = await deliverLoopbackCallback(target);
			if (!cancelled && !delivered) setUnreachable(true);
		})();
		return () => {
			cancelled = true;
		};
	}, [error, target]);

	if (error) {
		return <p className="text-center text-sm text-red-500">{error}</p>;
	}

	if (unreachable) {
		return (
			<div className="flex w-full max-w-md flex-col gap-3 text-center">
				<h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
					Could not reach the client
				</h1>
				<p className="text-sm text-zinc-600 dark:text-zinc-400">
					Access was granted, but the application that started sign-in is no longer
					listening. Return to it and start sign-in again.
				</p>
			</div>
		);
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
