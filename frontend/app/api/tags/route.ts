// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// tagsApi.getAll — list tags (admin only, as the settings page is).
export const GET = withPermission({ tag: ['read'] })((req) => proxyToBackend(req));
// tagsApi.create — add a tag (admin only).
export const POST = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));
