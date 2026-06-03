// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { IconName } from '@/components/icons';
import { TableType } from '@/enums/datasources';

const tableTypeIcons: Record<TableType, IconName> = {
	[TableType.BASE_TABLE]: IconName.Table,
	[TableType.VIEW]: IconName.View,
	[TableType.MATERIALIZED_VIEW]: IconName.MaterializedView,
};

export function iconForTableType(tableType: TableType | string | undefined): IconName {
	if (tableType && tableType in tableTypeIcons) {
		return tableTypeIcons[tableType as TableType];
	}
	return IconName.Table;
}

export function labelForTableType(tableType: TableType | string | undefined): string {
	if (tableType) return tableType;
	return TableType.BASE_TABLE;
}
