// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { IconName } from '@/components/icons';
import { DataModels, TableType } from '@/enums/datasources';

const tableTypeToCatalogKind: Record<TableType, DataModels> = {
	[TableType.BASE_TABLE]: DataModels.TABLE,
	[TableType.VIEW]: DataModels.VIEW,
	[TableType.MATERIALIZED_VIEW]: DataModels.MATERIALIZED_VIEW,
};

export const catalogNodeIcons: Record<DataModels, IconName> = {
	[DataModels.DB]: IconName.Database,
	[DataModels.SCHEMA]: IconName.Schema,
	[DataModels.TABLE]: IconName.Table,
	[DataModels.VIEW]: IconName.View,
	[DataModels.MATERIALIZED_VIEW]: IconName.MaterializedView,
	[DataModels.COLUMN]: IconName.Column,
};

export const catalogNodeTitles: Record<DataModels, string> = {
	[DataModels.DB]: 'database',
	[DataModels.SCHEMA]: 'schema',
	[DataModels.TABLE]: 'table',
	[DataModels.VIEW]: 'view',
	[DataModels.MATERIALIZED_VIEW]: 'materialized view',
	[DataModels.COLUMN]: 'column',
};

export function catalogKindForTableType(tableType: TableType | string | undefined): DataModels {
	return tableTypeToCatalogKind[tableType as TableType] ?? DataModels.TABLE;
}
