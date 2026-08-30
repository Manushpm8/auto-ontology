// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// Resolve a described value to the one physical column that stores it, and to
// the exact stored spelling. Same retrieval capability as chat, so it is gated
// on `chat: ['use']`.
export const POST = withPermission({ chat: ['use'] })((req) => proxyToBackend(req));
