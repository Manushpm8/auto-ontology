// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';
import { resolveZoneIds } from '@/auth/resolve-zones';

// termsApi.list — one page of Terms and their per-card count breakdowns,
// zone-scoped for viewers. `skip`/`limit` pass straight through.
export const GET = withPermission({ catalog: ['read'] })(async (req, { user }) => {
	const zoneIds = await resolveZoneIds(user.id, user.role);
	return proxyToBackend(req, {
		zoneIds: zoneIds ?? undefined,
		// This endpoint has its own envelope, so a viewer with no zones gets the
		// shape the list reads rather than the generic `{ data, count }`.
		emptyResponse: {
			terms: [],
			total: 0,
			column_attribute_counts: [],
			sql_attribute_counts: [],
			related_counts: [],
		},
	});
});
