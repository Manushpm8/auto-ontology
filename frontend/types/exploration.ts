// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Term } from '@/types/terms';

export type ExplorationLayer = 'semantic' | 'data';

export type ExplorationTermNode = Term & {
	layer: 'semantic';
	nodeType: 'term';
	relationshipCount: number;
};

export type ExplorationDataNode = {
	id: string;
	name: string;
	description: string | null;
	layer: 'data';
	nodeType: 'table' | 'view' | 'materialized-view';
	relationshipCount: number;
	databaseId: string;
	databaseName: string;
	schemaId: string;
	schemaName: string;
	columnsCount: number;
	sqlCount: number;
	termsCount: number;
};

export type ExplorationNode = ExplorationTermNode | ExplorationDataNode;

export type ExplorationLink = {
	source: string;
	target: string;
	queries: string[];
};

export type ExplorationGraph = {
	nodes: ExplorationNode[];
	links: ExplorationLink[];
};

export type TableExplorationDetails = {
	queries: Array<{
		id: string;
		sql: string;
	}>;
	terms: Array<{
		id: string;
		name: string;
		description: string | null;
	}>;
};
