// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Term, TermZone } from '@/types/terms';

export type ExplorationLayer = 'semantic' | 'data';

export type ExplorationTermNode = Term & {
	layer: 'semantic';
	nodeType: 'term';
	relationshipCount: number;
	columnAttributesCount: number;
	sqlAttributesCount: number;
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
	zones: TermZone[];
};

export type ExplorationNode = ExplorationTermNode | ExplorationDataNode;

export type ExplorationLink = {
	source: string;
	target: string;
	queries: string[];
	/** True when this pair of tables is also (or only) linked by a foreign key. */
	viaForeignKey?: boolean;
};

export type ExplorationGraph = {
	nodes: ExplorationNode[];
	links: ExplorationLink[];
};

/** Maps a Table or Term id to the Zones it belongs to. */
export type ExplorationZonesMap = Record<string, TermZone[]>;

/** Server DTO for a semantic (Term) node in the Exploration graph endpoint. */
export type SemanticGraphNodeDto = {
	id: string;
	name: string;
	description: string | null;
	synonyms: string[];
	zones: TermZone[];
	relationship_count: number;
	column_attributes_count: number;
	sql_attributes_count: number;
};

/** Server DTO for a data (Table) node in the Exploration graph endpoint. */
export type DataGraphNodeDto = {
	id: string;
	name: string;
	description: string | null;
	table_type: string;
	database_id: string;
	database_name: string;
	schema_id: string;
	schema_name: string;
	columns_count: number;
	sql_count: number;
	terms_count: number;
	zones: TermZone[];
};

export type SemanticExplorationGraph = {
	nodes: SemanticGraphNodeDto[];
	links: Array<{ source: string; target: string }>;
};

/** Server DTO for a data-layer Exploration edge (Table ↔ Table). */
export type DataGraphEdgeDto = {
	source: string;
	target: string;
	queries: string[];
	via_foreign_key: boolean;
};

export type DataExplorationGraph = {
	nodes: DataGraphNodeDto[];
	links: DataGraphEdgeDto[];
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
