// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useState, type ButtonHTMLAttributes } from 'react';

export type CopyButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
	text: string;
};

export const CopyButton = ({ text, className = '', ...props }: CopyButtonProps) => {
	const [copied, setCopied] = useState(false);

	const handleCopy = useCallback(() => {
		void navigator.clipboard.writeText(text).then(() => {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		});
	}, [text]);

	return (
		<button
			{...props}
			type="button"
			onClick={handleCopy}
			className={`absolute top-2 right-2 cursor-pointer rounded bg-zinc-700 px-2 py-1 text-xs font-medium text-zinc-300 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-zinc-600 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
		>
			{copied ? 'Copied!' : 'Copy'}
		</button>
	);
};
