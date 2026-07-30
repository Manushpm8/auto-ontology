// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Second step: proxies POST /api/chat/visualize to the FastAPI backend once
// the client already has the SQL + executed result from step 1
// (POST /api/chat/completions). Kept as a separate, non-streaming request so
// the answer never waits on an extra LLM round trip just to pick a chart.

import { NextResponse } from 'next/server';
import { withPermission } from '@/auth/with-auth';
import { isVisualizationEnabled } from '@/lib/configurations';

const PYTHON_API_URL = process.env.PYTHON_API_URL ?? 'http://127.0.0.1:3001';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const parseBody = (rawBody: string): Record<string, unknown> => {
	try {
		const parsed: unknown = JSON.parse(rawBody);
		return typeof parsed === 'object' && parsed !== null
			? (parsed as Record<string, unknown>)
			: {};
	} catch {
		return {};
	}
};

// Same permission as the chat completions route: every chat user may trigger
// this, not just admins. The instance-wide toggle is resolved here, not
// trusted from the caller, so a stale tab can't opt back into charts once
// admins turn the setting off.
export const POST = withPermission({ chat: ['use'] })(async (req) => {
	if (!(await isVisualizationEnabled())) {
		return NextResponse.json({ charts: null });
	}

	const payload = parseBody(await req.text());

	const upstream = await fetch(`${PYTHON_API_URL}/api/chat/visualize`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(payload),
	});

	if (!upstream.ok) {
		// Soft-fail: the chart is a nice-to-have, so surface "no chart" rather
		// than an error the UI would have to special-case.
		return NextResponse.json({ charts: null });
	}

	const data = await upstream.json();
	return NextResponse.json(data);
});
