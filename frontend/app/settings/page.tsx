'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon, IconName } from '@/components/icons';
import { promptsApi, type Prompt } from '@/api/settings';

type SettingsSectionProps = {
	title: string;
	subtitle: string;
	prompts: Prompt[];
	loading: boolean;
	onCreate: (content: string) => Promise<void>;
	onUpdate: (id: string, content: string) => Promise<void>;
	onDelete: (id: string) => Promise<void>;
};

const PromptEditor = ({
	initialValue,
	onSave,
	onCancel,
}: {
	initialValue: string;
	onSave: (content: string) => void;
	onCancel: () => void;
}) => {
	const [value, setValue] = useState(initialValue);
	const textareaRef = useRef<HTMLTextAreaElement>(null);

	useEffect(() => {
		textareaRef.current?.focus();
	}, []);

	return (
		<div className="space-y-3 p-4">
			<div className="rounded-lg border border-zinc-200/90 bg-white p-4 dark:border-zinc-700/90 dark:bg-zinc-900/60">
				<span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-zinc-500">
					Prompt
				</span>
				<div className="space-y-2">
					<textarea
						ref={textareaRef}
						value={value}
						onChange={(e) => setValue(e.target.value)}
						rows={3}
						placeholder="Enter your custom prompt prefix…"
						className="w-full resize-y rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm leading-relaxed text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300 dark:placeholder:text-zinc-600 dark:focus:border-emerald-400 dark:focus:ring-emerald-400/30"
					/>
					<div className="flex justify-end gap-2">
						<button
							type="button"
							onClick={onCancel}
							className="cursor-pointer rounded-md border border-zinc-300 px-2.5 py-1 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-400 dark:hover:bg-zinc-800"
						>
							Cancel
						</button>
						<button
							type="button"
							onClick={() => onSave(value)}
							className="cursor-pointer rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-600"
						>
							Save
						</button>
					</div>
				</div>
			</div>
		</div>
	);
};

const SettingsSection = ({
	title,
	subtitle,
	prompts,
	loading,
	onCreate,
	onUpdate,
	onDelete,
}: SettingsSectionProps) => {
	const hasPrompts = prompts.length > 0;
	const [editing, setEditing] = useState(false);

	const handleButtonClick = () => {
		setEditing(true);
	};

	const handleSave = async (content: string) => {
		const trimmed = content.trim();
		if (hasPrompts) {
			if (trimmed) {
				await onUpdate(prompts[0].id, trimmed);
			} else {
				await onDelete(prompts[0].id);
			}
		} else if (trimmed) {
			await onCreate(trimmed);
		}
		setEditing(false);
	};

	const handleCancel = () => {
		setEditing(false);
	};

	const renderBody = () => {
		if (editing) {
			return (
				<PromptEditor
					initialValue={hasPrompts ? prompts[0].content : ''}
					onSave={handleSave}
					onCancel={handleCancel}
				/>
			);
		}

		if (hasPrompts) {
			return (
				<div className="space-y-3 p-4">
					<div className="rounded-lg border border-zinc-200/90 bg-white p-4 dark:border-zinc-700/90 dark:bg-zinc-900/60">
						<span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-zinc-500">
							Prompt
						</span>
						<p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
							{prompts[0].content}
						</p>
					</div>
				</div>
			);
		}

		if (loading) return null;

		return (
			<div className="flex flex-col items-center justify-center gap-2 rounded-b-lg bg-zinc-50/80 px-8 py-10 dark:bg-zinc-900/30">
				<Icon
					name={IconName.ChatBubble}
					className="h-8 w-8 text-zinc-300 dark:text-zinc-600"
				/>
				<p className="text-sm text-zinc-400 dark:text-zinc-500">No Description</p>
			</div>
		);
	};

	return (
		<div className="rounded-lg border border-zinc-200/90 bg-white/90 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-700/90 dark:bg-zinc-950/50 dark:ring-white/[0.06]">
			<div className="flex items-center justify-between border-b border-zinc-200/90 px-5 py-3.5 dark:border-zinc-700/90">
				<div className="flex items-baseline gap-2">
					<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{title}
					</h2>
					<span className="text-sm text-zinc-500 dark:text-zinc-400">{subtitle}</span>
				</div>
				<button
					type="button"
					onClick={handleButtonClick}
					className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-emerald-300 px-3 py-1.5 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-50 dark:border-emerald-600 dark:text-emerald-400 dark:hover:bg-emerald-950/40"
				>
					<Icon name={IconName.Pencil} className="h-3.5 w-3.5" />
					{hasPrompts ? 'Edit Description' : 'Add Description'}
				</button>
			</div>

			{renderBody()}
		</div>
	);
};

export default function SettingsPage() {
	const [prompts, setPrompts] = useState<Prompt[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const fetchPrompts = useCallback(async () => {
		try {
			setError(null);
			const data = await promptsApi.get();
			setPrompts(data);
		} catch {
			setError('Failed to load custom prompts.');
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		fetchPrompts();
	}, [fetchPrompts]);

	const handleCreate = async (content: string) => {
		try {
			setError(null);
			const created = await promptsApi.create({ content });
			setPrompts((prev) => [created, ...prev]);
		} catch {
			setError('Failed to create custom prompt.');
		}
	};

	const handleUpdate = async (id: string, content: string) => {
		try {
			setError(null);
			const updated = await promptsApi.update(id, { content });
			setPrompts((prev) => prev.map((p) => (p.id === id ? updated : p)));
		} catch {
			setError('Failed to save custom prompt.');
		}
	};

	const handleDelete = async (id: string) => {
		try {
			setError(null);
			await promptsApi.delete(id);
			setPrompts((prev) => prev.filter((p) => p.id !== id));
		} catch {
			setError('Failed to delete custom prompt.');
		}
	};

	return (
		<div className="box-border flex h-full min-h-0 w-full min-w-0 flex-1 flex-col overflow-hidden bg-white dark:bg-zinc-950">
			{error ? (
				<div className="shrink-0 border-b border-red-200/90 bg-red-50 px-7 py-3 text-sm text-red-800 sm:px-10 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200">
					{error}
				</div>
			) : null}

			<main className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-[linear-gradient(180deg,rgba(255,255,255,1)_0%,rgba(250,250,250,0.6)_100%)] px-7 py-6 sm:px-10 sm:py-7 dark:bg-[linear-gradient(180deg,rgba(9,9,11,1)_0%,rgba(24,24,27,0.5)_100%)]">
				<div className="w-full space-y-5">
					<SettingsSection
						title="Custom prompts"
						subtitle="Default prompt prefixes automatically applied to all user prompts in this account"
						prompts={prompts}
						loading={loading}
						onCreate={handleCreate}
						onUpdate={handleUpdate}
						onDelete={handleDelete}
					/>
				</div>
			</main>
		</div>
	);
}
