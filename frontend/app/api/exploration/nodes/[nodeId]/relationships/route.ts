// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { proxyToBackend } from '@/auth/proxy-backend';
import { resolveZoneIds } from '@/auth/resolve-zones';
import { withPermission } from '@/auth/with-auth';

// explorationApi.getRelatedNodes — a zone-scoped page of related Exploration nodes.
export const GET = withPermission({ catalog: ['read'] })(async (req, { user }) => {
	const zoneIds = await resolveZoneIds(user.id, user.role);
	return proxyToBackend(req, {
		zoneIds: zoneIds ?? undefined,
		emptyResponse: { data: { nodes: [], total: 0 } },
	});
});
