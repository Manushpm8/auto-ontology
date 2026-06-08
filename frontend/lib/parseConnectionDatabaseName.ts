// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Derive the catalog database name from a postgres/snowflake connection string. */
export const parseConnectionDatabaseName = (connectionString: string): string | null => {
	const trimmed = connectionString.trim();
	if (!trimmed) return null;

	const databaseFromQuery = trimmed.match(/[?&]database=([^&#]+)/i)?.[1];
	if (databaseFromQuery?.trim()) {
		return decodeURIComponent(databaseFromQuery.trim());
	}

	try {
		const url = new URL(trimmed);
		const fromParams = url.searchParams.get('database');
		if (fromParams?.trim()) {
			return fromParams.trim();
		}

		const segments = url.pathname.split('/').filter(Boolean);
		if (segments.length > 0) {
			const last = segments[segments.length - 1]?.trim();
			return last || null;
		}
	} catch {
		// Fall through to path-regex parsing for passwords with special characters.
	}

	const pathMatch = trimmed.match(/\/([^/?&#]+)(?:\?|#|$)/);
	if (pathMatch?.[1]?.trim()) {
		return decodeURIComponent(pathMatch[1].trim());
	}

	return null;
};
