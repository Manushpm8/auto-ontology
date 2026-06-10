// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import dayjs from 'dayjs';

/** Display timestamp for connection cards (illumex ``formatTimestamp`` fallback). */
export function formatConnectionTimestamp(
	timestamp: string | null | undefined,
	fallback = 'Never',
): string {
	if (!timestamp) return fallback;
	const parsed = dayjs(timestamp);
	if (!parsed.isValid()) return fallback;
	return parsed.format('MMM D, YYYY');
}
