// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ConnectionType } from '@/enums/connection';

export type Connection = {
	id: string;
	database_name: string;
	connection_string: string;
};

/** User-provided fields for testing or creating a connection. */
export type ConnectionInput = {
	type: ConnectionType;
	connectionString: string;
	/** Set after a successful test action in the create-connection wizard. */
	tested?: boolean;
};
