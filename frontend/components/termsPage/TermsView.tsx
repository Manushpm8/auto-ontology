// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { Placeholders } from '@/assets/images/placeholders';
import { Icon, IconName } from '@/components/icons';
import { termsApi } from '@/api/terms';
import { sqlAttributesApi } from '@/api/sqlAttributes';
import { zonesApi } from '@/api/zones';
import { useSession } from '@/auth/auth-client';
import { ComposerSectionKind } from '@/enums/datasources';
import { SinglePageView, type SinglePageFormat } from '@/components/SinglePageView';
import { CreateSqlAttributeModal } from '@/components/termsPage/CreateSqlAttributeModal';
import type { ColumnAttribute, SqlAttribute, Term } from '@/types/terms';

type TermCardProps = {
	term: Term;
	attributes: ColumnAttribute[];
	sqlAttributes: SqlAttribute[];
	relatedCount: number;
	onClick: (term: Term) => void;
};

const TermCard = ({ term, attributes, sqlAttributes, relatedCount, onClick }: TermCardProps) => (
	<li
		role="button"
		tabIndex={0}
		onClick={() => onClick(term)}
		onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClick(term)}
		className="cursor-pointer rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm transition-shadow hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
	>
		{/* Card header */}
		<div className="flex items-start justify-between gap-3">
			<div>
				<h2 className="text-base font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
					{term.name}
				</h2>
				{term.synonyms.length > 0 && (
					<p className="mt-0.5 text-xs text-zinc-400 dark:text-zinc-500">
						{term.synonyms.join(', ')}
					</p>
				)}
			</div>
		</div>

		{term.description != null && term.description.trim() !== '' ? (
			<p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{term.description}</p>
		) : (
			<p className="mt-2 text-sm italic text-zinc-400 dark:text-zinc-500">
				No Description Available
			</p>
		)}

		{/* Three-column section */}
		<div className="mt-4 grid grid-cols-3 gap-0 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-700">
			{/* Column Attributes */}
			<div className="border-r border-zinc-200 dark:border-zinc-700">
				<div className="border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-700">
					<span className="text-xs font-semibold text-zinc-500 dark:text-zinc-400">
						Column Attributes
					</span>
				</div>
				<div className="flex items-center">
					<span className="flex w-full items-center justify-between px-4 py-3 text-sm text-zinc-700 dark:text-zinc-300">
						<span>Column Attributes</span>
						<span className="ml-1.5 font-medium text-zinc-900 dark:text-zinc-100">
							{attributes.length}
						</span>
					</span>
				</div>
			</div>

			{/* SQL Attributes */}
			<div className="border-r border-zinc-200 dark:border-zinc-700">
				<div className="border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-700">
					<span className="text-xs font-semibold text-zinc-500 dark:text-zinc-400">
						SQL Attributes
					</span>
				</div>
				<div className="flex items-center">
					<span className="flex w-full items-center justify-between px-4 py-3 text-sm text-zinc-700 dark:text-zinc-300">
						<span>SQL Attributes</span>
						<span className="ml-1.5 font-medium text-zinc-900 dark:text-zinc-100">
							{sqlAttributes.length}
						</span>
					</span>
				</div>
			</div>

			{/* Related Terms */}
			<div>
				<div className="border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-700">
					<span className="text-xs font-semibold text-zinc-500 dark:text-zinc-400">
						Related Terms
					</span>
				</div>
				<div className="flex items-center justify-between px-4 py-3 text-sm text-zinc-700 dark:text-zinc-300">
					<span>Related Terms</span>
					<span className="font-medium text-zinc-900 dark:text-zinc-100">
						{relatedCount}
					</span>
				</div>
			</div>
		</div>
	</li>
);

