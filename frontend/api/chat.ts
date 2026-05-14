// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type {
	ChatRequest,
	ChatStreamEvent,
	StepEvent,
	ResultEvent,
	ErrorEvent,
} from '@/types/chat';

export type StreamChatCallbacks = {
	onStep: (event: StepEvent) => void;
	onResult: (event: ResultEvent) => void;
	onError: (event: ErrorEvent) => void;
};

/**
 * Opens an SSE connection to POST /api/chat/completions and invokes
 * callbacks as events arrive. Returns an AbortController the caller
 * can use to cancel mid-stream.
 */
export const streamChat = (
	payload: ChatRequest,
	callbacks: StreamChatCallbacks,
): AbortController => {
	const controller = new AbortController();

	const body = JSON.stringify({
		question: payload.question,
		acronyms: payload.acronyms ?? null,
		custom_prompts: payload.customPrompts ?? null,
	});

	(async () => {
		try {
			const res = await fetch('/api/chat/completions', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body,
				signal: controller.signal,
			});

			if (!res.ok || !res.body) {
				callbacks.onError({
					type: 'error',
					message: `Server responded with ${res.status}`,
				});
				return;
			}

			const reader = res.body.getReader();
			const decoder = new TextDecoder();
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split('\n');
				buffer = lines.pop() ?? '';

				for (const line of lines) {
					const trimmed = line.trim();
					if (!trimmed.startsWith('data: ')) continue;

					const data = trimmed.slice(6);
					if (data === '[DONE]') return;

					try {
						const event: ChatStreamEvent = JSON.parse(data);
						switch (event.type) {
							case 'step':
								callbacks.onStep(event);
								break;
							case 'result':
								callbacks.onResult(event);
								break;
							case 'error':
								callbacks.onError(event);
								break;
						}
					} catch {
						// skip malformed lines
					}
				}
			}
		} catch (err: unknown) {
			if (err instanceof DOMException && err.name === 'AbortError') return;
			callbacks.onError({
				type: 'error',
				message: err instanceof Error ? err.message : 'Unknown error',
			});
		}
	})();

	return controller;
};
