// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';
import { requireApiAuth, requireApiAdmin } from '@/lib/api-auth';

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
	const rows = await prisma.conversationAnalytics.findMany({
		where,
		orderBy: { questionTimestamp: 'desc' },
		skip,
		...(limit != null ? { take: limit } : {}),
	});

	// Resolve who ran each question: questionMessageId -> message -> conversation
	// -> user. ConversationAnalytics has no Prisma relation to Message, so look
	// the owners up in one query and attach a display name to each row.
	const messageIds = rows.map((row) => row.questionMessageId);
	const messages = await prisma.message.findMany({
		where: { id: { in: messageIds } },
		select: {
			id: true,
			conversation: { select: { user: { select: { name: true, email: true } } } },
		},
	});
	const userByMessageId = new Map(
		messages.map((message) => {
			const user = message.conversation.user;
			return [message.id, user.name || user.email || null];
		}),
	);

	const data = rows.map((row) => ({
		...row,
		userName: userByMessageId.get(row.questionMessageId) ?? null,
	}));

	return NextResponse.json({ data, total });
}

export async function POST(req: Request) {
	const denied = await requireApiAuth();
	if (denied) return denied;

	const prisma = getPrisma();
	const body = await req.json();
	const row = await prisma.conversationAnalytics.create({
		data: {
			questionMessageId: body.questionMessageId,
			question: body.question ?? '',
		},
	});
	return NextResponse.json(row, { status: 201 });
}
