// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Graph labels used as search types. `View` is Table + a view table_type. */
export enum SearchObjectType {
	Term = 'Term',
	Attribute = 'ColumnAttribute',
	SqlAttribute = 'SqlAttribute',
	Analysis = 'CustomAnalysis',
	PqlAnalysis = 'PqlAnalysis',
	Db = 'Database',
	Schema = 'Schema',
	Table = 'Table',
	View = 'View',
	Column = 'Column',
}

export enum TextMatchOption {
	Contains = 'contains',
}

/** Cap on `POST /search/globalSearch`. Count is uncapped. */
export const GLOBAL_SEARCH_LIST_LIMIT = 200;

/** Tab id for the unfiltered global-search list. */
export const GLOBAL_SEARCH_ALL_TAB = 'all';
