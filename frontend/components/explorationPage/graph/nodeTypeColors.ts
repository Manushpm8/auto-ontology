// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type NodeType =
	| 'term'
	| 'table'
	| 'schema'
	| 'column'
	| 'columnAttribute'
	| 'sqlAttribute'
	| 'sql'
	| 'customAnalysis';

const DATA_OBJECT_ICON_COLOR = '#e8935c';
const TERM_OBJECT_ICON_COLOR = '#47bac5';
// Structural entities (schema/column) only ever appear once a table has been
// expanded — a violet/slate pair keeps them visually distinct from the
// analytical orange (table) and teal (term) types above.
const SCHEMA_OBJECT_ICON_COLOR = '#8b7fd6';
const COLUMN_OBJECT_ICON_COLOR = '#8a94a6';
// A Term's own ColumnAttribute neighbours are semantic (like the Term
// itself) rather than structural (like schema/column above), so they get a
// warm gold instead of that violet/slate pair — closer to the amber Neo4j
// Browser happens to auto-assign a fourth node label, while still reading
// as "belongs to the Term family" rather than "structural catalog metadata".
const COLUMN_ATTRIBUTE_OBJECT_ICON_COLOR = '#c99a2e';
// A Term's SqlAttribute neighbours are also `PROPERTY_OF` it, same as
// ColumnAttribute above, but computed from an expression rather than
// backed by a single Column — a rose accent keeps the two attribute types
// (and their otherwise-identical small leaf nodes) visually distinguishable
// on the graph.
const SQL_ATTRIBUTE_OBJECT_ICON_COLOR = '#c2577a';
// A SqlAttribute's own Sql query neighbour is one hop further out still —
// a distinct blue keeps it visually separate from the rose SqlAttribute
// it hangs off of, while still reading as a small structural leaf like
// column/columnAttribute above.
const SQL_OBJECT_ICON_COLOR = '#4f83cc';
// A CustomAnalysis sharing that Sql node is one hop further out still — a
// burnt-orange accent keeps it visually distinct from every other type
// (including the similarly-warm columnAttribute gold) while still reading
// as a small structural leaf like the others in this family.
const CUSTOM_ANALYSIS_OBJECT_ICON_COLOR = '#d17a3f';

/**
 * Each type's own accent color — used on the canvas as a node's border (see
 * `NODE_TYPE_BORDER_COLOR`'s own former doc comment in `GraphCanvas.tsx`)
 * and, via this same map, as the swatch color for its entry in
 * `ExplorationView.tsx`'s "Viewing: ..." legend — one source of truth so the
 * two never drift apart.
 */
export const NODE_TYPE_ACCENT_COLOR: Record<NodeType, string> = {
	term: TERM_OBJECT_ICON_COLOR,
	table: DATA_OBJECT_ICON_COLOR,
	schema: SCHEMA_OBJECT_ICON_COLOR,
	column: COLUMN_OBJECT_ICON_COLOR,
	columnAttribute: COLUMN_ATTRIBUTE_OBJECT_ICON_COLOR,
	sqlAttribute: SQL_ATTRIBUTE_OBJECT_ICON_COLOR,
	sql: SQL_OBJECT_ICON_COLOR,
	customAnalysis: CUSTOM_ANALYSIS_OBJECT_ICON_COLOR,
};
