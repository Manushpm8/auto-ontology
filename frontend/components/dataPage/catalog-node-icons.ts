// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { IconName } from '@/components/icons';
import { DataModels, TableType } from '@/enums/datasources';
import { getTableType } from '@/components/dataPage/get-table-type';

const tableTypeToCatalogKind: Record<TableType, DataModels> = {
	[TableType.BASE_TABLE]: DataModels.TABLE,
	[TableType.VIEW]: DataModels.VIEW,
	[TableType.MATERIALIZED_VIEW]: DataModels.MATERIALIZED_VIEW,
};

const catalogNodeIcons: Record<DataModels, IconName> = {
	[DataModels.DB]: IconName.Database,
	[DataModels.SCHEMA]: IconName.Schema,
	[DataModels.TABLE]: IconName.Table,
	[DataModels.VIEW]: IconName.View,
	[DataModels.MATERIALIZED_VIEW]: IconName.MaterializedView,
	[DataModels.COLUMN]: IconName.Column,
};

const catalogNodeTitles: Record<DataModels, string> = {
	[DataModels.DB]: 'database',
	[DataModels.SCHEMA]: 'schema',
	[DataModels.TABLE]: 'table',
	[DataModels.VIEW]: 'view',
	[DataModels.MATERIALIZED_VIEW]: 'materialized view',
	[DataModels.COLUMN]: 'column',
};

function catalogKindForTableType(tableType: TableType | string | undefined): DataModels {
	const resolved = getTableType(tableType);
	return tableTypeToCatalogKind[resolved as TableType] ?? DataModels.TABLE;
}

export function iconForCatalogNode(kind: DataModels, type?: string): IconName {
	if (type) {
		return catalogNodeIcons[catalogKindForTableType(type)];
	}
	return catalogNodeIcons[kind];
}

export function catalogNodeLabel(kind: DataModels, type?: string): string {
	if (type) {
		return getTableType(type);
	}
	return catalogNodeTitles[kind];
}
