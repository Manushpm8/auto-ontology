// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type Connection = {
	id: string;
	name: string;
	description?: string | null;
	type?: string | null;
	create_date?: string | null;
	last_pulled?: string | null;
	num_of_schemas: number;
};
