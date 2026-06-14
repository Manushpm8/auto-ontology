// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';

// Analytics always cover a fixed trailing window; not configurable per-request.
const ANALYTICS_DAYS = 30;

const parseIntParam = (value: string | null, fallback: number): number => {
	if (value == null) return fallback;
	const parsed = Number.parseInt(value, 10);
	return Number.isFinite(parsed) ? parsed : fallback;
};

export async function GET(request: Request) {
	const prisma = getPrisma();
	const { searchParams } = new URL(request.url);

	const skip = parseIntParam(searchParams.get('skip'), 0);
	const limitParam = searchParams.get('limit');
	const limit = limitParam != null ? parseIntParam(limitParam, 0) : null;

	const cutoff = new Date(Date.now() - ANALYTICS_DAYS * 24 * 60 * 60 * 1000);
	const where = { createdAt: { gte: cutoff } };

	const total = await prisma.messageAnalytic.count({ where });
	const rows = await prisma.messageAnalytic.findMany({
		where,
		orderBy: { createdAt: 'desc' },
		skip,
		...(limit != null ? { take: limit } : {}),
	});

	const messageIds = rows.flatMap((row) =>
		row.answerId != null ? [row.questionId, row.answerId] : [row.questionId],
	);

	const messages = await prisma.message.findMany({
		where: { id: { in: messageIds } },
		select: { id: true, content: true, sqlCode: true },
	});
	const messageById = new Map(messages.map((m) => [m.id, m]));

	const data = rows.map((row) => {
		const question = messageById.get(row.questionId);
		const answer = row.answerId != null ? messageById.get(row.answerId) : undefined;
		return {
			...row,
			question: question?.content ?? null,
			reasoning: answer?.content ?? null,
			responseSql: answer?.sqlCode ?? null,
		};
	});

	return NextResponse.json({ data, total });
}

export async function POST(req: Request) {
	const prisma = getPrisma();
	const body = await req.json();
	const row = await prisma.messageAnalytic.create({
		data: { questionId: body.questionId },
	});
	return NextResponse.json(row, { status: 201 });
}
