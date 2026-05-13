// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export function splitId(id: string, expectedParts: number): string[] {
	const parts = id.split('|');
	if (parts.length !== expectedParts) {
		throw new Error(`Invalid ID: ${id}. Expected ${expectedParts} part(s) separated by '|'.`);
	}
	return parts;
}
