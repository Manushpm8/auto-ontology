// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { redirect } from 'next/navigation';
import { connectionsApi } from '@/api/connections';

export default async function SettingsPage() {
	const isEnvSource = await connectionsApi.isEnvSource();
	redirect(isEnvSource ? '/settings/semantic-input' : '/settings/connections');
}
