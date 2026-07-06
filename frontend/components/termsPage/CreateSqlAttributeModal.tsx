// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useState } from 'react';
import { Icon, IconName } from '@/components/icons';
import { Modal } from '@/components/Modal';
import { SqlEditor } from '@/components/SqlBlock';

type FieldLabelProps = {
	icon: React.ReactNode;
	children: React.ReactNode;
};

const FieldLabel = ({ icon, children }: FieldLabelProps) => (
	<div className="mb-1.5 flex items-center gap-2 text-sm font-medium text-zinc-900 dark:text-zinc-100">
		{icon}
		{children}
	</div>
);

export type CreateSqlAttributeModalProps = {
	open: boolean;
	onClose: () => void;
	termId: string;
};

export const CreateSqlAttributeModal = ({
	open,
	onClose,
	termId,
}: CreateSqlAttributeModalProps) => {
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [sql, setSql] = useState('');
	const [validating, setValidating] = useState(false);
	const [validationMessage, setValidationMessage] = useState<string | null>(null);

	const trimmedName = name.trim();
	const trimmedSql = sql.trim();
	const canCreate = trimmedName.length > 0 && trimmedSql.length > 0;

	const resetForm = useCallback(() => {
		setName('');
		setDescription('');
		setSql('');
		setValidating(false);
		setValidationMessage(null);
	}, []);

	useEffect(() => {
		if (!open) {
			resetForm();
		}
	}, [open, resetForm]);

	const handleClose = () => {
		if (validating) return;
		onClose();
	};

	const handleValidateSql = async () => {
		if (trimmedSql.length === 0) return;
		setValidating(true);
		setValidationMessage(null);
		console.log('validate sql attribute', { termId, sql: trimmedSql });
		setValidationMessage('SQL validation is not wired yet.');
		setValidating(false);
	};

	const handleCreate = () => {
		if (!canCreate) return;
		console.log('create sql attribute', {
			termId,
			name: trimmedName,
			description: description.trim(),
			expression: trimmedSql,
		});
		onClose();
	};

	return (
		<Modal open={open} onClose={handleClose} className="w-[800px] max-w-full">
			<div className="flex items-center justify-between border-b border-zinc-200 px-6 py-4 dark:border-zinc-700">
				<div className="flex items-center gap-2">
					<Icon
						name={IconName.Terms}
						className="h-5 w-5 text-zinc-700 dark:text-zinc-300"
					/>
					<h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
						Create New Attribute
					</h3>
				</div>
				<button
					type="button"
					onClick={handleClose}
					className="cursor-pointer rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-700 dark:hover:text-zinc-300"
					aria-label="Close"
				>
					<svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
						<path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
					</svg>
				</button>
			</div>

			<div className="space-y-4 p-6">
				<div>
					<FieldLabel
						icon={
							<span className="flex h-5 w-5 items-center justify-center rounded border border-zinc-300 text-[10px] font-semibold text-zinc-600 dark:border-zinc-600 dark:text-zinc-300">
								A
							</span>
						}
					>
						Attribute Name
					</FieldLabel>
					<input
						type="text"
						value={name}
						onChange={(e) => setName(e.target.value)}
						placeholder="Attribute Name"
						className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300 dark:placeholder:text-zinc-500"
					/>
				</div>

				<div>
					<FieldLabel
						icon={
							<svg
								className="h-5 w-5 text-zinc-500 dark:text-zinc-400"
								viewBox="0 0 20 20"
								fill="currentColor"
								aria-hidden
							>
								<path d="M4 3.5A1.5 1.5 0 0 1 5.5 2h9A1.5 1.5 0 0 1 16 3.5v13a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 16.5v-13ZM5.5 4a.5.5 0 0 0-.5.5v11a.5.5 0 0 0 .5.5h9a.5.5 0 0 0 .5-.5v-11a.5.5 0 0 0-.5-.5h-9ZM7 7.25a.75.75 0 0 1 .75-.75h4.5a.75.75 0 0 1 0 1.5h-4.5A.75.75 0 0 1 7 7.25Zm0 3a.75.75 0 0 1 .75-.75h4.5a.75.75 0 0 1 0 1.5h-4.5A.75.75 0 0 1 7 10.25Zm0 3a.75.75 0 0 1 .75-.75h2.5a.75.75 0 0 1 0 1.5h-2.5a.75.75 0 0 1-.75-.75Z" />
							</svg>
						}
					>
						Description (Optional)
					</FieldLabel>
					<textarea
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						placeholder="Add Description"
						rows={3}
						className="w-full resize-y rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300 dark:placeholder:text-zinc-500"
					/>
				</div>

				<div>
					<FieldLabel
						icon={
							<span className="flex h-5 w-5 items-center justify-center rounded border border-zinc-300 text-[9px] font-semibold text-zinc-600 dark:border-zinc-600 dark:text-zinc-300">
								SQL
							</span>
						}
					>
						SQL Code
					</FieldLabel>
					<SqlEditor value={sql} onChange={setSql} label="SQL" rows={10} />
				</div>

				{validationMessage != null && (
					<p className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-300">
						{validationMessage}
					</p>
				)}

				<div className="flex justify-end gap-3 pt-2">
					<button
						type="button"
						onClick={() => {
							void handleValidateSql();
						}}
						disabled={trimmedSql.length === 0 || validating}
						className="cursor-pointer rounded-lg border border-zinc-300 bg-white px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-default disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
					>
						{validating ? 'Validating…' : 'Validate SQL'}
					</button>
					<button
						type="button"
						onClick={handleCreate}
						disabled={!canCreate}
						className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${canCreate ? 'cursor-pointer bg-[#76b900] text-white hover:bg-[#5e9400]' : 'cursor-default bg-zinc-200 text-zinc-500 dark:bg-zinc-700 dark:text-zinc-400'}`}
					>
						Create
					</button>
				</div>
			</div>
		</Modal>
	);
};
