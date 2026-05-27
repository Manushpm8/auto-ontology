// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useChatStore } from '@/stores/chatStore';
import { toConversation } from '@/api/conversations';
import {
	useConversationDetail,
	useConversationsList,
	useCreateConversation,
	useDeleteConversation,
	useRenameConversation,
} from '@/lib/conversationsQueries';
import type { Conversation } from '@/types/chat';
// Import the sibling files directly instead of via the ``./index`` barrel:
// ``./index`` re-exports this very component, which would set up a circular
// import that only works as long as nothing in this module is used at
// initialisation time. Direct file imports keep the graph acyclic.
import { ChatError } from './ChatError';
import { ChatInput } from './ChatInput';
import { ChatSidebar } from './ChatSidebar';
import { MessageList } from './MessageList';

export const ChatView = () => {
	const router = useRouter();
	const pathname = usePathname();
	const searchParams = useSearchParams();
	const rawFocus = searchParams.get('focus');
	const focusId = rawFocus != null && rawFocus.trim() !== '' ? rawFocus.trim() : null;

	const [sidebarOpen, setSidebarOpen] = useState(false);

	// Chat-stream state lives in the global store so navigating away from the
	// /chat route and back does not abort the in-flight stream or wipe the
	// visible progress. Each field is subscribed to individually so unrelated
	// store updates do not re-render this component.
	const activeConvId = useChatStore((s) => s.activeConvId);
	const sendMessage = useChatStore((s) => s.sendMessage);
	const stopGeneration = useChatStore((s) => s.stopGeneration);
	const clearActive = useChatStore((s) => s.clearActive);
	const setActiveConvId = useChatStore((s) => s.setActiveConvId);
	const dropConversation = useChatStore((s) => s.dropConversation);

	// Conversations list, detail, and mutations are managed by React Query.
	// The list cache is invalidated by mutations and by the chat store after
	// it persists a streamed message — no manual ``refreshConversations``.
	const { data: summaries, isLoading: conversationsLoading } = useConversationsList();
	const { data: detailData, isLoading: detailLoading } = useConversationDetail(focusId);
	const createConvMut = useCreateConversation();
	const renameConvMut = useRenameConversation();
	const deleteConvMut = useDeleteConversation();

	const conversations = useMemo<Conversation[]>(
		() =>
			(summaries ?? []).map((s) => ({
				id: s.id,
				title: s.title || 'New conversation',
				messages: [],
				createdAt: new Date(s.createdAt).getTime(),
			})),
		[summaries],
	);

	const updateFocusInUrl = useCallback(
		(id: string | null) => {
			const params = new URLSearchParams(searchParams.toString());
			if (id) {
				params.set('focus', id);
			} else {
				params.delete('focus');
			}
			const query = params.toString();
			router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
		},
		[router, pathname, searchParams],
	);

	// Keep ``activeConvId`` in lock-step with the focus query param so the
	// MessageList swaps to the new conversation immediately, without first
	// rendering the old one's messages while ``useConversationDetail`` is
	// still in flight. If the cache already has data for this conv we'll
	// show it straight away; otherwise the spinner takes over until the
	// detail effect below seeds messages.
	useEffect(() => {
		if (activeConvId !== focusId) {
			setActiveConvId(focusId);
		}
	}, [focusId, activeConvId, setActiveConvId]);

	// When the detail query resolves, seed the chat store. ``getState`` is
	// used to read live store fields without subscribing — we must not
	// re-render this component on every messagesByConv change.
	useEffect(() => {
		if (!detailData) return;
		const { streamingConvId, messagesByConv, setMessagesForConv } = useChatStore.getState();
		// Never clobber the messages of a conversation that is actively
		// streaming — its in-progress state lives in the store.
		if (streamingConvId === detailData.id) return;
		// If we already have a non-empty cache for this conv, keep it (the
		// detail snapshot may be stale relative to what just streamed in).
		if (messagesByConv[detailData.id]?.length) return;
		const conv = toConversation(detailData);
		setMessagesForConv(detailData.id, conv.messages);
	}, [detailData]);

	const handleNewChat = useCallback(() => {
		clearActive();
		setActiveConvId(null);
		setSidebarOpen(false);
		updateFocusInUrl(null);
	}, [clearActive, setActiveConvId, updateFocusInUrl]);

	const handleSelectConversation = useCallback(
		(id: string) => {
			setSidebarOpen(false);
			if (id === activeConvId) {
				updateFocusInUrl(id);
				return;
			}
			// Detail will be fetched (or read from cache) by
			// ``useConversationDetail`` once focusId updates.
			updateFocusInUrl(id);
		},
		[activeConvId, updateFocusInUrl],
	);

	const handleRename = useCallback(
		(id: string, title: string) => {
			renameConvMut.mutate({ id, title });
		},
		[renameConvMut],
	);

	const handleDelete = useCallback(
		(id: string) => {
			deleteConvMut.mutate(id, {
				onSuccess: () => {
					dropConversation(id);
					if (focusId === id) updateFocusInUrl(null);
				},
			});
		},
		[deleteConvMut, dropConversation, focusId, updateFocusInUrl],
	);

	const handleSend = useCallback(
		async (text: string) => {
			let convId = activeConvId;
			const isNew = !convId;
			if (!convId) {
				try {
					const title = text.slice(0, 50) || 'New conversation';
					const created = await createConvMut.mutateAsync(title);
					convId = created.id;
				} catch {
					return;
				}
			}
			// Push the user message into the store *before* swapping the
			// focus so the freshly-created conversation already has its
			// first message by the time ``focusId`` changes. Otherwise the
			// spinner would briefly show against an empty MessageList.
			sendMessage(text, convId);
			if (isNew) {
				setActiveConvId(convId);
				updateFocusInUrl(convId);
			}
		},
		[activeConvId, createConvMut, sendMessage, setActiveConvId, updateFocusInUrl],
	);

	// Spinner stays on until the store actually has messages for ``focusId``.
	// Relying on ``activeConvId !== focusId`` alone caused a flash of an
	// empty MessageList: ``setActiveConvId`` runs in an effect *after* the
	// first render, so on render N+1 the equality already held while the
	// detail query was still in flight and ``messagesByConv[focusId]``
	// was still empty. Subscribing to the message count for the *focus*
	// conv keeps the spinner up until either the store seeds it (from
	// detailData) or ``sendMessage`` adds the first user message.
	const focusMessageCount = useChatStore((s) =>
		focusId ? (s.messagesByConv[focusId]?.length ?? 0) : 0,
	);
	const conversationLoading = !!focusId && detailLoading && focusMessageCount === 0;

	return (
		<div className="flex h-full bg-white dark:bg-zinc-950">
			<ChatSidebar
				conversations={conversations}
				activeId={activeConvId}
				onSelect={handleSelectConversation}
				onNewChat={handleNewChat}
				onRename={handleRename}
				onDelete={handleDelete}
				isOpen={sidebarOpen}
				onToggle={() => setSidebarOpen((o) => !o)}
				loading={conversationsLoading}
			/>

			<main className="flex min-w-0 flex-1 flex-col">
				<MessageList conversationLoading={conversationLoading} />
				<ChatError />
				<ChatInput onSend={handleSend} onStop={stopGeneration} />
			</main>
		</div>
	);
};
