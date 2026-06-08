// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ConnectionType } from '@/enums/connection';

/** User-provided fields for testing or creating a connection. */
export type ConnectionInput = {
	type: ConnectionType;
	connectionString: string;
};
