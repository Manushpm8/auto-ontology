// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Cursor (and other desktop MCP clients) register a short-lived loopback HTTP
// listener and/or a private-use URI scheme as the OAuth redirect. Navigating
// Chrome to that URL is what produces "This site can't be reached": the
// listener is gone (or bound to a different localhost address than Chrome
// resolved). These helpers decide when a Location is that callback, and deliver
// the code with fetch instead of a top-level navigation.

const BLOCKED_PROTOCOLS = new Set(['file:', 'javascript:', 'data:', 'blob:', 'ws:', 'wss:']);

const isLoopbackHostname = (hostname: string): boolean =>
	hostname === 'localhost' ||
	hostname === '127.0.0.1' ||
	hostname === '::1' ||
	hostname === '[::1]';

const originPort = (url: URL): string => {
	if (url.port) return url.port;
	return url.protocol === 'https:' ? '443' : '80';
};

/** Loopback HTTP or a private-use scheme; never `file:` / `javascript:` / web origins. */
export const isSafeMcpRedirect = (url: URL): boolean => {
	if (BLOCKED_PROTOCOLS.has(url.protocol)) return false;
	if (url.protocol !== 'http:' && url.protocol !== 'https:') return true;
	return isLoopbackHostname(url.hostname);
};

/** True when `url` is an MCP client's callback, not a GSF page. */
export const isMcpClientCallback = (url: URL, requestUrl: string): boolean => {
	if (!isSafeMcpRedirect(url)) return false;
	if (url.protocol !== 'http:' && url.protocol !== 'https:') return true;
	try {
		// Consent, login, and other GSF pages are also loopback in local dev.
		// Only a *different port* (Cursor's :8787, Claude's ephemeral port, …)
		// is the client's listener.
		return originPort(url) !== originPort(new URL(requestUrl));
	} catch {
		return false;
	}
};

const loopbackCandidates = (parsed: URL): string[] => {
	const aliases = ['localhost', '127.0.0.1', '::1'];
	return [
		parsed.toString(),
		...aliases
			.filter((hostname) => parsed.hostname !== hostname)
			.map((hostname) => {
				const next = new URL(parsed);
				next.hostname = hostname;
				return next.toString();
			}),
	];
};

// Fetch every localhost alias so an IPv4 Chrome tab still hits a listener that
// bound `[::1]:8787` (and the reverse). `no-cors` is required: the listener is
// not GSF and will not send ACAO. Keep the tab on a GSF page either way.
export const deliverLoopbackCallback = async (redirectUrl: string): Promise<boolean> => {
	let parsed: URL;
	try {
		parsed = new URL(redirectUrl);
	} catch {
		return false;
	}
	if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
	if (!isLoopbackHostname(parsed.hostname)) return false;

	await Promise.allSettled(
		loopbackCandidates(parsed).map((url) =>
			fetch(url, { mode: 'no-cors', credentials: 'omit', keepalive: true }),
		),
	);
	return true;
};
