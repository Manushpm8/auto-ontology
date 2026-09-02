// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type Tag = {
	id: string;
	name: string;
	/** ISO 8601, from the database clock. */
	created: string;
	/** ISO 8601. Equal to `created` until something edits the tag. */
	modified: string;
};

export type TagCreateInput = {
	name: string;
};
