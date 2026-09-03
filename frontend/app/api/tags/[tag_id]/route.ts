// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// tagsApi.getById — one tag with the objects it labels.
//
// `manage` rather than the `read` that guards the tag *list*: this answer names
// catalog objects and spells out where they sit, and it is not scoped to the
// caller's zones the way the catalog reads are. Only the settings page asks for
// it, and that whole section is admin-only already.
export const GET = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));

// tagsApi.delete — remove a tag (admin only).
export const DELETE = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));
