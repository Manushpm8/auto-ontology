// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useRef, useState } from 'react';
import { streamChat } from '@/api/chat';
import { conversationsApi } from '@/api/conversations';
import type { ChatMessage, GraphStep } from '@/types/chat';

let nextId = 0;
const uid = () => `msg-${Date.now()}-${nextId++}`;

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

export const useChat = () => {
	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [steps, setSteps] = useState<GraphStep[]>([]);
	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const controllerRef = useRef<AbortController | null>(null);

	const sendMessage = useCallback((text: string, convId: string | null) => {
		const userMsg: ChatMessage = {
			id: uid(),
			role: 'user',
			content: text,
			timestamp: Date.now(),
		};

		setMessages((prev) => [...prev, userMsg]);
		setSteps([]);
		setIsLoading(true);
		setError(null);

		if (convId) {
			conversationsApi.addMessage(convId, { role: 'user', content: text }).catch(() => {});
		}

		const controller = streamChat(
			{ question: text },
			{
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
					const answer = event.answer;
					const content =
						typeof answer.response === 'string'
							? answer.response
							: JSON.stringify(answer, null, 2);
					const sql = typeof answer.sql_code === 'string' ? answer.sql_code : undefined;
					const sqlResponse = stringifySqlResponse(answer.sql_response_from_db);

					const assistantMsg: ChatMessage = {
						id: uid(),
						role: 'assistant',
						content,
						sql,
						sqlResponse,
						timestamp: Date.now(),
					};

					setMessages((prev) => [...prev, assistantMsg]);
					setSteps((prev) => prev.map((s) => ({ ...s, status: 'completed' as const })));
					setIsLoading(false);
					controllerRef.current = null;

					if (convId) {
						conversationsApi
							.addMessage(convId, {
								role: 'assistant',
								content,
								sqlCode: sql ?? null,
								sqlResponse: sqlResponse ?? null,
							})
							.catch(() => {});
					}
				},

				onError(event) {
					setError(event.message);
					setIsLoading(false);
					controllerRef.current = null;
				},
			},
		);

		controllerRef.current = controller;
	}, []);

	const stopGeneration = useCallback(() => {
		controllerRef.current?.abort();
		controllerRef.current = null;
		setIsLoading(false);
	}, []);

	const clearMessages = useCallback(() => {
		controllerRef.current?.abort();
		controllerRef.current = null;
		setMessages([]);
		setSteps([]);
		setIsLoading(false);
		setError(null);
	}, []);

	return {
		messages,
		setMessages,
		steps,
		isLoading,
		error,
		sendMessage,
		stopGeneration,
		clearMessages,
	};
};
