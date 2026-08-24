// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import { semanticCompilationApi } from '@/api/settings';
import { formatDate } from '@/common/date';
import { Icon, IconName } from '@/common/icons';
import { ConfirmModal } from '@/common/modal';
import { Spinner } from '@/common/Spinner';
import { Toast } from '@/common/Toast';
import { Toggle } from '@/common/Toggle';
import { ToastVariant } from '@/enums/toast';

export const SemanticCompilationForm = ({ initialEnabled }: { initialEnabled: boolean }) => {
	// Seeded from the server (see page.tsx) so the correct state renders on first
	// paint; updated optimistically and rolled back if the save fails.
	const [enabled, setEnabled] = useState(initialEnabled);
	const [saving, setSaving] = useState(false);
	const [confirmModalOpen, setConfirmModalOpen] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [message, setMessage] = useState<string | null>(null);
	const [running, setRunning] = useState(false);
	const [lastSuccessAt, setLastSuccessAt] = useState<string | null>(null);
	useEffect(() => {
		semanticCompilationApi
			.getStatus()
			.then((result) => {
				setRunning(result.running);
				setLastSuccessAt(result.last_success_at);
			})
			.catch(() => {});
	}, []);

	const handleToggle = async () => {
		if (saving) return;
		const next = !enabled;
		setSaving(true);
		setError(null);
		setMessage(null);
		setEnabled(next);

		try {
			const result = await semanticCompilationApi.setEnabled(next);
			setEnabled(result.enabled);
			setMessage(
				result.enabled
					? 'Semantic compilation enabled — a compilation run has been triggered.'
					: 'Semantic compilation disabled.',
			);
		} catch {
			setEnabled(!next); // roll back the optimistic update
			setError('Failed to update semantic compilation. Please try again.');
		} finally {
			setSaving(false);
		}
	};

	const handleReset = () => {
		setError(null);
		setConfirmModalOpen(false);
		// The ingestion service deletes in the background and answers 202, so the
		// request only starts the reset. Nothing here depends on the response, so
		// it is left unawaited and only a failure to reach the API is surfaced.
		setMessage('Semantic layer reset started — it runs in the background.');
		semanticCompilationApi.reset().catch(() => {
			setMessage(null);
			setError('Failed to reset the semantic layer. Please try again.');
		});
	};

	return (
		<div className="h-full overflow-auto p-6">
			<div className="mx-auto max-w-xl">
				<h1 className="mb-1 text-lg font-semibold text-zinc-900 dark:text-zinc-100">
					Semantic Compilation
				</h1>
				<p className="mb-4 text-xs text-zinc-500">
					When enabled, the ingestion service compiles the semantic layer for every
					connected database on startup and once every 24 hours. Enabling it also triggers
					a compilation run immediately.
				</p>

				<div className="mb-3 flex items-center justify-between rounded-lg border border-zinc-200 px-4 py-3 dark:border-zinc-700">
					<div className="flex flex-col">
						<span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
							{running
								? 'Semantic compilation is running'
								: 'Semantic compilation is ready'}
						</span>
						<span className="text-xs text-zinc-500">
							Last semantic compilation:{' '}
							{lastSuccessAt
								? formatDate(lastSuccessAt, 'MMM DD YYYY, HH:mm Z')
								: '—'}
						</span>
					</div>
					{running ? (
						<span title="Semantic compilation is running">
							<Spinner className="h-4 w-4 shrink-0 text-[#76b900]" />
						</span>
					) : (
						<span
							className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#76b900]/15 text-[#76b900]"
							title="Semantic compilation is ready"
						>
							<Icon name={IconName.Check} className="h-3 w-3" />
						</span>
					)}
				</div>

				<div className="flex items-center justify-between rounded-lg border border-zinc-200 px-4 py-3 dark:border-zinc-700">
					<div className="flex flex-col">
						<span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
							Enable semantic compilation
						</span>
						<span className="text-xs text-zinc-500">
							{enabled ? 'Running on the 24h schedule.' : 'Currently off.'}
						</span>
					</div>
					<Toggle
						checked={enabled}
						aria-label="Enable semantic compilation"
						disabled={saving}
						onChange={handleToggle}
					/>
				</div>

				{enabled && (
					<div className="mt-3 flex items-center justify-between rounded-lg border border-zinc-200 px-4 py-3 dark:border-zinc-700">
						<div className="flex flex-col">
							<span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
								Reset semantic layer
							</span>
							<span className="text-xs text-zinc-500">
								Deletes the semantic layer for every database, and rebuild it from
								scratch.
							</span>
						</div>
						<button
							type="button"
							disabled={saving}
							onClick={() => setConfirmModalOpen(true)}
							className="cursor-pointer rounded-lg border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900/60 dark:text-red-300 dark:hover:bg-red-950/40"
						>
							Reset
						</button>
					</div>
				)}
			</div>

			<ConfirmModal
				open={confirmModalOpen}
				title="Reset semantic layer"
				message="This deletes the semantic layer and a rebuild will be triggered."
				confirmLabel="Reset"
				onConfirm={handleReset}
				onCancel={() => setConfirmModalOpen(false)}
			/>

			<Toast
				open={error !== null}
				message={error ?? ''}
				variant={ToastVariant.Error}
				onClose={() => setError(null)}
			/>
			<Toast
				open={message !== null}
				message={message ?? ''}
				variant={ToastVariant.Success}
				onClose={() => setMessage(null)}
			/>
		</div>
	);
};
