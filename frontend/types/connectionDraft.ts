// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ConnectionType } from '@/enums/connection';

export type ConnectionDraft = {
	type: ConnectionType;
	name: string;
	description: string;
	connectionString: string;
	databases: string[];
};
