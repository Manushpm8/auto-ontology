'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import type { Conversation } from '@/types/chat';
import { Icon, IconName } from '@/components/icons';
import { ConfirmModal } from '@/components/ConfirmModal';

type ChatSidebarProps = {
	conversations: Conversation[];
	activeId: string | null;
	onSelect: (id: string) => void;
	onNewChat: () => void;
	onRename: (id: string, title: string) => void;
	onDelete: (id: string) => void;
	isOpen: boolean;
	onToggle: () => void;
};

function ConversationItem({
	conv,
	isActive,
	onSelect,
	onRename,
	onDelete,
}: {
	conv: Conversation;
	isActive: boolean;
	onSelect: () => void;
	onRename: (title: string) => void;
	onDelete: () => void;
}) {
	const [menuOpen, setMenuOpen] = useState(false);
	const [editing, setEditing] = useState(false);
	const [confirmDelete, setConfirmDelete] = useState(false);
	const [editValue, setEditValue] = useState(conv.title);
	const inputRef = useRef<HTMLInputElement>(null);
	const menuRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (editing) inputRef.current?.focus();
	}, [editing]);

	useEffect(() => {
		if (!menuOpen) return;
		const handleClick = (e: MouseEvent) => {
			if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
				setMenuOpen(false);
			}
		};
		document.addEventListener('mousedown', handleClick);
		return () => document.removeEventListener('mousedown', handleClick);
	}, [menuOpen]);

	const handleRenameSubmit = useCallback(() => {
		const trimmed = editValue.trim();
		if (trimmed && trimmed !== conv.title) {
			onRename(trimmed);
		}
		setEditing(false);
	}, [editValue, conv.title, onRename]);

	if (editing) {
		return (
			<li>
				<form
					onSubmit={(e) => {
						e.preventDefault();
						handleRenameSubmit();
					}}
					className="flex gap-1 px-1 py-1"
				>
					<input
						ref={inputRef}
						value={editValue}
						onChange={(e) => setEditValue(e.target.value)}
						onBlur={handleRenameSubmit}
						onKeyDown={(e) => {
							if (e.key === 'Escape') {
								setEditValue(conv.title);
								setEditing(false);
							}
						}}
						className="min-w-0 flex-1 rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-black outline-none focus:border-[#76b900]"
					/>
				</form>
			</li>
		);
	}

	return (
		<li className="group relative">
			<button
				type="button"
				onClick={onSelect}
				className={`w-full rounded-lg px-3 py-2 pr-8 text-left text-sm transition-colors ${
					isActive
						? 'bg-zinc-100 font-medium text-black'
						: 'text-zinc-700 hover:bg-zinc-100 hover:text-black'
				}`}
			>
				<span className="line-clamp-1">{conv.title}</span>
				<span className="mt-0.5 block text-[10px] text-zinc-500">
					{new Date(conv.createdAt).toLocaleDateString()}
				</span>
			</button>

			<div ref={menuRef} className="absolute right-1 top-1.5">
				<button
					type="button"
					onClick={(e) => {
						e.stopPropagation();
						setMenuOpen((o) => !o);
					}}
					className="rounded p-1 text-zinc-400 opacity-0 transition-opacity hover:bg-zinc-200 hover:text-zinc-700 group-hover:opacity-100"
					aria-label="Conversation options"
				>
					<Icon name={IconName.DotsVertical} className="h-4 w-4" />
				</button>

				{menuOpen && (
					<div className="absolute right-0 top-7 z-30 w-32 rounded-lg border border-zinc-200 bg-white py-1 shadow-lg">
						<button
							type="button"
							onClick={() => {
								setMenuOpen(false);
								setEditValue(conv.title);
								setEditing(true);
							}}
							className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-100"
						>
							<Icon name={IconName.Pencil} className="h-3.5 w-3.5" />
							Rename
						</button>
						<button
							type="button"
							onClick={() => {
								setMenuOpen(false);
								setConfirmDelete(true);
							}}
							className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-red-600 hover:bg-red-50"
						>
							<Icon name={IconName.Trash} className="h-3.5 w-3.5" />
							Delete
						</button>
					</div>
				)}
			</div>

			<ConfirmModal
				open={confirmDelete}
				title="Delete conversation"
				message="Are you sure you want to delete this conversation? This action cannot be undone."
				onConfirm={() => {
					setConfirmDelete(false);
					onDelete();
				}}
				onCancel={() => setConfirmDelete(false)}
			/>
		</li>
	);
}

export const ChatSidebar = ({
	conversations,
	activeId,
	onSelect,
	onNewChat,
	onRename,
	onDelete,
	isOpen,
	onToggle,
}: ChatSidebarProps) => {
	return (
		<>
			{/* Mobile toggle */}
			<button
				type="button"
				onClick={onToggle}
				className="fixed top-3 left-3 z-30 rounded-lg bg-white p-2 shadow-md lg:hidden"
				aria-label="Toggle sidebar"
			>
				<Icon name={IconName.Menu} className="h-5 w-5 text-zinc-700" />
			</button>

			{/* Backdrop for mobile */}
			{isOpen && (
				<div
					className="fixed inset-0 z-20 bg-black/30 lg:hidden"
					onClick={onToggle}
					aria-hidden
				/>
			)}

			{/* Sidebar panel */}
			<aside
				className={`fixed inset-y-0 left-12 z-20 flex w-[296px] flex-col border-r border-zinc-200 bg-white transition-transform duration-200 lg:static lg:translate-x-0 ${
					isOpen ? 'translate-x-0' : '-translate-x-full'
				}`}
			>
				<div className="flex h-[65px] w-full shrink-0 items-center border-b border-zinc-200 px-4">
					<Link href="/" className="flex items-center gap-3">
						<Icon name={IconName.NvidiaLogo} className="h-6 w-auto text-[#76b900]" />
						<span className="text-sm font-semibold tracking-wide text-black">GSF</span>
					</Link>
				</div>

				<div className="px-3 py-3">
					<button
						type="button"
						onClick={onNewChat}
						className="flex w-full items-center gap-2 rounded-lg border border-zinc-300 px-3 py-2 text-sm font-medium text-black transition-colors hover:border-zinc-400 hover:bg-zinc-100"
					>
						<span className="text-lg leading-none text-[#76b900]">+</span>
						New Chat
					</button>
				</div>

				<nav className="flex-1 overflow-y-auto px-2 pb-2">
					{conversations.length === 0 ? (
						<p className="px-2 py-4 text-center text-xs text-zinc-500">
							No conversations yet
						</p>
					) : (
						<ul className="space-y-0.5">
							{conversations.map((conv) => (
								<ConversationItem
									key={conv.id}
									conv={conv}
									isActive={activeId === conv.id}
									onSelect={() => onSelect(conv.id)}
									onRename={(title) => onRename(conv.id, title)}
									onDelete={() => onDelete(conv.id)}
								/>
							))}
						</ul>
					)}
				</nav>
			</aside>
		</>
	);
};
