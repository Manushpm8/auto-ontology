// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import { authClient } from '@/lib/auth-client';

type SsoProvider = { providerId: string; issuer: string; domain: string };

const inputClass =
	'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300';

const trimTrailingSlash = (value: string): string => value.replace(/\/$/, '');

const fetchSsoProviders = async (): Promise<SsoProvider[]> => {
	const res = await fetch('/api/sso-providers').catch(() => null);
	const data = res ? await res.json().catch(() => null) : null;
	return data?.providers ?? [];
};

export const SsoConfigForm = () => {
	const [providers, setProviders] = useState<SsoProvider[]>([]);
	const [providerId, setProviderId] = useState('');
	const [issuer, setIssuer] = useState('');
	const [domain, setDomain] = useState('');
	const [clientId, setClientId] = useState('');
	const [clientSecret, setClientSecret] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [message, setMessage] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	useEffect(() => {
		fetchSsoProviders().then(setProviders);
	}, []);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		setSubmitting(true);
		setError(null);
		setMessage(null);

		const result = await authClient.sso.register({
			providerId,
			issuer,
			domain,
			oidcConfig: {
				clientId,
				clientSecret,
				discoveryEndpoint: `${trimTrailingSlash(issuer)}/.well-known/openid-configuration`,
				scopes: ['openid', 'profile', 'email'],
				pkce: true,
			},
		});

		if (result.error) {
			setError(result.error.message ?? 'Failed to save the SSO provider.');
			setSubmitting(false);
			return;
		}

		setMessage('SSO provider saved.');
		setClientSecret('');
		setSubmitting(false);
		setProviders(await fetchSsoProviders());
	};

	return (
		<div className="h-full overflow-auto p-6">
			<div className="mx-auto max-w-xl">
				<h1 className="mb-1 text-lg font-semibold text-zinc-900 dark:text-zinc-100">
					Single Sign-On (OIDC)
				</h1>
				<p className="mb-4 text-xs text-zinc-500">
					Register an OpenID Connect provider. Endpoints are auto-discovered from the
					issuer. The client secret is stored securely and never shown again.
				</p>

				{providers.length > 0 ? (
					<div className="mb-6 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
						<h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
							Configured providers
						</h2>
						<ul className="space-y-1 text-sm text-zinc-700 dark:text-zinc-300">
							{providers.map((provider) => (
								<li
									key={provider.providerId}
									className="flex justify-between gap-4"
								>
									<span className="font-medium">{provider.providerId}</span>
									<span className="truncate text-xs text-zinc-400">
										{provider.issuer}
									</span>
								</li>
							))}
						</ul>
					</div>
				) : null}

				<form onSubmit={handleSubmit} className="flex flex-col gap-4">
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="providerId"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Provider ID
						</label>
						<input
							id="providerId"
							type="text"
							required
							placeholder="okta"
							value={providerId}
							onChange={(event) => setProviderId(event.target.value)}
							className={inputClass}
						/>
					</div>
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="issuer"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Issuer URL
						</label>
						<input
							id="issuer"
							type="url"
							required
							placeholder="https://example.okta.com"
							value={issuer}
							onChange={(event) => setIssuer(event.target.value)}
							className={inputClass}
						/>
					</div>
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="domain"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Email domain
						</label>
						<input
							id="domain"
							type="text"
							required
							placeholder="nvidia.com"
							value={domain}
							onChange={(event) => setDomain(event.target.value)}
							className={inputClass}
						/>
					</div>
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="clientId"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Client ID
						</label>
						<input
							id="clientId"
							type="text"
							required
							value={clientId}
							onChange={(event) => setClientId(event.target.value)}
							className={inputClass}
						/>
					</div>
					<div className="flex flex-col gap-1.5">
						<label
							htmlFor="clientSecret"
							className="text-xs font-medium text-zinc-600 dark:text-zinc-400"
						>
							Client Secret
						</label>
						<input
							id="clientSecret"
							type="password"
							required
							autoComplete="off"
							value={clientSecret}
							onChange={(event) => setClientSecret(event.target.value)}
							className={inputClass}
						/>
					</div>

					{error ? <p className="text-xs text-red-500">{error}</p> : null}
					{message ? <p className="text-xs text-[#76b900]">{message}</p> : null}

					<button
						type="submit"
						disabled={submitting}
						className="cursor-pointer self-start rounded-md bg-[#76b900] px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-[#6aa600] disabled:opacity-50"
					>
						{submitting ? 'Saving…' : 'Save provider'}
					</button>
				</form>
			</div>
		</div>
	);
};
