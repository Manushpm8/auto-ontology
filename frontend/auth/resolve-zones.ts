// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Zone membership is no longer an authorization boundary. Both roles receive
 * the unfiltered catalog scope.
 */
export function resolveZoneIds(userId: string, role: string | null): string[] | null {
	void userId;
	void role;
	return null;
}

const PYTHON_API_URL = process.env.PYTHON_API_URL ?? 'http://127.0.0.1:3001';

type ZoneRow = { id: string; enabled: boolean };
type ZonesResponse = { data: ZoneRow[] };

const fetchEnabledZoneIds = async (userId: string): Promise<string[]> => {
	const url = `${PYTHON_API_URL}/api/zones?uid=${encodeURIComponent(userId)}`;
	const response = await fetch(url, { headers: { Accept: 'application/json' } });
	if (!response.ok) {
		throw new Error(`Failed to resolve zones for the agent: ${response.status}`);
	}
	const payload = (await response.json()) as ZonesResponse;
	return (payload.data ?? []).filter((zone) => zone.enabled).map((zone) => zone.id);
};

/**
 * Text-to-SQL is explicitly scoped to every enabled Neo4j zone for both
 * admins and viewers.
 */
export async function resolveAgentZoneIds(userId: string): Promise<string[]> {
	return fetchEnabledZoneIds(userId);
}
