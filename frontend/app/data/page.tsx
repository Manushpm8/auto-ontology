// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Suspense } from 'react';
import { DataWorkspaceView } from '@/components/dataPage';
import { datasources } from '@/api/datasources';

export default async function DataPage() {
	const res = await datasources.getDBs();
	const databases = res.error === true ? [] : res.data;
	const error = res.error === true ? (res.message ?? null) : null;

	return (
		<Suspense
			fallback={
				<div className="flex h-screen items-center justify-center">
					<div
						className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-200 border-t-emerald-600 dark:border-zinc-700 dark:border-t-emerald-400"
						role="status"
						aria-label="Loading"
					/>
				</div>
			}
		>
			<DataWorkspaceView databases={databases} loadError={error} />
		</Suspense>
	);
}
