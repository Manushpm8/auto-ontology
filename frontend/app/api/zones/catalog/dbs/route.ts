// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { proxyToBackend } from '@/auth/proxy-backend';
import { withPermission } from '@/auth/with-auth';

// Zone editors must see the full catalog to add data not assigned to a zone yet.
export const GET = withPermission({ zone: ['manage'] })((req) =>
	proxyToBackend(req, { targetPathname: '/api/datasources/dbs' }),
);
