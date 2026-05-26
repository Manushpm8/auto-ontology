// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from 'next';

export const metadata: Metadata = {
	title: 'Analysis',
};

export default function AnalysisLayout({ children }: { children: React.ReactNode }) {
	return children;
}
