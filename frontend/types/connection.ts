// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type Connection = {
	id: string;
	type: string;
	create_date?: string | null;
	last_pulled?: string | null;
	database_name: string;
};
