// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export function capitalize(s: string): string {
	if (!s) return s;
	return s.charAt(0).toUpperCase() + s.slice(1);
}

export function formatTimestamp({
	timestamp,
	format,
}: {
	timestamp: Date;
	format: string;
}): string {
	if (format === '(DD.MM.YY)') {
		const d = timestamp;
		const dd = String(d.getDate()).padStart(2, '0');
		const mm = String(d.getMonth() + 1).padStart(2, '0');
		const yy = String(d.getFullYear()).slice(-2);
		return `(${dd}.${mm}.${yy})`;
	}
	return '';
}
