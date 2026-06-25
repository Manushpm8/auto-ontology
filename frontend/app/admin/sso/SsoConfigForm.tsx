// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useState } from 'react';
import { authClient } from '@/lib/auth-client';

type SsoProvider = { providerId: string; issuer: string; domain: string };

const inputClass =
	'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 disabled:cursor-not-allowed disabled:bg-zinc-100 disabled:text-zinc-500 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300 dark:disabled:bg-zinc-800/50 dark:disabled:text-zinc-400';

const fetchSsoProviders = async (): Promise<SsoProvider[]> => {
	const res = await fetch('/api/sso-providers').catch(() => null);
	const data = res ? await res.json().catch(() => null) : null;
	return data?.providers ?? [];
};

export const SsoConfigForm = ({ initialProviders }: { initialProviders: SsoProvider[] }) => {
	// Seeded from the server (see page.tsx) so the correct view renders on first
	// paint; re-fetched after register/delete to stay in sync.
	const [providers, setProviders] = useState<SsoProvider[]>(initialProviders);
	const [providerId, setProviderId] = useState('');
	const [issuer, setIssuer] = useState('');
	const [clientId, setClientId] = useState('');
	const [clientSecret, setClientSecret] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [message, setMessage] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const [deletingId, setDeletingId] = useState<string | null>(null);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		setSubmitting(true);
		setError(null);
		setMessage(null);

		// Resolve the provider's endpoints server-side, then register with
		// skipDiscovery. This avoids Better Auth's auto-discovery, which would
		// require the IdP origin in trustedOrigins; the skipDiscovery path
		// accepts any public IdP without per-provider config.
		const discoveryRes = await fetch(
			`/api/sso-discovery?issuer=${encodeURIComponent(issuer)}`,
		).catch(() => null);
		const discovery = discoveryRes ? await discoveryRes.json().catch(() => null) : null;

		if (!discoveryRes || !discoveryRes.ok || !discovery) {
			setError(
				discovery?.error ?? 'Could not fetch the provider configuration from the issuer.',
			);
			setSubmitting(false);
			return;
		}

		const result = await authClient.sso.register({
			providerId,
			issuer,
			// Domain-based provider routing isn't used (login is by providerId), but
			// Better Auth requires a string here, so send empty.
			domain: '',
			oidcConfig: {
				clientId,
				clientSecret,
				skipDiscovery: true,
				authorizationEndpoint: discovery.authorizationEndpoint,
				tokenEndpoint: discovery.tokenEndpoint,
				userInfoEndpoint: discovery.userInfoEndpoint ?? undefined,
				jwksEndpoint: discovery.jwksEndpoint ?? undefined,
				discoveryEndpoint: discovery.discoveryEndpoint,
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

	const handleDelete = async (id: string) => {
		if (
			!window.confirm(
				`Delete SSO provider "${id}"? Users will no longer be able to sign in with it.`,
			)
		) {
			return;
		}

		setError(null);
		setMessage(null);
		setDeletingId(id);

		const result = await authClient.sso.deleteProvider({ providerId: id });

		if (result.error) {
			setError(result.error.message ?? 'Failed to delete the SSO provider.');
			setDeletingId(null);
			return;
		}

		setMessage(`SSO provider "${id}" deleted.`);
		setDeletingId(null);
		setProviders(await fetchSsoProviders());
	};

	const provider = providers[0];
	const isConfigured = Boolean(provider);

	return (
		<div className="h-full overflow-auto p-6">
			<div className="mx-auto max-w-xl">
				<h1 className="mb-1 text-lg font-semibold text-zinc-900 dark:text-zinc-100">
					Single Sign-On (OIDC)
				</h1>
				<p className="mb-4 text-xs text-zinc-500">
					Configure a single OpenID Connect provider. Endpoints are discovered from the
					issuer. The client secret is stored securely and never shown again.
				</p>

				{error ? <p className="mb-3 text-xs text-red-500">{error}</p> : null}
				{message ? <p className="mb-3 text-xs text-[#76b900]">{message}</p> : null}

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
							disabled={isConfigured}
							value={provider ? provider.providerId : providerId}
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
							disabled={isConfigured}
							value={provider ? provider.issuer : issuer}
							onChange={(event) => setIssuer(event.target.value)}
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
							disabled={isConfigured}
							value={isConfigured ? '' : clientId}
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
							disabled={isConfigured}
							placeholder={isConfigured ? '••••••••' : undefined}
							value={isConfigured ? '' : clientSecret}
							onChange={(event) => setClientSecret(event.target.value)}
							className={inputClass}
						/>
					</div>

					{provider ? (
						<button
							type="button"
							onClick={() => handleDelete(provider.providerId)}
							disabled={deletingId === provider.providerId}
							className="cursor-pointer self-start rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950"
						>
							{deletingId === provider.providerId ? 'Deleting…' : 'Delete provider'}
						</button>
					) : (
						<button
							type="submit"
							disabled={submitting}
							className="cursor-pointer self-start rounded-md bg-[#76b900] px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-[#6aa600] disabled:opacity-50"
						>
							{submitting ? 'Saving…' : 'Save provider'}
						</button>
					)}
				</form>
			</div>
		</div>
	);
};
