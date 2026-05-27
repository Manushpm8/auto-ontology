// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { create } from 'zustand';
import { useShallow } from 'zustand/react/shallow';
import { streamChat } from '@/api/chat';
import { conversationsApi } from '@/api/conversations';
import { getQueryClient } from '@/lib/queryClient';
import type { ChatMessage, GraphStep } from '@/types/chat';

// Centralised cache invalidation. ``getQueryClient`` returns the per-tab
// singleton the React tree uses, so this stays in sync with mounted
// ``useQuery``s. We invalidate both the list (titles / order may have
// changed) and the matching detail snapshot (so a later remount of the
// conv won't return a snapshot that's missing the just-persisted message).
const invalidateConversationData = (convId: string): void => {
	const qc = getQueryClient();
	qc.invalidateQueries({ queryKey: ['conversations'] });
	qc.invalidateQueries({ queryKey: ['conversations', 'detail', convId] });
};

// Chat-stream state lives outside the React component tree so navigation
// (leaving the /chat route and coming back) does not abort the in-flight
// request or wipe the visible progress. Components subscribe via selectors so
// they only re-render when the slice they care about changes.

let nextId = 0;
const uid = () => `msg-${Date.now()}-${nextId++}`;

const stringifySqlResponse = (value: unknown): string | undefined => {
	if (value == null) return undefined;
	if (typeof value === 'string') return value.trim() ? value : undefined;
	try {
		return JSON.stringify(value);
	} catch {
		return undefined;
	}
};

// Per-conversation slices: the user can be viewing conversation A while a
// stream for conversation B is finishing in the background. Sending a new
// message anywhere, however, cancels any in-flight stream — we run a single
// generation at a time (one ``controller`` field below). When that cancel
// happens in a *different* conversation we leave a soft notice there so the
// owner of that conv notices their previous answer never arrived.
type ChatState = {
	messagesByConv: Record<string, ChatMessage[]>;
	stepsByConv: Record<string, GraphStep[]>;
	errorsByConv: Record<string, string | null>;
	// Soft, non-error UI notices ("Generation stopped." / "Cancelled because
	// you sent a new message in another conversation."). Rendered in a less
	// alarming style than ``errorsByConv``.
	noticesByConv: Record<string, string | null>;
	activeConvId: string | null;
	streamingConvId: string | null;
	controller: AbortController | null;

	sendMessage: (text: string, convId: string) => Promise<void>;
	stopGeneration: () => void;
	setMessagesForConv: (convId: string, messages: ChatMessage[]) => void;
	setActiveConvId: (id: string | null) => void;
	clearActive: () => void;
	dropConversation: (convId: string) => void;
};

const updateRecord = <T>(record: Record<string, T>, key: string, value: T): Record<string, T> => ({
	...record,
	[key]: value,
});

const removeKey = <T>(record: Record<string, T>, key: string): Record<string, T> => {
	if (!(key in record)) return record;
	const next = { ...record };
	delete next[key];
	return next;
};

