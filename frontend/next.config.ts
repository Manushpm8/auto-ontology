// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import path from 'node:path';
import dotenv from 'dotenv';
import type { NextConfig } from 'next';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

const pythonApiUrl = process.env.PYTHON_API_URL ?? 'http://127.0.0.1:3001';

const isStandalone = process.env.NEXT_OUTPUT === 'standalone';

const nextConfig: NextConfig = {
	output: isStandalone ? 'standalone' : undefined,
	// Only widen the file-tracing root when building the standalone Docker image,
	// where we need to pull in files from the monorepo root. In dev/regular builds
	// this would push Turbopack's resolver context to the repo root (which has no
	// node_modules) and break CSS module resolution (e.g. `@import 'tailwindcss'`
	// pulled in via `transpilePackages`).
	outputFileTracingRoot: isStandalone ? path.resolve(__dirname, '..') : undefined,
	transpilePackages: ['@nvidia/foundations-react-core'],
	turbopack: {
		rules: {
			'*.svg': {
				loaders: [{ loader: '@svgr/webpack', options: { svgo: false } }],
				as: '*.js',
			},
		},
	},
	async rewrites() {
		return [
			{
				source: '/api/chat/:path*',
				destination: `${pythonApiUrl}/api/chat/:path*`,
			},
			{
				source: '/api/datasources/:path*',
				destination: `${pythonApiUrl}/api/datasources/:path*`,
			},
			{
				source: '/api/schemas/:path*',
				destination: `${pythonApiUrl}/api/schemas/:path*`,
			},
			{
				source: '/api/tables/:path*',
				destination: `${pythonApiUrl}/api/tables/:path*`,
			},
			{
				source: '/api/columns/:path*',
				destination: `${pythonApiUrl}/api/columns/:path*`,
			},
			{
				source: '/api/nodes/:path*',
				destination: `${pythonApiUrl}/api/nodes/:path*`,
			},
			{
				source: '/api/health',
				destination: `${pythonApiUrl}/api/health`,
			},
		];
	},
};

export default nextConfig;
