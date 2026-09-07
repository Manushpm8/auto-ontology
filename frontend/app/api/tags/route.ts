// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { userCan } from '@/auth/permissions';
import { proxyToBackend } from '@/auth/proxy-backend';
import { withTagAuthors } from '@/lib/tagAuthors';

// tagsApi.getAll — list tags. Viewers too, unlike the rest of this file: the
// tag picker on every detail page offers the whole vocabulary, and a tag's name
// and id say nothing about the catalog.
//
// The author names are added only for callers who can reach the settings page
// that shows them. The picker needs a name and an id, and who curated the
// vocabulary is not something a viewer is asked to know.
export const GET = withPermission({ tag: ['read'] })(async (req, { user }) =>
	userCan(user, { tag: ['manage'] })
		? withTagAuthors(await proxyToBackend(req))
		: proxyToBackend(req),
);

// tagsApi.create — add a tag (admin only). The identity goes down so the
// backend can record who created it, and comes back resolved to a name, so the
// settings page draws the new row from this response like every other.
export const POST = withPermission({ tag: ['manage'] })(async (req, { user }) =>
	withTagAuthors(await proxyToBackend(req, { userId: user.id })),
);
