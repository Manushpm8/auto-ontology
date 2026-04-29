import path from 'node:path';
import dotenv from 'dotenv';
import { defineConfig } from 'prisma/config';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

export default defineConfig({
	datasource: {
		url: process.env.DATABASE_URL ?? 'postgresql://localhost:5432/placeholder',
	},
});
