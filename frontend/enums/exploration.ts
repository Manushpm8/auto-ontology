// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { IconName } from '@/components/icons';

/** The two graph layers rendered by the Exploration page. */
export enum ExplorationLayer {
	Semantic = 'semantic',
	Data = 'data',
}

/** Kind of a data-layer Exploration node, mirroring the catalog's Table/View/MaterializedView split. */
export enum ExplorationDataNodeKind {
	Table = 'table',
	View = 'view',
	MaterializedView = 'materialized-view',
}

/** Icon rendered for a data-layer Exploration node, keyed by its kind. */
export const EXPLORATION_DATA_NODE_ICON: Record<ExplorationDataNodeKind, IconName> = {
	[ExplorationDataNodeKind.Table]: IconName.Table,
	[ExplorationDataNodeKind.View]: IconName.View,
	[ExplorationDataNodeKind.MaterializedView]: IconName.MaterializedView,
};
