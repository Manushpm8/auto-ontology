// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	const body = await req.json();

	const message = await prisma.message.create({
		data: {
			conversationId: id,
			role: body.role,
			content: body.content ?? '',
			sqlCode: body.sqlCode ?? null,
			sqlResponse: body.sqlResponse ?? null,
		},
	});

	await prisma.conversation.update({
		where: { id },
		data: { updatedAt: new Date() },
	});

	return NextResponse.json(message, { status: 201 });
}
