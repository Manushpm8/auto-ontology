// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useChat } from '@/lib/useChat';
import type { Conversation } from '@/types/chat';
import {
	conversationsApi,
	toConversation,
	type ConversationSummary,
	type ConversationDetail,
} from '@/api/conversations';
import { ChatSidebar } from './ChatSidebar';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';

export const ChatView = () => {
	const router = useRouter();
	const pathname = usePathname();
	const searchParams = useSearchParams();
	const rawFocus = searchParams.get('focus');
	const focusId = rawFocus != null && rawFocus.trim() !== '' ? rawFocus.trim() : null;

	const [activeConvId, setActiveConvId] = useState<string | null>(focusId);
	const [sidebarOpen, setSidebarOpen] = useState(false);
	const [conversations, setConversations] = useState<Conversation[]>([]);
	const [conversationsLoading, setConversationsLoading] = useState(true);
	const [activeConversationLoading, setActiveConversationLoading] = useState<boolean>(
		focusId !== null,
	);
	const loadedFocusRef = useRef<string | null>(null);

	const {
		messages,
		setMessages,
		steps,
		isLoading,
		error,
		sendMessage,
		stopGeneration,
		clearMessages,
	} = useChat();

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

	const refreshConversations = useCallback(async () => {
		try {
			const summaries = await conversationsApi.list();
			setConversations(
				summaries.map((s: ConversationSummary) => ({
					id: s.id,
					title: s.title || 'New conversation',
					messages: [],
					createdAt: new Date(s.createdAt).getTime(),
				})),
			);
		} catch {
			// ignore fetch errors
		}
	}, []);

	useEffect(() => {
		let active = true;
		setConversationsLoading(true);
		conversationsApi
			.list()
			.then((summaries: ConversationSummary[]) => {
				if (!active) return;
				setConversations(
					summaries.map((s) => ({
						id: s.id,
						title: s.title || 'New conversation',
						messages: [],
						createdAt: new Date(s.createdAt).getTime(),
					})),
				);
			})
			.catch(() => {})
			.finally(() => {
				if (active) setConversationsLoading(false);
			});
		return () => {
			active = false;
		};
	}, []);

	useEffect(() => {
		if (!focusId) {
			loadedFocusRef.current = null;
			setActiveConversationLoading(false);
			return;
		}
		if (loadedFocusRef.current === focusId) return;
		loadedFocusRef.current = focusId;
		let active = true;
		setActiveConversationLoading(true);
		conversationsApi
			.get(focusId)
			.then((detail: ConversationDetail) => {
				if (!active) return;
				const conv = toConversation(detail);
				setActiveConvId(detail.id);
				setMessages(conv.messages);
			})
			.catch(() => {
				if (!active) return;
				loadedFocusRef.current = null;
				updateFocusInUrl(null);
			})
			.finally(() => {
				if (active) setActiveConversationLoading(false);
			});
		return () => {
			active = false;
		};
	}, [focusId, setMessages, updateFocusInUrl]);

	const handleNewChat = useCallback(async () => {
		clearMessages();
		setActiveConvId(null);
		setSidebarOpen(false);
		loadedFocusRef.current = null;
		updateFocusInUrl(null);
		await refreshConversations();
	}, [clearMessages, refreshConversations, updateFocusInUrl]);

	const handleSelectConversation = useCallback(
		async (id: string) => {
			if (id === activeConvId) {
				setSidebarOpen(false);
				updateFocusInUrl(id);
				return;
			}
			setActiveConversationLoading(true);
			setMessages([]);
			setActiveConvId(id);
			setSidebarOpen(false);
			updateFocusInUrl(id);
			try {
				const detail: ConversationDetail = await conversationsApi.get(id);
				const conv = toConversation(detail);
				loadedFocusRef.current = id;
				setActiveConvId(detail.id);
				setMessages(conv.messages);
			} catch {
				// ignore fetch errors
			} finally {
				setActiveConversationLoading(false);
			}
		},
		[activeConvId, setMessages, updateFocusInUrl],
	);

	const handleRename = useCallback(
		async (id: string, title: string) => {
			try {
				await conversationsApi.rename(id, title);
				await refreshConversations();
			} catch {
				// ignore
			}
		},
		[refreshConversations],
	);

	const handleDelete = useCallback(
		async (id: string) => {
			try {
				await conversationsApi.delete(id);
				if (activeConvId === id) {
					clearMessages();
					setActiveConvId(null);
					loadedFocusRef.current = null;
					updateFocusInUrl(null);
				}
				await refreshConversations();
			} catch {
				// ignore
			}
		},
		[activeConvId, clearMessages, refreshConversations, updateFocusInUrl],
	);

	const handleSend = useCallback(
		async (text: string) => {
			let convId = activeConvId;
			if (!convId) {
				try {
					const title = text.slice(0, 50) || 'New conversation';
					const created = await conversationsApi.create(title);
					convId = created.id;
					loadedFocusRef.current = convId;
					setActiveConvId(convId);
					updateFocusInUrl(convId);
					refreshConversations();
				} catch {
					return;
				}
			}
			sendMessage(text, convId);
		},
		[activeConvId, sendMessage, refreshConversations, updateFocusInUrl],
	);

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
				isLoading={conversationsLoading}
			/>

			<main className="flex min-w-0 flex-1 flex-col">
				<MessageList
					messages={messages}
					isLoading={isLoading}
					steps={steps}
					isLoadingConversation={activeConversationLoading}
				/>

				{error && (
					<div className="mx-auto w-full max-w-3xl px-4 py-2">
						<p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
							{error}
						</p>
					</div>
				)}

				<ChatInput onSend={handleSend} onStop={stopGeneration} isLoading={isLoading} />
			</main>
		</div>
	);
};
