// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import { Spinner } from '@nvidia/foundations-react-core';

import { termsApi } from '@/api/terms';
import { Icon, IconName } from '@/common/icons';
import { Button } from '@/common/Button';
import { EmptyState } from '@/common/EmptyState';
import { Size, ButtonTheme } from '@/enums/button';
import { EmptyStateVariant } from '@/enums/emptyState';
import type { SqlAttribute } from '@/types/terms';
import { SqlBlock } from '@/common/SqlBlock';
import { Text } from '@/common/Text';
import { TextVariant } from '@/enums/text';
import { Modal } from './Modal';

/** Minimal Term reference — decoupled from any specific page's node/row shape. */
export type SqlAttributesModalTerm = {
	id: string;
	name: string;
};

type SqlAttributesModalProps = {
	term: SqlAttributesModalTerm | null;
	onClose: () => void;
};

// The caller constrains the width; `Text` clips to one line and
// reveals the full text in a popover on hover.
const TruncatedDescription = ({ text }: { text: string | null }) => {
	const value = text?.trim() ?? '';

	if (value === '') {
		return <span className="italic text-zinc-400 dark:text-zinc-500">No Description</span>;
	}

	return <Text text={value} />;
};

/** Generic modal listing a Term's SQL Attributes. Reusable from any page that has a term id. */
export const SqlAttributesModal = ({ term, onClose }: SqlAttributesModalProps) => {
	const [attributes, setAttributes] = useState<SqlAttribute[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (term == null) return undefined;
		let cancelled = false;

		const load = async () => {
			setLoading(true);
			setError(null);
			const response = await termsApi.getSqlAttributes(term.id);
			if (cancelled) return;
			if (response.error) {
				setError(response.message ?? 'Failed to load SQL attributes');
			} else {
				setAttributes(response.data ?? []);
			}
			setLoading(false);
		};

		void load();
		return () => {
			cancelled = true;
		};
	}, [term]);

	return (
		<Modal open={term != null} onClose={onClose} className="w-full max-w-2xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Link} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<Text as="h2" variant={TextVariant.Heading}>
						{term?.name} — SQL Attributes ({attributes.length})
					</Text>
				</div>
				<Button
					theme={ButtonTheme.IconNeutral}
					size={Size.SMALL}
					iconOnly
					type="button"
					onClick={onClose}
					aria-label="Close SQL attributes"
				>
					<Icon name={IconName.Close} className="h-4 w-4" />
				</Button>
			</header>
			<div className="max-h-[70dvh] overflow-y-auto p-5">
				{loading ? (
					<div className="flex h-32 items-center justify-center">
						<Spinner aria-label="Loading SQL attributes" className="h-8 w-8" />
					</div>
				) : error != null ? (
					<p className="text-sm text-red-600 dark:text-red-300">{error}</p>
				) : attributes.length === 0 ? (
					<EmptyState variant={EmptyStateVariant.Inline} title="No SQL attributes" />
				) : (
					<ul className="space-y-4">
						{attributes.map((attr) => (
							<li
								key={attr.id}
								className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700"
							>
								<Text as="h3" text={attr.name} variant={TextVariant.Heading} />
								<div className="mt-1 max-w-md text-xs text-zinc-500 dark:text-zinc-400">
									<TruncatedDescription text={attr.description} />
								</div>
								<SqlBlock
									className="mt-2"
									sql={attr.expression || attr.sql || ''}
									label="SQL"
								/>
							</li>
						))}
					</ul>
				)}
			</div>
		</Modal>
	);
};