export const TermsView = () => {
	const router = useRouter();
	const searchParams = useSearchParams();
	const focusId = searchParams.get('focus');
	const sqlAttrId = searchParams.get('sqlAttr');

	const { data: session } = useSession();
	const sessionUserId = session?.user?.id ?? null;
	const sessionRole = session?.user?.role ?? null;

	const [terms, setTerms] = useState<Term[]>([]);
	const [attrs, setAttrs] = useState<ColumnAttribute[]>([]);
	const [sqlAttrs, setSqlAttrs] = useState<SqlAttribute[]>([]);
	const [relatedCountsMap, setRelatedCountsMap] = useState<Map<string, number>>(new Map());
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [createSqlAttrModalOpen, setCreateSqlAttrModalOpen] = useState(false);

	useEffect(() => {
		let cancelled = false;

		(async () => {
			setLoading(true);
			const [termsRes, attrsRes, sqlAttrsRes, countsRes] = await Promise.all([
				termsApi.list(),
				termsApi.listColumnAttributes(),
				termsApi.listSqlAttributes(),
				termsApi.listRelatedCounts(),
			]);
			if (cancelled) return;

			if (termsRes.error) {
				setError(termsRes.message ?? 'Failed to load terms');
				setTerms([]);
			} else {
				setError(null);
				setTerms(termsRes.data ?? []);
			}

			if (!attrsRes.error) {
				setAttrs(attrsRes.data ?? []);
			}

			if (!sqlAttrsRes.error) {
				setSqlAttrs(sqlAttrsRes.data ?? []);
			}

			if (!countsRes.error) {
				const map = new Map<string, number>();
				for (const { term_id, count } of countsRes.data ?? []) {
					map.set(term_id, count);
				}
				setRelatedCountsMap(map);
			}

			setLoading(false);
		})();

		return () => {
			cancelled = true;
		};
	}, []);

	const attrsByTerm = useMemo(() => {
		const map = new Map<string, ColumnAttribute[]>();
		for (const attr of attrs) {
			const list = map.get(attr.term_name) ?? [];
			list.push(attr);
			map.set(attr.term_name, list);
		}
		return map;
	}, [attrs]);

	const sqlAttrsByTerm = useMemo(() => {
		const map = new Map<string, SqlAttribute[]>();
		for (const attr of sqlAttrs) {
			if (attr.term_name == null) continue;
			const list = map.get(attr.term_name) ?? [];
			list.push(attr);
			map.set(attr.term_name, list);
		}
		return map;
	}, [sqlAttrs]);

	const handleCardClick = useCallback(
		(term: Term) => {
			router.push(`/terms?focus=${encodeURIComponent(term.id)}`);
		},
		[router],
	);

	const handleBack = useCallback(() => {
		router.push('/terms');
	}, [router]);

	const handleBackToTerm = useCallback(() => {
		if (focusId == null) {
			handleBack();
			return;
		}
		router.push(`/terms?focus=${encodeURIComponent(focusId)}`);
	}, [focusId, handleBack, router]);

	const handleSqlAttrClick = useCallback(
		(sectionId: string, rowId: string) => {
			if (sectionId !== 'sql_attributes' || focusId == null) return;
			router.push(
				`/terms?focus=${encodeURIComponent(focusId)}&sqlAttr=${encodeURIComponent(rowId)}`,
			);
		},
		[focusId, router],
	);

	const getSqlAttributeSinglePage = useCallback(
		async (attrId: string): Promise<SinglePageFormat> => {
			const isViewer = sessionRole !== null && sessionRole !== 'admin';
			const [res, userZonesRes] = await Promise.all([
				sqlAttributesApi.get(attrId),
				isViewer && sessionUserId ? zonesApi.getAll(sessionUserId) : Promise.resolve(null),
			]);
			if (res.error || !res.data) {
				return {
					sections: [],
					header: { header: { title: 'SQL Attribute not found', withBorder: true } },
				};
			}
			const attr = res.data;

			// null = admin (no zone restriction), string[] = viewer's accessible zone IDs
			const userZoneIds: string[] | null =
				userZonesRes !== null && !userZonesRes.error
					? (userZonesRes.data ?? []).map((z) => z.id)
					: null;

			return {
				header: { header: { title: attr.name, withBorder: true } },
				sections: [
					{
						type: ComposerSectionKind.TEXT_CARD,
						id: 'description',
						title: 'Description',
						body: attr.description ?? '',
					},
					{
						type: ComposerSectionKind.SQL_BLOCK,
						id: 'sql',
						title: 'SQL',
						sql: attr.expression ?? '',
					},
					{
						type: ComposerSectionKind.ZONES_CHIPS,
						id: 'zones',
						title: 'Zones',
						zones: (attr.zones ?? []).map((z) => ({
							id: z.id,
							name: z.name,
							color: z.color,
						})),
						userZoneIds,
					},
				],
			};
		},
		[sessionUserId, sessionRole],
	);

	const getSinglePage = useCallback(
		async (termId: string): Promise<SinglePageFormat> => {
			const isViewer = sessionRole !== null && sessionRole !== 'admin';
			const [res, attrsRes, sqlAttrsRes, relatedRes, userZonesRes] = await Promise.all([
				termsApi.get(termId),
				termsApi.getColumnAttributes(termId),
				termsApi.getSqlAttributes(termId),
				termsApi.getRelatedTerms(termId),
				isViewer && sessionUserId ? zonesApi.getAll(sessionUserId) : Promise.resolve(null),
			]);
			if (res.error || !res.data) {
				return {
					sections: [],
					header: { header: { title: 'Term not found', withBorder: true } },
				};
			}
			const term = res.data;
			const termAttrs = attrsRes?.data ?? [];
			const termSqlAttrs = sqlAttrsRes?.data ?? [];
			const relatedTerms = relatedRes?.data ?? [];

			// null = admin (no zone restriction), string[] = viewer's accessible zone IDs
			const userZoneIds: string[] | null =
				userZonesRes !== null && !userZonesRes.error
					? (userZonesRes.data ?? []).map((z) => z.id)
					: null;

			return {
				header: {
					header: {
						title: term.name,
						withBorder: true,
					},
				},
				sections: [
					{
						type: ComposerSectionKind.TEXT_CARD,
						id: 'description',
						title: 'Description',
						body: term.description ?? '',
					},
					{
						type: ComposerSectionKind.TEXT_CARD,
						id: 'entities',
						title: 'Entities',
						body: `Tables (${term.table_count})`,
					},
					{
						type: ComposerSectionKind.ZONES_CHIPS,
						id: 'zones',
						title: 'Zones',
						zones: term.zones.map((z) => ({
							id: z.id,
							name: z.name,
							color: z.color,
						})),
						userZoneIds,
					},
					{
						type: ComposerSectionKind.RELATED_TERMS_CHIPS,
						id: 'related_terms',
						title: 'Related Terms',
						terms: relatedTerms.map((t) => ({
							id: t.id,
							name: t.name,
							description: t.description,
						})),
					},
					{
						type: ComposerSectionKind.DATA_TABLE,
						id: 'column_attributes',
						title: 'Column Attributes',
						columns: [{ key: 'name', label: 'Attribute Name' }],
						rows: termAttrs.map((attr) => ({
							name: attr.name,
						})),
					},
					{
						type: ComposerSectionKind.DATA_TABLE,
						id: 'sql_attributes',
						title: 'SQL Attributes',
						rowIdKey: 'id',
						columns: [{ key: 'name', label: 'Attribute Name' }],
						rows: termSqlAttrs.map((attr) => ({
							id: attr.id,
							name: attr.name,
						})),
					},
				],
			};
		},
		[sessionUserId, sessionRole],
	);

	const focusedTerm = focusId != null ? (terms.find((t) => t.id === focusId) ?? null) : null;
	const focusedSqlAttr =
		sqlAttrId != null
			? (sqlAttrs.find((attr) => attr.id === sqlAttrId) ??
				(focusedTerm != null
					? (sqlAttrsByTerm
							.get(focusedTerm.name)
							?.find((attr) => attr.id === sqlAttrId) ?? null)
					: null))
			: null;

	if (focusId != null && sqlAttrId != null) {
		const termTitle = focusedTerm?.name ?? focusId;
		const sqlAttrTitle = focusedSqlAttr?.name ?? sqlAttrId;
		return (
			<div className="flex h-full w-full flex-col bg-white dark:bg-zinc-950">
				<header className="flex items-center gap-3 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
					<button
						type="button"
						onClick={handleBack}
						className="flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
						aria-label="Back to terms list"
					>
						<Icon name={IconName.Terms} className="h-4 w-4" />
						Terms
					</button>
					<span className="text-zinc-300 dark:text-zinc-600">/</span>
					<button
						type="button"
						onClick={handleBackToTerm}
						className="cursor-pointer rounded-lg px-1.5 py-1 text-sm font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
					>
						{termTitle}
					</button>
					<span className="text-zinc-300 dark:text-zinc-600">/</span>
					<span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
						{sqlAttrTitle}
					</span>
				</header>
				<main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
					<SinglePageView
						dataId={sqlAttrId}
						title={sqlAttrTitle}
						getSinglePage={getSqlAttributeSinglePage}
					/>
				</main>
			</div>
		);
	}

	if (focusId != null) {
		const termTitle = focusedTerm?.name ?? focusId;
		return (
			<div className="flex h-full w-full flex-col bg-white dark:bg-zinc-950">
				<header className="flex items-center gap-3 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
					<button
						type="button"
						onClick={handleBack}
						className="flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
						aria-label="Back to terms list"
					>
						<Icon name={IconName.Terms} className="h-4 w-4" />
						Terms
					</button>
					<span className="text-zinc-300 dark:text-zinc-600">/</span>
					<span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
						{termTitle}
					</span>
					<button
						type="button"
						onClick={() => setCreateSqlAttrModalOpen(true)}
						className="ml-auto flex cursor-pointer items-center gap-2 rounded-lg bg-[#76b900] px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-[#5e9400]"
					>
						Create new sql attribute
					</button>
				</header>
				<main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
					<SinglePageView
						dataId={focusId}
						title={termTitle}
						getSinglePage={getSinglePage}
						onDataTableRowClick={handleSqlAttrClick}
					/>
				</main>
				<CreateSqlAttributeModal
					open={createSqlAttrModalOpen}
					onClose={() => setCreateSqlAttrModalOpen(false)}
					termId={focusId}
				/>
			</div>
		);
	}

	return (
		<div className="flex h-full w-full flex-col bg-white dark:bg-zinc-950">
			<header className="flex items-center gap-3 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
				<Icon name={IconName.Terms} className="h-5 w-5 text-[#76b900]" />
				<h1 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
					Terms
				</h1>
			</header>

			<div className="flex-1 overflow-y-auto px-6 py-6">
				{loading && (
					<div className="flex h-full items-center justify-center">
						<div
							className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900] dark:border-zinc-700"
							role="status"
							aria-label="Loading terms"
						/>
					</div>
				)}

				{!loading && error != null && (
					<div className="mx-auto max-w-lg rounded-2xl border border-red-200/80 bg-white/90 px-8 py-10 text-center shadow-xl shadow-red-100/50 dark:border-red-900/50 dark:bg-zinc-950/80 dark:shadow-none">
						<h2 className="text-lg font-semibold tracking-tight text-red-800 dark:text-red-300">
							Couldn&apos;t load terms
						</h2>
						<pre className="mt-4 max-w-full overflow-x-auto rounded-lg border border-red-100 bg-red-50/80 p-3 text-left text-xs text-red-900/80 dark:border-red-900/40 dark:bg-red-950/40 dark:text-red-200">
							{error}
						</pre>
					</div>
				)}

				{!loading && error == null && terms.length === 0 && (
					<div className="flex h-full min-h-[40dvh] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-zinc-300/80 bg-white/60 p-12 text-center dark:border-zinc-600 dark:bg-zinc-950/40">
						<Placeholders.NoTerms />
						<p className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
							No Terms Created Yet
						</p>
					</div>
				)}

				{!loading && error == null && terms.length > 0 && (
					<ul className="flex flex-col gap-4">
						{terms.map((term) => (
							<TermCard
								key={term.id}
								term={term}
								attributes={attrsByTerm.get(term.name) ?? []}
								sqlAttributes={sqlAttrsByTerm.get(term.name) ?? []}
								relatedCount={relatedCountsMap.get(term.id) ?? 0}
								onClick={handleCardClick}
							/>
						))}
					</ul>
				)}
			</div>
		</div>
	);
};
