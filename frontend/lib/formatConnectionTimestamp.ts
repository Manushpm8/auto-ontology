// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Display timestamp for connection cards (illumex ``formatTimestamp`` fallback). */
export function formatConnectionTimestamp(
	timestamp: string | null | undefined,
	fallback = 'Never',
): string {
	if (!timestamp) return fallback;
	const parsed = new Date(timestamp);
	if (Number.isNaN(parsed.getTime())) return fallback;
	return parsed.toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		year: 'numeric',
	});
}
