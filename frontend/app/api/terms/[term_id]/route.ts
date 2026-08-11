// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';
import { resolveZoneIds } from '@/auth/resolve-zones';

// termsApi.getById — fetch one Term node.
export const GET = withPermission({ catalog: ['read'] })(async (req, { user }) => {
	const zoneIds = await resolveZoneIds(user.id, user.role);
	return proxyToBackend(req, { zoneIds: zoneIds ?? undefined });
});

// Updating a Term name invalidates cached SqlAttribute description suggestions in backend.
export const PATCH = withPermission({ catalog: ['edit'] })((req) => proxyToBackend(req));
