// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { getQueryClient } from '@/lib/queryClient';

type Props = { children: ReactNode };

// ``useState`` ensures the client survives suspense-driven re-renders of
// the Provider without being recreated. ``getQueryClient`` returns a
// per-tab singleton in the browser (and a fresh client on the server),
// keeping our cache request-scoped during SSR.
export const QueryProvider = ({ children }: Props) => {
	const [client] = useState(() => getQueryClient());
	return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};
