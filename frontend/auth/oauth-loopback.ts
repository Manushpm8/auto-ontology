// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Desktop MCP clients (Cursor, the Python SDK) register a short-lived loopback
// HTTP listener as the OAuth redirect. Navigating Chrome to that URL is what
// produces "This site can't be reached": the listener is gone, or bound to a
// different localhost address than Chrome resolved. These helpers decide when
// a Location is that listener and deliver the code with fetch instead of a
// top-level navigation.
//
// Only loopback HTTP(S) qualifies. A private-use scheme such as `cursor://` is
// left as an ordinary 302: the browser hands it to the OS protocol handler, and
// that navigation carries the user's consent click as its activation. Routing
// it through the handoff page would lose that activation and would let anyone
// who can craft a `/oauth/handoff?url=` link launch an arbitrary protocol
// handler from a GSF origin.

const isLoopbackHostname = (hostname: string): boolean =>
	hostname === 'localhost' ||
	hostname === '127.0.0.1' ||
	hostname === '::1' ||
	hostname === '[::1]';

const originPort = (url: URL): string => {
	if (url.port) return url.port;
	return url.protocol === 'https:' ? '443' : '80';
};

/** Loopback HTTP(S) only; never a web origin, a file, or a custom scheme. */
export const isLoopbackCallback = (url: URL): boolean => {
	if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
	return isLoopbackHostname(url.hostname);
};

/** True when `url` is an MCP client's loopback listener, not a GSF page. */
export const isMcpClientCallback = (url: URL, requestUrl: string): boolean => {
	if (!isLoopbackCallback(url)) return false;
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
	// `URL.hostname` accepts IPv6 only in bracketed form; a bare `::1` is
	// silently ignored and the previous hostname is kept.
	const aliases = ['localhost', '127.0.0.1', '[::1]'];
	return [
		parsed.toString(),
		...aliases
			.filter(
				(hostname) => parsed.hostname !== hostname && `[${parsed.hostname}]` !== hostname,
			)
			.map((hostname) => {
				const next = new URL(parsed);
				next.hostname = hostname;
				return next.toString();
			}),
	];
};

// Fetch every localhost alias so an IPv4 Chrome tab still hits a listener that
// bound `[::1]:8787` (and the reverse). `no-cors` is required: the listener is
// not GSF and will not send ACAO. A live listener answers with an opaque
// response, which counts as fulfilled; a closed port rejects. Returns true only
// when at least one alias was reached, so callers can tell delivery from a dead
// listener. Each attempt has a deadline: a listener that accepts the socket
// but never answers would otherwise leave the page in its waiting state forever.
const DELIVERY_TIMEOUT_MS = 5_000;

export const deliverLoopbackCallback = async (redirectUrl: string): Promise<boolean> => {
	let parsed: URL;
	try {
		parsed = new URL(redirectUrl);
	} catch {
		return false;
	}
	if (!isLoopbackCallback(parsed)) return false;

	const results = await Promise.allSettled(
		loopbackCandidates(parsed).map((url) =>
			fetch(url, {
				mode: 'no-cors',
				credentials: 'omit',
				keepalive: true,
				signal: AbortSignal.timeout(DELIVERY_TIMEOUT_MS),
			}),
		),
	);
	return results.some((result) => result.status === 'fulfilled');
};
