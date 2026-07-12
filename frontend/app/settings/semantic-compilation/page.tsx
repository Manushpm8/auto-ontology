// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requireAdmin } from '@/auth/auth-guards';
import { getPrisma } from '@/lib/prisma';
import { SemanticCompilationForm } from './SemanticCompilationForm';

const CONFIG_KEY = 'semantic_compilation_enabled';

const SemanticCompilationPage = async () => {
	await requireAdmin();
	// Fetch on the server so the toggle renders in the correct state on first
	// paint (no flash of "off" before the client fetch).
	const row = await getPrisma().configuration.findUnique({ where: { key: CONFIG_KEY } });
	return <SemanticCompilationForm initialEnabled={row?.value === 'true'} />;
};

export default SemanticCompilationPage;
