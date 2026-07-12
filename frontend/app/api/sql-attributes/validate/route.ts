// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// Validating SQL is part of the create flow, so it shares the same
// catalog:edit permission as POST /api/sql-attributes.
export const POST = withPermission({ catalog: ['edit'] })((req) => proxyToBackend(req));
