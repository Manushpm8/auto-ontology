// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { Placeholders } from '@/assets/images/placeholders';
import { ConnectionInfoCard } from '@/components/connectionsPage/ConnectionInfoCard';
import { SkeletonCard } from '@/common/Skeleton';
import type { Connection } from '@/types/connection';

type ConnectionsInfoCardViewProps = {
	connections: Connection[];
	loading?: boolean;
	onDelete?: (databaseName: string) => void;
};

export const ConnectionsInfoCardView = ({
	connections,
	loading = false,
	onDelete,
}: ConnectionsInfoCardViewProps) => {
	if (loading) {
		return (
			<div className="grid w-full grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
				{Array.from({ length: 9 }, (_, i) => (
					<SkeletonCard key={i} className="min-h-[148px]" />
				))}
			</div>
		);
	}

	if (connections.length === 0) {
		return (
			<div className="flex w-full flex-col items-center justify-center py-12">
				<Placeholders.NoResults />
			</div>
		);
	}

	return (
		<div className="grid w-full grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{connections.map((connection) => (
				<ConnectionInfoCard
					key={connection.database_name}
					connection={connection}
					onDelete={onDelete}
				/>
			))}
		</div>
	);
};
