import { PrismaClient } from '@/generated/prisma/client';
import { PrismaPg } from '@prisma/adapter-pg';

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

function buildConnectionString() {
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

function createPrismaClient() {
	const adapter = new PrismaPg({ connectionString: buildConnectionString() });
	return new PrismaClient({ adapter });
}

export const prisma = globalForPrisma.prisma ?? createPrismaClient();

if (process.env.NODE_ENV !== 'production') {
	globalForPrisma.prisma = prisma;
}
