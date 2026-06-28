// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';
import { requireApiAuth } from '@/auth/api-auth';

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
	const denied = await requireApiAuth();
	if (denied) return denied;

	const prisma = getPrisma();
	const { id } = await params;
	const body = await req.json();
	const row = await prisma.conversationAnalytics.update({
		where: { id },
		data: {
			responseMessageId: body.responseMessageId,
			response: body.response ?? null,
			sql: body.sql ?? null,
			responseTimestamp: new Date(),
		},
	});
	return NextResponse.json(row);
}
