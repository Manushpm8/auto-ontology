// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';

// rulesApi.getById — one rule.
export const GET = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));

// rulesApi.update — rename a rule or change the tags it applies (admin only).
//
// No identity forwarded, unlike the create: an edit advances `modified` and
// leaves `created_by` alone, so the rule stays attributed to whoever saved it
// rather than to whoever last touched it.
export const PATCH = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));

// rulesApi.delete — remove a rule (admin only).
export const DELETE = withPermission({ tag: ['manage'] })((req) => proxyToBackend(req));
