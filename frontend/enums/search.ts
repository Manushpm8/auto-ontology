// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export enum SearchObjectType {
	Term = 'term',
	Attribute = 'attribute',
	SqlAttribute = 'sql_attribute',
	Analysis = 'analysis',
	PqlAnalysis = 'pql_analysis',
	Db = 'db',
	Schema = 'schema',
	Table = 'table',
	View = 'view',
	Column = 'column',
}

export enum TextMatchOption {
	Contains = 'contains',
}

/** Cap on `POST /search/globalSearch`. Count is uncapped. */
export const GLOBAL_SEARCH_LIST_LIMIT = 200;

/** Tab id for the unfiltered global-search list. */
export const GLOBAL_SEARCH_ALL_TAB = 'all';
