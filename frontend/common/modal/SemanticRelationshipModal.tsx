// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { Icon, IconName } from '@/common/icons';
import { Button, SelectButton } from '@/common/Button';
import { Size, ButtonTheme, SelectButtonTheme } from '@/enums/button';
import { TruncatedText } from '@/common/TruncatedText';
import { TextVariant } from '@/enums/text';
import { Modal } from './Modal';

/** Minimal Term reference — decoupled from any specific page's node/row shape. */
export type SemanticRelationshipModalTerm = {
	id: string;
	name: string;
};

type SemanticRelationshipModalProps = {
	sourceTerm: SemanticRelationshipModalTerm | null;
	targetTerm: SemanticRelationshipModalTerm | null;
	onClose: () => void;
	onView: (termId: string) => void;
};

/** Generic modal showing the two Terms joined by a semantic-graph edge. */
export const SemanticRelationshipModal = ({
	sourceTerm,
	targetTerm,
	onClose,
	onView,
}: SemanticRelationshipModalProps) => {
	const open = sourceTerm != null && targetTerm != null;

	return (
		<Modal open={open} onClose={onClose} className="w-full max-w-2xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Connection} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<TruncatedText as="h2" variant={TextVariant.Heading}>
						{sourceTerm?.name} (Term) &lt;&gt; {targetTerm?.name} (Term)
					</TruncatedText>
				</div>
				<Button
					theme={ButtonTheme.IconNeutral}
					size={Size.SMALL}
					iconOnly
					type="button"
					onClick={onClose}
					aria-label="Close term relationship"
				>
					<Icon name={IconName.Close} className="h-4 w-4" />
				</Button>
			</header>
			<div className="p-5">
				<div className="grid grid-cols-2 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
					{[sourceTerm, targetTerm].map((term, index) => (
						<div
							key={term?.id ?? index}
							className={
								index === 0 ? 'border-r border-zinc-200 dark:border-zinc-700' : ''
							}
						>
							<div className="border-b border-zinc-200 bg-zinc-50 px-4 py-2.5 dark:border-zinc-700 dark:bg-zinc-800/60">
								<TruncatedText text={term?.name} variant={TextVariant.Label} />
							</div>
							<SelectButton
								theme={SelectButtonTheme.ListItemLink}
								onClick={() => term != null && onView(term.id)}
							>
								<span className="flex min-w-0 items-center gap-1.5">
									<Icon
										name={IconName.Terms}
										className="h-3.5 w-3.5 shrink-0 text-[#76b900]"
									/>
									<TruncatedText text={term?.name} />
								</span>
								<Icon
									name={IconName.ExternalLink}
									className="h-4 w-4 shrink-0 text-zinc-400"
								/>
							</SelectButton>
						</div>
					))}
				</div>
			</div>
		</Modal>
	);
};