export const useChatStore = create<ChatState>((set, get) => ({
	messagesByConv: {},
	stepsByConv: {},
	errorsByConv: {},
	noticesByConv: {},
	activeConvId: null,
	streamingConvId: null,
	controller: null,

	setMessagesForConv: (convId, messages) =>
		set((s) => ({ messagesByConv: updateRecord(s.messagesByConv, convId, messages) })),

	setActiveConvId: (id) => set({ activeConvId: id }),

	stopGeneration: () => {
		const { controller, streamingConvId } = get();
		if (!controller) return;
		controller.abort();
		set((s) => ({
			controller: null,
			streamingConvId: null,
			noticesByConv: streamingConvId
				? updateRecord(s.noticesByConv, streamingConvId, 'Generation stopped.')
				: s.noticesByConv,
		}));
	},

	clearActive: () => {
		const { controller, streamingConvId } = get();
		if (controller) controller.abort();
		set((s) => ({
			controller: null,
			streamingConvId: null,
			noticesByConv: streamingConvId
				? updateRecord(s.noticesByConv, streamingConvId, 'Generation stopped.')
				: s.noticesByConv,
		}));
	},

	dropConversation: (convId) => {
		const state = get();
		if (state.streamingConvId === convId) {
			state.controller?.abort();
		}
		set((s) => ({
			messagesByConv: removeKey(s.messagesByConv, convId),
			stepsByConv: removeKey(s.stepsByConv, convId),
			errorsByConv: removeKey(s.errorsByConv, convId),
			noticesByConv: removeKey(s.noticesByConv, convId),
			streamingConvId: s.streamingConvId === convId ? null : s.streamingConvId,
			controller: s.streamingConvId === convId ? null : s.controller,
			activeConvId: s.activeConvId === convId ? null : s.activeConvId,
		}));
		// Drop the React Query detail cache so a later refetch can't return
		// a snapshot for a conversation that no longer exists. The list
		// cache is invalidated by the calling delete mutation.
		getQueryClient().removeQueries({ queryKey: ['conversations', 'detail', convId] });
	},

	sendMessage: async (text, convId) => {
		// Single in-flight stream globally. If the previous stream belongs to
		// a *different* conversation we leave a soft notice on it so the user
		// can see their previous request was cancelled when they navigate
		// back. Same-conv re-submits silently supersede (no notice — the new
		// answer is what the user wants there anyway).
		const prevController = get().controller;
		const prevStreaming = get().streamingConvId;
		if (prevController && prevStreaming && prevStreaming !== convId) {
			prevController.abort();
			set((s) => ({
				controller: null,
				streamingConvId: null,
				noticesByConv: updateRecord(
					s.noticesByConv,
					prevStreaming,
					'Generation cancelled because you sent a new message in another conversation.',
				),
			}));
		} else if (prevController) {
			prevController.abort();
		}

		const userMsg: ChatMessage = {
			id: uid(),
			role: 'user',
			content: text,
			timestamp: Date.now(),
		};

		// A "pre-stream" controller stakes a claim on the slot while we wait
		// for the user message to persist. If anything supersedes us during
		// that await (another ``sendMessage`` or ``stopGeneration``), the
		// controller will no longer match and we bail out without ever
		// opening the SSE connection.
		const preController = new AbortController();
		set((s) => ({
			messagesByConv: updateRecord(s.messagesByConv, convId, [
				...(s.messagesByConv[convId] ?? []),
				userMsg,
			]),
			stepsByConv: updateRecord(s.stepsByConv, convId, []),
			errorsByConv: updateRecord(s.errorsByConv, convId, null),
			noticesByConv: updateRecord(s.noticesByConv, convId, null),
			streamingConvId: convId,
			controller: preController,
		}));

		// Persist the user message *before* opening the stream so the
		// server-side ordering can never be (assistant, user). The visible
		// state is already updated locally — this is purely for durability
		// and reload-time ordering. Failure is logged but not surfaced; the
		// stream still runs, the worst case is a missing message on reload.
		try {
			await conversationsApi.addMessage(convId, { role: 'user', content: text });
			invalidateConversationData(convId);
		} catch (err: unknown) {
			console.error('chat: failed to persist user message', err);
		}

		// Superseded while we were awaiting persistence.
		if (get().controller !== preController) {
			return;
		}

		// Capture the controller in the closures below so each callback
		// can prove it belongs to the same request that is currently
		// considered active. Comparing by ``streamingConvId`` alone is not
		// enough: two rapid sends to the *same* conversation would share
		// the same id and an old callback could append a duplicate reply.
		const controller = streamChat(
			{ question: text, conversationId: convId },
			{
				onStep(event) {
					if (get().controller !== controller) return;
					set((s) => {
						const current = s.stepsByConv[convId] ?? [];
						const completed = current.map((step) => ({
							...step,
							status: 'completed' as const,
						}));
						return {
							stepsByConv: updateRecord(s.stepsByConv, convId, [
								...completed,
								{ node: event.node, label: event.label, status: 'active' },
							]),
						};
					});
				},

				onResult(event) {
					if (get().controller !== controller) return;
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

					set((s) => {
						const msgs = s.messagesByConv[convId] ?? [];
						const steps = s.stepsByConv[convId] ?? [];
						return {
							messagesByConv: updateRecord(s.messagesByConv, convId, [
								...msgs,
								assistantMsg,
							]),
							stepsByConv: updateRecord(
								s.stepsByConv,
								convId,
								steps.map((step) => ({ ...step, status: 'completed' as const })),
							),
							controller: null,
							streamingConvId: null,
						};
					});

					conversationsApi
						.addMessage(convId, {
							role: 'assistant',
							content,
							sqlCode: sql ?? null,
							sqlResponse: sqlResponse ?? null,
						})
						.then(() => invalidateConversationData(convId))
						.catch((err: unknown) => {
							console.error('chat: failed to persist assistant message', err);
						});
				},

				onError(event) {
					if (get().controller !== controller) return;
					set((s) => ({
						errorsByConv: updateRecord(s.errorsByConv, convId, event.message),
						controller: null,
						streamingConvId: null,
					}));
				},
			},
		);

		// Streamchat may have already returned its real controller by the
		// time we get here. Only install it if our pre-stream slot is still
		// the active one (we may have been superseded between the await
		// above and now via another ``sendMessage``).
		if (get().controller === preController) {
			set({ controller });
		} else {
			controller.abort();
		}
	},
}));

// Stable sentinels — the selector below must NEVER fabricate a new array on
// the fly. ``useShallow`` shallow-compares the selector output; a fresh ``[]``
// every call would always look different and React would loop on every render
// (and log "The result of getServerSnapshot should be cached").
const EMPTY_MESSAGES: ChatMessage[] = [];
const EMPTY_STEPS: GraphStep[] = [];

// Slice tuned for the chat view: each field reflects the currently-active
// conversation. ``useShallow`` keeps a single subscription that fires only
// when one of these primitives actually changes.
export const useActiveChat = () =>
	useChatStore(
		useShallow((s) => {
			const convId = s.activeConvId;
			const messages = convId ? (s.messagesByConv[convId] ?? EMPTY_MESSAGES) : EMPTY_MESSAGES;
			// Steps belong to whichever conversation is streaming; we only show
			// them when the user is actually looking at that conversation.
			const isStreaming = s.streamingConvId !== null && s.streamingConvId === convId;
			const steps =
				isStreaming && convId ? (s.stepsByConv[convId] ?? EMPTY_STEPS) : EMPTY_STEPS;
			const error = convId ? (s.errorsByConv[convId] ?? null) : null;
			const notice = convId ? (s.noticesByConv[convId] ?? null) : null;
			return { messages, steps, isLoading: isStreaming, error, notice };
		}),
	);
