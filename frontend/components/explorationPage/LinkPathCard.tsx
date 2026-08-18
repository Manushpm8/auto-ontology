// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useState } from 'react';

import { Button } from '@/common/Button';
import { Size, ButtonTheme } from '@/enums/button';
import { EmptyState } from '@/common/EmptyState';
import { EmptyStateVariant } from '@/enums/emptyState';
import { Icon, IconName } from '@/common/icons';
import { LabelList } from '@/common/SinglePageComposer';
import { SqlBlock } from '@/common/SqlBlock';
import { Text } from '@/common/Text';
import { TextVariant } from '@/enums/text';
import type { ExplorationForeignKey, ExplorationLink } from '@/types/exploration';

const ForeignKeyColumnsPanel = ({
	foreignKeys,
	sourceName,
	targetName,
}: {
	foreignKeys: ExplorationForeignKey[];
	sourceName: string;
	targetName: string;
}) => {
	if (foreignKeys.length === 0) {
		return (
			<EmptyState
				variant={EmptyStateVariant.Inline}
				icon={IconName.Link}
				title="No stored SQL query"
				description="These tables share a foreign key relationship, but no stored query references both of them together."
			/>
		);
	}

	return (
		<div className="space-y-3">
			<p className="text-xs text-zinc-500 dark:text-zinc-400">
				No stored SQL query references both tables together. They are joined by the
				following foreign key column{foreignKeys.length > 1 ? 's' : ''}:
			</p>
			{foreignKeys.map((fk, index) => (
				<div
					key={`${fk.sourceColumn}-${fk.targetColumn}-${index}`}
					className="grid grid-cols-1 gap-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-700"
				>
					<div className="min-w-0">
						<Text as="p" variant={TextVariant.Overline}>
							{sourceName}.{fk.sourceColumn}
						</Text>
						<div className="mt-1.5">
							<LabelList
								values={
									Array.isArray(fk.sourceSampleValues)
										? fk.sourceSampleValues
										: []
								}
							/>
						</div>
					</div>
					<div className="min-w-0">
						<Text as="p" variant={TextVariant.Overline}>
							{targetName}.{fk.targetColumn}
						</Text>
						<div className="mt-1.5">
							<LabelList
								values={
									Array.isArray(fk.targetSampleValues)
										? fk.targetSampleValues
										: []
								}
							/>
						</div>
					</div>
				</div>
			))}
		</div>
	);
};

type LinkPathCardProps = {
	sourceName: string;
	targetName: string;
	link: ExplorationLink;
	onClose: () => void;
};

/**
 * Corner card showing what's really behind a clicked Data-layer (Table↔Table)
 * graph edge — replaces the old `QueryCarouselModal`. Shows the same SQL
 * query (or FK column pair) carousel those tables' `queries`/`foreignKeys`
 * always had. Styled like `ActiveExpansionCard`/`ActiveDataCard` (non-modal,
 * docked to the same top-right corner) rather than as a dialog, since the
 * graph itself — now with the matching path highlighted — stays the real
 * focus. A clicked Semantic-layer (term↔term) edge instead shows a
 * `connection`-kind `ActiveExpansionCard` — see
 * `activeSemanticConnectionEntity` in `ExplorationView.tsx` — rather than
 * this component's own now-removed `semantic` variant, for one consistent
 * card shape across every kind of Exploration side panel.
 */
export const LinkPathCard = ({ sourceName, targetName, link, onClose }: LinkPathCardProps) => {
	const [queryIndex, setQueryIndex] = useState(0);

	const { queries } = link;
	const query = queries[queryIndex] ?? '';
	const viaForeignKey = link.viaForeignKey ?? false;

	return (
		<section className="absolute right-4 top-20 bottom-28 z-20 flex w-[380px] flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
			<header className="flex shrink-0 items-start justify-between gap-2 border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Connection} className="h-4 w-4 shrink-0 text-[#3b82b6]" />
					<div className="min-w-0">
						<Text
							as="p"
							text={`${sourceName} ↔ ${targetName}`}
							variant={TextVariant.Label}
						/>
						{viaForeignKey && (
							<span className="mt-0.5 inline-block rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
								Foreign key
							</span>
						)}
					</div>
				</div>
				<Button
					theme={ButtonTheme.IconNeutral}
					size={Size.SMALL}
					iconOnly
					type="button"
					onClick={onClose}
					aria-label="Close connection details"
				>
					<Icon name={IconName.Close} className="h-4 w-4" />
				</Button>
			</header>
			<div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
				{queries.length > 0 ? (
					<div className="flex flex-col gap-2">
						{queries.length > 1 && (
							<div className="flex items-center justify-between">
								<Text as="p" variant={TextVariant.Caption}>
									SQL Query
								</Text>
								<div className="flex items-center gap-2">
									<Button
										theme={ButtonTheme.IconNeutral}
										size={Size.SMALL}
										iconOnly
										type="button"
										onClick={() =>
											setQueryIndex((index) => Math.max(0, index - 1))
										}
										disabled={queryIndex === 0}
										aria-label="Previous query"
									>
										<Icon
											name={IconName.ChevronRight}
											className="h-4 w-4 rotate-180"
										/>
									</Button>
									<span className="text-xs text-zinc-500">
										{queryIndex + 1}/{queries.length}
									</span>
									<Button
										theme={ButtonTheme.IconNeutral}
										size={Size.SMALL}
										iconOnly
										type="button"
										onClick={() =>
											setQueryIndex((index) =>
												Math.min(queries.length - 1, index + 1),
											)
										}
										disabled={queryIndex === queries.length - 1}
										aria-label="Next query"
									>
										<Icon name={IconName.ChevronRight} className="h-4 w-4" />
									</Button>
								</div>
							</div>
						)}
						<SqlBlock sql={query} />
					</div>
				) : (
					<ForeignKeyColumnsPanel
						foreignKeys={link.foreignKeys ?? []}
						sourceName={sourceName}
						targetName={targetName}
					/>
				)}
			</div>
		</section>
	);
};
