// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';

import { tagsApi } from '@/api/tags';
import { Button } from '@/common/Button';
import { formatDate } from '@/common/date';
import { EmptyState } from '@/common/EmptyState';
import { Icon, IconName } from '@/common/icons';
import { ConfirmModal, ModalCreateNewItem } from '@/common/modal';
import { PopoverMenu } from '@/common/PopoverMenu';
import { SkeletonRows } from '@/common/Skeleton';
import { ButtonTheme, Size } from '@/enums/button';
import { EmptyStateVariant } from '@/enums/emptyState';
import type { Tag } from '@/types/tags';

/** Mirrors `MAX_TAG_NAME_LENGTH` in `gsf/server/tags/router.py`, which rejects longer. */
const MAX_TAG_NAME_LENGTH = 25;

const byName = (left: Tag, right: Tag): number =>
	left.name.toLowerCase().localeCompare(right.name.toLowerCase());

/**
 * Both timestamps are serialised from one row by one serialiser, so an
 * untouched tag has them byte-identical and no date parsing is needed to tell.
 * Rendering the creation date twice would read as an edit that never happened.
 */
const modifiedLabel = (tag: Tag): string =>
	tag.modified === tag.created ? 'Never' : formatDate(tag.modified);

export default function TagsSettingsPage() {
	const [tags, setTags] = useState<Tag[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const [modalOpen, setModalOpen] = useState(false);
	const [name, setName] = useState('');
	const [submitting, setSubmitting] = useState(false);
	const [submitError, setSubmitError] = useState<string | null>(null);

	const [confirmDeleteTag, setConfirmDeleteTag] = useState<Tag | null>(null);
	const [deleting, setDeleting] = useState(false);
	const [deleteError, setDeleteError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		tagsApi.getAll().then((response) => {
			if (cancelled) return;
			if (response.error) {
				setError(response.message ?? 'Failed to load tags.');
				setTags([]);
			} else {
				setError(null);
				setTags([...(response.data ?? [])].sort(byName));
			}
			setLoading(false);
		});
		return () => {
			cancelled = true;
		};
	}, []);

	const trimmedName = name.trim();
	// The backend owns this rule and answers 409; checking here too is what puts
	// the message under the field while typing instead of after a round trip.
	const nameTaken = tags.some(
		(tag) => tag.name.trim().toLowerCase() === trimmedName.toLowerCase(),
	);
	const canSubmit = !submitting && trimmedName.length > 0 && !nameTaken;

	const openCreateModal = () => {
		setName('');
		setSubmitError(null);
		setModalOpen(true);
	};

	const closeCreateModal = () => {
		if (submitting) return;
		setModalOpen(false);
		setSubmitError(null);
	};

	const handleSubmit = async () => {
		if (!canSubmit) return;
		setSubmitting(true);
		setSubmitError(null);

		const response = await tagsApi.create({ name: trimmedName });
		setSubmitting(false);

		if (response.error) {
			setSubmitError(response.message ?? 'Failed to create tag.');
			return;
		}

		const created = response.data;
		if (created != null) {
			setTags((prev) =>
				[...prev.filter((tag) => tag.id !== created.id), created].sort(byName),
			);
		}
		setModalOpen(false);
	};

	const handleRequestDelete = (tag: Tag) => {
		setDeleteError(null);
		setConfirmDeleteTag(tag);
	};

	const handleCancelDelete = () => {
		if (deleting) return;
		setConfirmDeleteTag(null);
		setDeleteError(null);
	};

	const handleConfirmDelete = async () => {
		if (confirmDeleteTag == null) return;
		setDeleting(true);
		setDeleteError(null);

		const response = await tagsApi.delete(confirmDeleteTag.id);
		setDeleting(false);

		if (response.error) {
			setDeleteError(response.message ?? 'Failed to delete tag.');
			return;
		}

		setTags((prev) => prev.filter((tag) => tag.id !== confirmDeleteTag.id));
		setConfirmDeleteTag(null);
	};

	return (
		<main className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-[linear-gradient(180deg,rgba(255,255,255,1)_0%,rgba(250,250,250,0.6)_100%)] px-7 py-6 sm:px-10 sm:py-7 dark:bg-[linear-gradient(180deg,rgba(9,9,11,1)_0%,rgba(24,24,27,0.5)_100%)]">
			<div className="w-full space-y-5">
				<div className="flex items-center gap-3">
					<h1 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
						Tags
					</h1>
					{tags.length > 0 ? (
						<div className="ml-auto">
							<Button
								theme={ButtonTheme.Primary}
								size={Size.REGULAR}
								type="button"
								onClick={openCreateModal}
								iconPosition="left"
								shadow
							>
								<Icon name={IconName.Plus} className="h-4 w-4" />
								Create New Tag
							</Button>
						</div>
					) : null}
				</div>

				{error ? (
					<div className="rounded-lg border border-red-200/90 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200">
						{error}
					</div>
				) : null}

				{loading ? (
					<div className="py-2" role="status" aria-label="Loading tags">
						<SkeletonRows rows={4} />
					</div>
				) : tags.length > 0 ? (
					/* No `overflow-hidden` here, deliberately: it would clip the
					   absolutely positioned action menu of every row. */
					<div className="rounded-lg border border-zinc-200/90 bg-white/90 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-700/90 dark:bg-zinc-950/50 dark:ring-white/[0.06]">
						<div className="flex items-center gap-3 border-b border-zinc-200/90 px-4 py-2 text-xs font-semibold tracking-wide text-zinc-500 uppercase dark:border-zinc-700/90 dark:text-zinc-400">
							<span className="min-w-0 flex-1">Name</span>
							<span className="w-28 shrink-0 text-right">Created</span>
							<span className="w-28 shrink-0 text-right">Modified</span>
							{/* The action button's exact footprint (`Size.SMALL`,
							    `iconOnly`), so the date columns line up with these
							    headers and nothing pads the right edge. */}
							<span className="w-[26px] shrink-0" aria-hidden="true" />
						</div>
						<ul className="divide-y divide-zinc-200/90 dark:divide-zinc-700/90">
							{tags.map((tag) => (
								<li key={tag.id} className="flex items-center gap-3 px-4 py-3">
									<Icon
										name={IconName.Tag}
										className="h-4 w-4 shrink-0 text-zinc-400 dark:text-zinc-500"
									/>
									<span className="min-w-0 flex-1 truncate text-sm text-zinc-800 dark:text-zinc-200">
										{tag.name}
									</span>
									<span className="w-28 shrink-0 text-right text-xs text-zinc-500 dark:text-zinc-400">
										{formatDate(tag.created)}
									</span>
									<span className="w-28 shrink-0 text-right text-xs text-zinc-500 dark:text-zinc-400">
										{modifiedLabel(tag)}
									</span>
									<div className="relative w-[26px] shrink-0">
										<PopoverMenu
											items={[
												{
													label: 'Delete Tag',
													icon: (
														<Icon
															name={IconName.Trash}
															className="h-3.5 w-3.5"
														/>
													),
													onClick: () => handleRequestDelete(tag),
													danger: true,
												},
											]}
											trigger={({ toggle }) => (
												<Button
													theme={ButtonTheme.IconNeutral}
													size={Size.SMALL}
													iconOnly
													type="button"
													onClick={toggle}
													aria-label={`Actions for ${tag.name}`}
												>
													<Icon
														name={IconName.DotsVertical}
														className="h-4 w-4"
													/>
												</Button>
											)}
										/>
									</div>
								</li>
							))}
						</ul>
					</div>
				) : (
					<EmptyState
						variant={EmptyStateVariant.Inline}
						icon={IconName.Tag}
						title="No tags yet"
						description="Tags label catalog items so they can be found and governed together."
						action={{
							label: 'Create New Tag',
							icon: IconName.Plus,
							onClick: openCreateModal,
						}}
						className="rounded-lg border border-dashed border-zinc-300/90 bg-white/70 dark:border-zinc-600 dark:bg-zinc-900/30"
					/>
				)}
			</div>

			<ModalCreateNewItem
				open={modalOpen}
				onClose={closeCreateModal}
				title="Create New Tag"
				submitLabel="Create"
				canSubmit={canSubmit}
				onSubmit={handleSubmit}
				className="w-[520px] max-w-full"
			>
				<div>
					<label
						htmlFor="tag-name"
						className="mb-1.5 flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100"
					>
						<Icon
							name={IconName.Tag}
							className="h-4 w-4 text-zinc-500 dark:text-zinc-400"
						/>
						Tag Name
						<span className="font-normal text-zinc-400 dark:text-zinc-500">
							(Max. {MAX_TAG_NAME_LENGTH})
						</span>
					</label>
					<input
						id="tag-name"
						type="text"
						value={name}
						maxLength={MAX_TAG_NAME_LENGTH}
						onChange={(e) => setName(e.target.value)}
						placeholder="Type tag name"
						className={`w-full rounded-lg border bg-white px-3 py-2 text-sm text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 dark:bg-zinc-900 dark:text-zinc-300 dark:placeholder:text-zinc-500 ${nameTaken ? 'border-red-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/30 dark:border-red-500 dark:focus:border-red-400 dark:focus:ring-red-400/30' : 'border-zinc-300 focus:border-[#76b900] focus:ring-2 focus:ring-[#76b900]/30 dark:border-zinc-600'}`}
					/>
					{nameTaken ? (
						<p className="mt-1 text-xs text-red-500 dark:text-red-400">
							A tag with this name already exists
						</p>
					) : null}
				</div>

				{submitError ? (
					<div className="rounded-lg border border-red-200/90 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200">
						{submitError}
					</div>
				) : null}

				{/* Footer rule. Negative margins cancel the modal body's padding so it
				    spans the full width, as the dialog's header rule does. */}
				<div className="-mx-6 border-t border-zinc-200 dark:border-zinc-700" />
			</ModalCreateNewItem>

			<ConfirmModal
				open={confirmDeleteTag !== null}
				title="Delete tag"
				message={
					confirmDeleteTag == null
						? ''
						: `Are you sure you want to delete "${confirmDeleteTag.name}"? This action cannot be undone.`
				}
				onConfirm={handleConfirmDelete}
				onCancel={handleCancelDelete}
				confirming={deleting}
				error={deleteError}
			/>
		</main>
	);
}
