// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Icon, IconName } from '@/components/icons';
import { signUp } from '@/lib/auth-client';

const inputClass =
	'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300';

export const SignupForm = () => {
	const router = useRouter();
	const [name, setName] = useState('');
	const [email, setEmail] = useState('');
	const [password, setPassword] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		setSubmitting(true);
		setError(null);
		const result = await signUp.email({ name, email, password });
		if (result.error) {
			setError(result.error.message ?? 'Could not create account.');
			setSubmitting(false);
			return;
		}
		router.push('/chat');
		router.refresh();
	};

	return (
		<div className="flex h-full w-full items-center justify-center bg-white p-6 dark:bg-zinc-950">
			<div className="flex flex-col items-center gap-6">
				<div className="flex items-center gap-2">
					<Icon name={IconName.NvidiaLogo} className="h-6 w-6" />
					<span className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
						GSF
					</span>
				</div>
				<p className="max-w-sm text-center text-xs text-zinc-500">
					Create your account to access GSF.
				</p>
				<form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="name"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Name
						</label>
						<input
							id="name"
							type="text"
							autoComplete="name"
							required
							value={name}
							onChange={(event) => setName(event.target.value)}
							className={inputClass}
						/>
					</div>
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="email"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Email
						</label>
						<input
							id="email"
							type="email"
							autoComplete="email"
							required
							value={email}
							onChange={(event) => setEmail(event.target.value)}
							className={inputClass}
						/>
					</div>
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="password"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Password
						</label>
						<input
							id="password"
							type="password"
							autoComplete="new-password"
							required
							minLength={8}
							value={password}
							onChange={(event) => setPassword(event.target.value)}
							className={inputClass}
						/>
					</div>

					{error ? <p className="text-xs text-red-500">{error}</p> : null}

					<button
						type="submit"
						disabled={submitting}
						className="cursor-pointer rounded-md bg-[#76b900] px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-[#6aa600] disabled:opacity-50"
					>
						{submitting ? 'Creating account…' : 'Create account'}
					</button>
				</form>
			</div>
		</div>
	);
};
