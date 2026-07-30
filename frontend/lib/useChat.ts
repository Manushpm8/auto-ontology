// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { streamChat, fetchCharts } from '@/api/chat';
import { conversationsApi } from '@/api/conversations';
import type { ChatMessage, GraphStep } from '@/types/chat';

let nextId = 0;
const uid = () => `msg-${Date.now()}-${nextId++}`;

const GENERIC_ERROR_MESSAGE =
	'Something went wrong. Please try again, and if the issue persists, contact our support';

// The agent returns the executed-DB rows under `sql_response_from_db`. It can
// be a stringified markdown/CSV table or a structured ``list[dict]`` payload.
// Normalise both shapes into a single string so DB persistence and parsing in
// `DynamicTable` stay simple (compact JSON for objects → cheap to re-parse).
const stringifySqlResponse = (value: unknown): string | undefined => {
	if (value == null) return undefined;
	if (typeof value === 'string') return value.trim() ? value : undefined;
	try {
		return JSON.stringify(value);
	} catch {
		return undefined;
	}
};

/** Strip ```chart / ```chart-carousel fences so Message 1 stays prose-only. */
const stripChartFences = (markdown: string): string =>
	markdown
		.replace(/(^|\n)```(?:chart|chart-carousel)\b[\s\S]*?```/g, '\n')
		.replace(/\n{3,}/g, '\n\n')
		.trim();

const chartsToFencedContent = (charts: Record<string, unknown>[]): string =>
	charts.map((spec) => `\`\`\`chart\n${JSON.stringify(spec)}\n\`\`\``).join('\n\n');

export const useChat = () => {
	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [steps, setSteps] = useState<GraphStep[]>([]);
	const [isLoading, setIsLoading] = useState(false);
	const controllerRef = useRef<AbortController | null>(null);
	// Bumped on every `sendMessage`/`clearConversation` so the async step-2
	// chart fetch (which isn't tied to `controllerRef`'s AbortController) can
	// tell it's stale and skip mutating state after the user stopped/started
	// a new turn while it was still in flight.
	const requestIdRef = useRef(0);

	const appendAssistantMessage = useCallback(
		(
			conversationId: string | null,
			content: string,
			extras?: { sql?: string; sqlResponse?: string },
		) => {
			const assistantMsg: ChatMessage = {
				id: uid(),
				role: 'assistant',
				content,
				sql: extras?.sql,
				sqlResponse: extras?.sqlResponse,
				timestamp: Date.now(),
			};
			setMessages((prev) => [...prev, assistantMsg]);

			if (!conversationId) return;

			// Persist the assistant turn to the conversation history. Analytics
			// is captured server-side in the chat proxy route, so there is no
			// analytics work to do here.
			conversationsApi
				.addMessage(conversationId, {
					role: 'assistant',
					content,
					sqlCode: extras?.sql ?? null,
					sqlResponse: extras?.sqlResponse ?? null,
				})
				.catch(() => {});
		},
		[],
	);

	useEffect(
		() => () => {
			controllerRef.current?.abort();
			controllerRef.current = null;
		},
		[],
	);

	const sendMessage = useCallback(
		(text: string, conversationId: string | null) => {
			const userMsg: ChatMessage = {
				id: uid(),
				role: 'user',
				content: text,
				timestamp: Date.now(),
			};

			// Lock the input immediately so the Send button morphs into Stop and
			// duplicate sends are ignored — but DO NOT add the user message to
			// the conversation yet. We only commit it (UI + DB) once the backend
			// accepts the request via the `onStart` callback below; on 409
			// "Conversation in progress" (or any other pre-stream error) the
			// message is never persisted, keeping the chat history clean.
			setSteps([]);
			setIsLoading(true);
			const requestId = ++requestIdRef.current;

			const finishLoading = () => {
				setSteps((prev) => prev.map((s) => ({ ...s, status: 'completed' as const })));
				setIsLoading(false);
				controllerRef.current = null;
			};

			const controller = streamChat(
				{ question: text },
				{
					onStart() {
						setMessages((prev) => [...prev, userMsg]);
						if (conversationId) {
							conversationsApi
								.addMessage(conversationId, { role: 'user', content: text })
								.catch(() => {});
						}
					},

					onStep(event) {
						setSteps((prev) => {
							const completed = prev.map((s) => ({
								...s,
								status: 'completed' as const,
							}));
							return [
								...completed,
								{ node: event.node, label: event.label, status: 'active' },
							];
						});
					},

					onResult(event) {
						const {
							response,
							sql_code: sqlCode,
							sql_response_from_db: sqlResponseFromDb,
						} = event.answer;
						const sqlResponse = stringifySqlResponse(sqlResponseFromDb);
						const prose = stripChartFences(response ?? '');

						// Step 1 — text + SQL, shown as soon as the SQL pipeline
						// resolves. Charts are not computed yet.
						const hasMessage1Content = Boolean(prose) || Boolean(sqlCode);
						if (hasMessage1Content) {
							appendAssistantMessage(conversationId, prose, { sql: sqlCode });
						}

						if (!sqlResponseFromDb) {
							// No executed result to visualize — nothing to build in
							// step 2, so wrap up here.
							if (!hasMessage1Content) {
								appendAssistantMessage(conversationId, GENERIC_ERROR_MESSAGE);
							}
							finishLoading();
							return;
						}

						// Step 2 — a separate request decides whether a chart applies
						// to the already-executed result. Keep the "thinking"
						// indicator up with its own step label while this resolves,
						// then render Message 2 as a chart or fall back to a plain
						// table.
						setSteps((prev) => [
							...prev.map((s) => ({ ...s, status: 'completed' as const })),
							{
								node: 'visualize',
								label: 'Building charts',
								status: 'active' as const,
							},
						]);

						fetchCharts(text, sqlCode, sqlResponseFromDb)
							.then((charts) => {
								if (requestId !== requestIdRef.current) return;
								if (charts && charts.length > 0) {
									appendAssistantMessage(
										conversationId,
										chartsToFencedContent(charts),
									);
								} else if (sqlResponse) {
									appendAssistantMessage(conversationId, '', { sqlResponse });
								}
							})
							.catch(() => {
								if (requestId !== requestIdRef.current) return;
								if (sqlResponse) {
									appendAssistantMessage(conversationId, '', { sqlResponse });
								}
							})
							.finally(() => {
								if (requestId !== requestIdRef.current) return;
								finishLoading();
							});
					},

					onError() {
						appendAssistantMessage(conversationId, GENERIC_ERROR_MESSAGE);
						finishLoading();
					},
				},
			);

			controllerRef.current = controller;
		},
		[appendAssistantMessage],
	);

	const clearConversation = useCallback(() => {
		requestIdRef.current++;
		controllerRef.current?.abort();
		controllerRef.current = null;
		setMessages([]);
		setSteps([]);
		setIsLoading(false);
	}, []);

	return {
		messages,
		setMessages,
		steps,
		isLoading,
		sendMessage,
		clearConversation,
	};
};
