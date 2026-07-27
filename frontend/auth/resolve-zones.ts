// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const PYTHON_API_URL = process.env.PYTHON_API_URL ?? 'http://127.0.0.1:3001';

type ZoneRow = { id: string; enabled: boolean };
type ZonesResponse = { data: ZoneRow[] };

const fetchEnabledZoneIds = async (userId: string): Promise<string[]> => {
	const url = `${PYTHON_API_URL}/api/zones?uid=${encodeURIComponent(userId)}`;
	const response = await fetch(url, { headers: { Accept: 'application/json' } });
	if (!response.ok) {
		throw new Error(`Failed to resolve zones: ${response.status}`);
	}
	const payload = (await response.json()) as ZonesResponse;
	return (payload.data ?? []).filter((zone) => zone.enabled).map((zone) => zone.id);
};

/**
 * Shared zone-scoping rule for admins and viewers alike:
 * - ``null`` — no enabled zones configured → no boundary to enforce → all data.
 * - ``[id, ...]`` — restrict catalog/agent access to tables reachable via those zones.
 *
 * Zone membership is no longer a per-user authorization boundary, so this does
 * not depend on the requesting user's own grants and applies identically
 * regardless of role.
 */
const resolveEnabledZoneScope = async (userId: string): Promise<string[] | null> => {
	const enabledZoneIds = await fetchEnabledZoneIds(userId);
	return enabledZoneIds.length > 0 ? enabledZoneIds : null;
};

/**
 * Catalog/Terms access: both admins and viewers share the same scope — see
 * ``resolveEnabledZoneScope``. *role* is accepted for call-site compatibility
 * but no longer changes the result.
 */
export async function resolveZoneIds(
	userId: string,
	role: string | null,
): Promise<string[] | null> {
	void role;
	return resolveEnabledZoneScope(userId);
}

/**
 * Text-to-SQL agent scope — see ``resolveEnabledZoneScope``. Applies
 * identically for both admins and viewers.
 */
export async function resolveAgentZoneIds(userId: string): Promise<string[] | null> {
	return resolveEnabledZoneScope(userId);
}
