import path from 'node:path';
import dotenv from 'dotenv';
import { defineConfig, env } from 'prisma/config';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

export default defineConfig({
	datasource: {
		url: env('DATABASE_URL'),
	},
});
