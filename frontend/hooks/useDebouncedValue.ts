// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';

/**
 * Returns `value`, delayed by `delayMs` and re-emitted only once `value`
 * has stopped changing for that long. Handy for search inputs that trigger
 * an API call on every keystroke — debounce the value first, then use the
 * debounced copy as the effect dependency that fires the request.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
	const [debounced, setDebounced] = useState(value);

	useEffect(() => {
		const timer = setTimeout(() => setDebounced(value), delayMs);
		return () => clearTimeout(timer);
	}, [value, delayMs]);

	return debounced;
}
