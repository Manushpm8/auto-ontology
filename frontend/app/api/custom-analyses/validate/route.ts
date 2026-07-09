// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// Validating SQL is part of the create/edit flow, so it shares the same
// analysis:manage permission as POST /custom-analyses.
export const POST = withPermission({ analysis: ['manage'] })((req) => proxyToBackend(req));
