// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { IconName } from '@/components/icons';
import { TableObjectType } from '@/enums/datasources';

const tableTypeIcons: Record<TableObjectType, IconName> = {
	[TableObjectType.BASE_TABLE]: IconName.Table,
	[TableObjectType.VIEW]: IconName.View,
	[TableObjectType.MATERIALIZED_VIEW]: IconName.MaterializedView,
};

export function iconForTableType(tableType: TableObjectType | string | undefined): IconName {
	if (tableType && tableType in tableTypeIcons) {
		return tableTypeIcons[tableType as TableObjectType];
	}
	return IconName.Table;
}

export function labelForTableType(tableType: TableObjectType | string | undefined): string {
	if (tableType) return tableType;
	return TableObjectType.BASE_TABLE;
}
