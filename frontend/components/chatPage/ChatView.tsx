'use client';

import { useCallback, useEffect, useState } from 'react';
import { useChat } from '@/lib/useChat';
import type { Conversation } from '@/types/chat';
import {
	conversationsApi,
	toConversation,
	type ConversationSummary,
	type ConversationDetail,
} from '@/api/conversations';
import { Breadcrumbs } from '@/components/Breadcrumbs';
import { ChatSidebar } from './ChatSidebar';
import { MessageList } from './MessageList';
import { StepIndicator } from './StepIndicator';
import { ChatInput } from './ChatInput';

export const ChatView = () => {
	const [activeConvId, setActiveConvId] = useState<string | null>(null);
	const [sidebarOpen, setSidebarOpen] = useState(false);
	const [conversations, setConversations] = useState<Conversation[]>([]);

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
		refreshConversations();
	}, [refreshConversations]);

	const handleNewChat = useCallback(async () => {
		clearMessages();
		setActiveConvId(null);
		setSidebarOpen(false);
		await refreshConversations();
	}, [clearMessages, refreshConversations]);

	const handleSelectConversation = useCallback(
		async (id: string) => {
			if (id === activeConvId) {
				setSidebarOpen(false);
				return;
			}
			try {
				const detail: ConversationDetail = await conversationsApi.get(id);
				const conv = toConversation(detail);
				setActiveConvId(id);
				setMessages(conv.messages);
				setSidebarOpen(false);
			} catch {
				// ignore fetch errors
			}
		},
		[activeConvId, setMessages],
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
				}
				await refreshConversations();
			} catch {
				// ignore
			}
		},
		[activeConvId, clearMessages, refreshConversations],
	);

	const handleSend = useCallback(
		async (text: string) => {
			let convId = activeConvId;
			if (!convId) {
				try {
					const title = text.slice(0, 50) || 'New conversation';
					const created = await conversationsApi.create(title);
					convId = created.id;
					setActiveConvId(convId);
					refreshConversations();
				} catch {
					return;
				}
			}
			sendMessage(text, convId);
		},
		[activeConvId, sendMessage, refreshConversations],
	);

	return (
		<div className="flex h-screen bg-white dark:bg-zinc-950">
			<ChatSidebar
				conversations={conversations}
				activeId={activeConvId}
				onSelect={handleSelectConversation}
				onNewChat={handleNewChat}
				onRename={handleRename}
				onDelete={handleDelete}
				isOpen={sidebarOpen}
				onToggle={() => setSidebarOpen((o) => !o)}
			/>

			<main className="flex min-w-0 flex-1 flex-col">
				<header className="flex items-center border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
					<Breadcrumbs
						items={[
							{ label: 'Home', href: '/' },
							{ label: 'Chat', href: '/chat' },
						]}
					/>
				</header>

				<MessageList messages={messages} />

				<div className="mx-auto w-full max-w-3xl">
					<StepIndicator steps={steps} visible={isLoading} />
				</div>

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
