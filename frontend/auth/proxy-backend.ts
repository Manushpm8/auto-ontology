// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Forward a request to the Python (FastAPI) backend, preserving method, path,
// query, and body. Used by route handlers that need permission gating for
// endpoints that would otherwise be plain next.config rewrites — the wrapper
// (withPermission) enforces access, this just relays the call. The frontend and
// backend share the same `/api/...` path, so we forward the incoming pathname
// verbatim.
//
// Zone scoping is not applied here: zone membership stopped being an
// authorization boundary, so the backend's own `zone_ids` default (unscoped)
// is what every proxied call gets.

const PYTHON_API_URL = process.env.PYTHON_API_URL ?? 'http://127.0.0.1:3001';

/**
 * `extraHeaders` is how a route sends the backend something the incoming
 * request cannot be trusted for. Nothing from the caller's own headers is
 * forwarded — only the ones built here — so a header the backend trusts cannot
 * be set by whoever called the route. The identity header is the case that
 * needs it: see `buildInternalIdentityHeaders`, and `app/api/rules/route.ts`
 * for a route that attributes a write to `ctx.user`.
 */
export async function proxyToBackend(
	req: Request,
	extraHeaders: Record<string, string> = {},
): Promise<Response> {
	const incoming = new URL(req.url);
	const target = `${PYTHON_API_URL}${incoming.pathname}${incoming.search}`;

	const headers: Record<string, string> = { Accept: 'application/json', ...extraHeaders };
	const contentType = req.headers.get('content-type');
	if (contentType) headers['Content-Type'] = contentType;

	const hasBody = req.method !== 'GET' && req.method !== 'HEAD';
	const body = hasBody ? await req.text() : undefined;

	const upstream = await fetch(target, { method: req.method, headers, body });

	const respBody = await upstream.text();
	return new Response(respBody, {
		status: upstream.status,
		headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
	});
}
