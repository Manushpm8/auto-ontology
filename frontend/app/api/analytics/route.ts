// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';
import { requireApiAdmin } from '@/auth/api-auth';
import { getCurrentSession } from '@/auth/auth-guards';

// Analytics always cover a fixed trailing window; not configurable per-request.
const ANALYTICS_DAYS = 30;

const parseIntParam = (value: string | null, fallback: number): number => {
	if (value == null) return fallback;
	const parsed = Number.parseInt(value, 10);
	return Number.isFinite(parsed) ? parsed : fallback;
};

export async function GET(request: Request) {
	// Viewing analytics (the report) is admin-only.
	const denied = await requireApiAdmin();
	if (denied) return denied;

	const prisma = getPrisma();
	const { searchParams } = new URL(request.url);

	const skip = parseIntParam(searchParams.get('skip'), 0);
	const limitParam = searchParams.get('limit');
	const limit = limitParam != null ? parseIntParam(limitParam, 0) : null;

	const cutoff = new Date(Date.now() - ANALYTICS_DAYS * 24 * 60 * 60 * 1000);
	const where = { questionTimestamp: { gte: cutoff } };

	const total = await prisma.conversationAnalytics.count({ where });
	// userName is denormalized onto the row at capture time (see POST), so the
	// report reads it directly — no join back to message/conversation/user.
	const data = await prisma.conversationAnalytics.findMany({
		where,
		orderBy: { questionTimestamp: 'desc' },
		skip,
		...(limit != null ? { take: limit } : {}),
	});

	return NextResponse.json({ data, total });
}

export async function POST(req: Request) {
	// Capture runs for the signed-in user; their display name is recorded on the
	// analytics row so the report is self-contained.
	const session = await getCurrentSession();
	if (!session) {
		return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
	}

	const prisma = getPrisma();
	const body = await req.json();
	const row = await prisma.conversationAnalytics.create({
		data: {
			questionMessageId: body.questionMessageId,
			question: body.question ?? '',
			source: body.source ?? 'app',
			userName: session.user.name || session.user.email || null,
		},
	});
	return NextResponse.json(row, { status: 201 });
}
