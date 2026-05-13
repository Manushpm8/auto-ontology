// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { PrismaClient } from '@/generated/prisma/client';
import { PrismaPg } from '@prisma/adapter-pg';

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

function buildConnectionString(): string {
	const host = process.env.POSTGRES_HOST ?? 'localhost';
	const port = process.env.POSTGRES_PORT ?? '5432';
	const user = process.env.POSTGRES_USER;
	const password = process.env.POSTGRES_PASSWORD;
	const database = process.env.POSTGRES_DATABASE;

	if (!user || !password || !database) {
		throw new Error(
			'POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DATABASE must be set in the root .env file',
		);
	}

	return `postgresql://${user}:${password}@${host}:${port}/${database}`;
}

function createPrismaClient(): PrismaClient {
	const adapter = new PrismaPg({ connectionString: buildConnectionString() });
	return new PrismaClient({ adapter });
}

/** Lazy singleton — only connects when first accessed at runtime, not at import/build time. */
export function getPrisma(): PrismaClient {
	const client = globalForPrisma.prisma ?? createPrismaClient();
	if (process.env.NODE_ENV !== 'production') {
		globalForPrisma.prisma = client;
	}
	return client;
}
