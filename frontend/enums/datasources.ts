// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export enum DataModels {
	DB = 'db',
	SCHEMA = 'schema',
	TABLE = 'table',
	VIEW = 'view',
	COLUMN = 'column',
}

/** Tree focus resolution before a catalog entity is matched. */
export enum TreeFocusState {
	NONE = 'none',
	LOADING = 'loading',
}

/** Block type in SinglePageComposer sections. */
export enum ComposerSectionKind {
	TEXT_CARD = 'textCard',
	INFO_GRID = 'infoGrid',
	DATA_TABLE = 'dataTable',
	LOADING_PANEL = 'loadingPanel',
}

export enum Usage {
	HIGH = 'high',
	MEDIUM = 'medium',
	LOW = 'low',
	UNUSED = 'unused',
}
