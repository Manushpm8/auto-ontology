// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// tagsApi.getAll — list tags. Viewers too, unlike the rest of this file: the
// tag picker on every detail page offers the whole vocabulary, and a tag's name
// and id say nothing about the catalog.
export const GET = withPermission({ tag: ['read'] })((req) => proxyToBackend(req));
// tagsApi.create — add a tag (admin only).
export const POST = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));
