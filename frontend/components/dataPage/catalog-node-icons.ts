// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { IconName } from '@/components/icons';
import { DataModels } from '@/enums/datasources';
import { iconForTableType, labelForTableType } from '@/components/dataPage/table-type-icons';

const catalogNodeIcons: Record<DataModels, IconName> = {
	[DataModels.DB]: IconName.Database,
	[DataModels.SCHEMA]: IconName.Schema,
	[DataModels.TABLE]: IconName.Table,
	[DataModels.VIEW]: IconName.View,
	[DataModels.COLUMN]: IconName.Column,
};

const catalogNodeLabels: Record<DataModels, string> = {
	[DataModels.DB]: 'database',
	[DataModels.SCHEMA]: 'schema',
	[DataModels.TABLE]: 'table',
	[DataModels.VIEW]: 'view',
	[DataModels.COLUMN]: 'column',
};

export function iconForCatalogNode(kind: DataModels, type?: string): IconName {
	if (type) {
		return iconForTableType(type);
	}
	return catalogNodeIcons[kind];
}

export function catalogNodeLabel(kind: DataModels, type?: string): string {
	if (type) {
		return labelForTableType(type);
	}
	return catalogNodeLabels[kind];
}
