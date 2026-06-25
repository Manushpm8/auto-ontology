// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from 'next';
import { SettingsPanelLayout } from '@/components/settings/SettingsPanelLayout';

export const metadata: Metadata = {
	title: 'Settings',
};

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
	return <SettingsPanelLayout>{children}</SettingsPanelLayout>;
}
