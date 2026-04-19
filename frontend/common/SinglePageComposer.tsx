'use client';

import { forwardRef, useEffect, useRef, useState, type ReactNode } from 'react';
import { Spinner } from '@nvidia/foundations-react-core';
import type { Breadcrumb } from '@/types/breadcrumbs';
import { ComposerSectionKind } from '@/enums/datasources';
import { isComposerSection, type ComposerSection } from '@/types/composer-section';

export type SinglePageComposerProps = {
	sections: unknown[];
	header?: {
		header?: Record<string, unknown>;
		errorBanner?: unknown;
	};
	leftPanel?: { bulks: unknown[]; width: string; slot?: ReactNode };
	rightPanel?: { bulks: unknown[]; width: string; slot?: ReactNode };
	pdfProps?: {
		isPDFView: boolean;
		headerProps: Record<string, unknown>;
	};
	entityUpdatingProperties?: Record<string, string | string[]>;
	isEditingDescription?: boolean;
	onSaveDescription?: (description: string) => void;
	onCancelEdit?: () => void;
};

type EditHeaderProps = {
	editMode?: boolean;
	onBeginEdit?: () => void;
	onFinishEdit?: (approve: boolean) => void;
	isLoadingUpdate?: boolean;
};

function composerSectionHeading(section: ComposerSection): string {
	switch (section.type) {
		case ComposerSectionKind.LOADING_PANEL:
			return section.message;
		default:
			return section.title;
	}
}

const EditableDescriptionCard = ({
	section,
	onSave,
	onCancel,
}: {
	section: { title: string; body: string };
	onSave?: (description: string) => void;
	onCancel?: () => void;
}) => {
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const [value, setValue] = useState(section.body);

	useEffect(() => {
		textareaRef.current?.focus();
	}, []);

	return (
		<div
			id="description-section"
			className="rounded-lg border-2 border-emerald-500 bg-white/90 p-5 shadow-sm ring-1 ring-emerald-500/20 dark:border-emerald-400 dark:bg-zinc-950/50 dark:ring-emerald-400/20"
		>
			<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
				{section.title}
			</h2>
			<textarea
				ref={textareaRef}
				value={value}
				onChange={(e) => setValue(e.target.value)}
				rows={4}
				className="mt-3 w-full resize-y rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm leading-relaxed text-zinc-700 outline-none transition-colors focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/30 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-300 dark:focus:border-emerald-400 dark:focus:ring-emerald-400/30"
			/>
			<div className="mt-3 flex items-center gap-2">
				<button
					type="button"
					className="inline-flex items-center rounded-lg border border-emerald-600 bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-emerald-700 hover:border-emerald-700 active:scale-[0.97] dark:bg-emerald-500 dark:border-emerald-500 dark:hover:bg-emerald-600 dark:hover:border-emerald-600"
					onClick={() => onSave?.(value)}
				>
					Save
				</button>
				<button
					type="button"
					className="inline-flex items-center rounded-lg border border-zinc-300 bg-white px-4 py-1.5 text-sm font-medium text-zinc-700 shadow-sm transition-all hover:bg-zinc-50 active:scale-[0.97] dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
					onClick={onCancel}
				>
					Cancel
				</button>
			</div>
		</div>
	);
};

function renderComposerSection(section: ComposerSection): ReactNode {
	switch (section.type) {
		case ComposerSectionKind.TEXT_CARD:
			return (
				<div
					id={section.id === 'description' ? 'description-section' : undefined}
					className="rounded-lg border border-zinc-200/90 bg-white/90 p-5 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-700/90 dark:bg-zinc-950/50 dark:ring-white/[0.06]"
				>
					<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{section.title}
					</h2>
					<p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
						{section.body}
					</p>
				</div>
			);
		case ComposerSectionKind.INFO_GRID:
			return (
				<div className="rounded-lg border border-zinc-200/90 bg-white/90 p-5 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-700/90 dark:bg-zinc-950/50 dark:ring-white/[0.06]">
					<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{section.title}
					</h2>
					<dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
						{section.items.map((item) => (
							<div
								key={item.label}
								className="rounded-lg border border-zinc-100/90 bg-zinc-50/80 p-3.5 dark:border-zinc-800 dark:bg-zinc-900/60"
							>
								<dt className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
									{item.label}
								</dt>
								<dd className="mt-1.5 text-sm text-zinc-900 dark:text-zinc-100">
									{item.value}
								</dd>
							</div>
						))}
					</dl>
				</div>
			);
		case ComposerSectionKind.DATA_TABLE:
			return (
				<div className="rounded-lg border border-zinc-200/90 bg-white/90 p-5 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-700/90 dark:bg-zinc-950/50 dark:ring-white/[0.06]">
					<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{section.title}
					</h2>
					<div className="mt-4 overflow-x-auto rounded-md border border-zinc-200/90 dark:border-zinc-700">
						<table className="w-full min-w-[28rem] text-left text-sm">
							<thead className="border-b border-zinc-200 bg-zinc-100/95 dark:border-zinc-700 dark:bg-zinc-800/90">
								<tr>
									{section.columns.map((col) => (
										<th
											key={col.key}
											className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300"
										>
											{col.label}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								{section.rows.map((row, ri) => (
									<tr
										key={ri}
										className="border-b border-zinc-100 transition-colors hover:bg-zinc-50/80 dark:border-zinc-800 dark:hover:bg-zinc-900/50"
									>
										{section.columns.map((col) => (
											<td
												key={col.key}
												className="px-3 py-2 text-zinc-800 dark:text-zinc-200"
											>
												{row[col.key] ?? '—'}
											</td>
										))}
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</div>
			);
		case ComposerSectionKind.LOADING_PANEL:
			return (
				<div
					className="flex min-h-[min(50dvh,420px)] flex-col items-center justify-center gap-4 rounded-lg border border-zinc-200/80 bg-white/70 px-8 py-12 dark:border-zinc-700/80 dark:bg-zinc-950/40"
					role="status"
				>
					<Spinner aria-label="Loading" />
					<p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
						{section.message}
					</p>
				</div>
			);
		default:
			return null;
	}
}

function breadcrumbsFromPdf(headerProps: Record<string, unknown> | undefined): Breadcrumb[] {
	const raw = headerProps?.breadcrumbs;
	if (!Array.isArray(raw)) return [];
	return raw.filter(
		(b): b is Breadcrumb =>
			b !== null && typeof b === 'object' && typeof (b as Breadcrumb).name === 'string',
	);
}

export const SinglePageComposer = forwardRef<HTMLDivElement, SinglePageComposerProps>(
	function SinglePageComposer(props, ref) {
		const {
			sections,
			header,
			leftPanel,
			rightPanel,
			pdfProps,
			entityUpdatingProperties,
			isEditingDescription,
			onSaveDescription,
			onCancelEdit,
		} = props;
		const hh = header?.header;
		const title = (hh?.title as string) ?? 'Untitled';
		const subtitle = hh?.subtitle as string | undefined;
		const entityId = hh?.entityId as string | undefined;
		const parentId = hh?.parentId as string | undefined;
		const editProps = hh?.editProps as EditHeaderProps | undefined;
		const pdfPageName = (hh?.pdfProps as { pageName?: string } | undefined)?.pageName;
		const handleIsPDF = (hh?.pdfProps as { handleIsPDF?: (v: boolean) => void })?.handleIsPDF;

		const pdfHeader = pdfProps?.headerProps;
		const breadcrumbs = breadcrumbsFromPdf(pdfHeader);
		const isPDFView = pdfProps?.isPDFView ?? false;

		const gridTemplate =
			leftPanel && rightPanel
				? `[full-start] ${leftPanel.width} [main-start] 1fr [main-end] ${rightPanel.width} [full-end]`
				: leftPanel
					? `[full-start] ${leftPanel.width} [main-start] 1fr [main-end full-end]`
					: rightPanel
						? `[full-start main-start] 1fr [main-end] ${rightPanel.width} [full-end]`
						: `[full-start main-start] 1fr [main-end full-end]`;

		if (isPDFView) {
			return (
				<div
					ref={ref}
					className="flex min-h-[50vh] w-full flex-col gap-6 rounded-lg border border-zinc-200 bg-white p-8 text-zinc-900 shadow-sm dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-50"
				>
					<div className="border-b border-zinc-200 pb-4 dark:border-zinc-700">
						<p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
							PDF preview
							{pdfPageName ? (
								<span className="ml-2 font-normal normal-case text-zinc-600 dark:text-zinc-400">
									{pdfPageName}
								</span>
							) : null}
						</p>
						<div className="mt-2 flex flex-wrap gap-1 text-sm text-zinc-600 dark:text-zinc-400">
							{breadcrumbs.map((b, i) => (
								<span key={b.id ?? `${b.path}-${i}`}>
									{i > 0 ? <span className="mx-1 text-zinc-400">/</span> : null}
									{b.name}
								</span>
							))}
						</div>
						<h1 className="mt-3 text-2xl font-semibold tracking-tight">{title}</h1>
					</div>
					<ol className="list-decimal space-y-4 pl-5 text-sm">
						{sections.map((s, i) => (
							<li key={i} className="text-zinc-700 dark:text-zinc-300">
								<span className="font-medium">
									{isComposerSection(s) ? composerSectionHeading(s) : 'Section'}
								</span>
							</li>
						))}
					</ol>
					{handleIsPDF ? (
						<button
							type="button"
							className="self-start rounded-md bg-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-300 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700"
							onClick={() => handleIsPDF(false)}
						>
							Close PDF preview
						</button>
					) : null}
				</div>
			);
		}

		return (
			<div
				ref={ref}
				className="box-border flex h-full min-h-0 w-full min-w-0 flex-1 flex-col gap-0 overflow-hidden rounded-b-2xl rounded-t-none border border-zinc-200/90 bg-zinc-50/90 p-4 shadow-xl shadow-zinc-300/40 ring-1 ring-zinc-950/5 sm:p-5 md:p-6 dark:border-zinc-700/90 dark:bg-zinc-900/50 dark:shadow-2xl dark:shadow-black/40 dark:ring-white/5"
			>
				<div
					className="h-1 shrink-0 rounded-full bg-gradient-to-r from-emerald-500 via-teal-500 to-cyan-600"
					aria-hidden
				/>
				{header?.errorBanner ? (
					<div className="border-b border-amber-200/90 bg-amber-50 px-7 py-4 text-sm text-amber-950 sm:px-10 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-100">
						{String(header.errorBanner)}
					</div>
				) : null}

				<header className="border-b border-zinc-200/80 bg-gradient-to-b from-white to-zinc-50/90 px-7 py-6 sm:px-10 sm:py-7 dark:border-zinc-700/80 dark:from-zinc-950 dark:to-zinc-950/90">
					<div className="flex flex-wrap items-start justify-between gap-3">
						<div className="min-w-0">
							<h1 className="text-xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50 sm:text-2xl">
								{title}
							</h1>
							{subtitle ? (
								<p className="mt-1.5 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
									{subtitle}
								</p>
							) : null}
							<dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500 dark:text-zinc-500">
								{entityId ? (
									<div>
										<dt className="inline font-medium">Entity</dt>{' '}
										<dd className="inline font-mono text-zinc-700 dark:text-zinc-300">
											{entityId}
										</dd>
									</div>
								) : null}
								{parentId ? (
									<div>
										<dt className="inline font-medium">Parent</dt>{' '}
										<dd className="inline font-mono text-zinc-700 dark:text-zinc-300">
											{parentId}
										</dd>
									</div>
								) : null}
							</dl>
						</div>
						<div className="flex flex-wrap items-center gap-2">
							{handleIsPDF ? (
								<button
									type="button"
									className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-800 shadow-sm hover:bg-zinc-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
									onClick={() => handleIsPDF(true)}
								>
									PDF preview
								</button>
							) : null}
							{editProps && editProps.onBeginEdit && editProps.onFinishEdit ? (
								editProps.editMode ? (
									<>
										<button
											type="button"
											disabled={editProps.isLoadingUpdate}
											className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
											onClick={() => editProps.onFinishEdit?.(true)}
										>
											{editProps.isLoadingUpdate ? 'Saving…' : 'Save'}
										</button>
										<button
											type="button"
											disabled={editProps.isLoadingUpdate}
											className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200"
											onClick={() => editProps.onFinishEdit?.(false)}
										>
											Cancel
										</button>
									</>
								) : (
									<button
										type="button"
										className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
										onClick={() => editProps.onBeginEdit?.()}
									>
										Edit
									</button>
								)
							) : null}
						</div>
					</div>
				</header>

				<div
					className="grid min-h-0 w-full min-w-0 flex-1 gap-0 overflow-hidden"
					style={{ gridTemplateColumns: gridTemplate }}
				>
					{leftPanel ? (
						<aside className="flex min-h-0 min-w-0 flex-col border-r border-zinc-200/80 bg-gradient-to-b from-zinc-50/95 to-white dark:border-zinc-700/80 dark:from-zinc-950 dark:to-zinc-950/90">
							{leftPanel.slot ? (
								<div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden p-0">
									{leftPanel.slot}
								</div>
							) : (
								<div className="px-5 py-4 text-sm text-zinc-600 sm:px-6 dark:text-zinc-400">
									<p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
										Left panel
									</p>
									<p className="text-xs text-zinc-500">
										{leftPanel.bulks.length} bulk(s) · width {leftPanel.width}
									</p>
								</div>
							)}
						</aside>
					) : null}

					<main className="min-h-0 min-w-0 overflow-y-auto bg-[linear-gradient(180deg,rgba(255,255,255,1)_0%,rgba(250,250,250,0.6)_100%)] px-7 py-6 sm:px-10 sm:py-7 dark:bg-[linear-gradient(180deg,rgba(9,9,11,1)_0%,rgba(24,24,27,0.5)_100%)]">
						{sections.length === 0 ? (
							<div className="flex min-h-[14rem] flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-zinc-300/90 bg-white/70 px-8 py-12 text-center dark:border-zinc-600 dark:bg-zinc-900/30">
								<div
									className="flex h-12 w-12 items-center justify-center rounded-lg bg-emerald-100/90 text-xl dark:bg-emerald-950/60"
									aria-hidden
								>
									◇
								</div>
								<p className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
									Nothing selected yet
								</p>
								<p className="max-w-sm text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
									Pick a database, schema, table, column, or field in the explorer
									to load metadata, descriptions, and related entities.
								</p>
							</div>
						) : (
							<div className="space-y-5">
								{sections.map((section, i) =>
									isComposerSection(section) ? (
										<div key={section.id}>
											{isEditingDescription &&
											section.type === ComposerSectionKind.TEXT_CARD &&
											section.id === 'description' ? (
												<EditableDescriptionCard
													section={section}
													onSave={onSaveDescription}
													onCancel={onCancelEdit}
												/>
											) : (
												renderComposerSection(section)
											)}
										</div>
									) : (
										<section
											key={i}
											className="rounded-lg border border-zinc-200/90 bg-white/90 p-5 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-700/90 dark:bg-zinc-950/50 dark:ring-white/[0.06]"
										>
											<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
												{typeof section === 'object' &&
												section !== null &&
												'title' in section &&
												typeof (section as { title: unknown }).title ===
													'string'
													? (section as { title: string }).title
													: 'Section'}
											</h2>
											<pre className="mt-2 max-h-40 overflow-auto rounded bg-zinc-100 p-2 text-xs text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
												{JSON.stringify(section, null, 2)}
											</pre>
										</section>
									),
								)}
							</div>
						)}

						{entityUpdatingProperties &&
						Object.keys(entityUpdatingProperties).length > 0 ? (
							<div className="mt-6 rounded-lg border border-dashed border-zinc-300 p-3 dark:border-zinc-600">
								<p className="mb-2 text-xs font-semibold uppercase text-zinc-500">
									Entity updates (preview)
								</p>
								<ul className="space-y-1 text-xs text-zinc-600 dark:text-zinc-400">
									{Object.entries(entityUpdatingProperties).map(([k, v]) => (
										<li key={k}>
											<span className="font-mono text-zinc-800 dark:text-zinc-200">
												{k}
											</span>
											: {Array.isArray(v) ? v.join(', ') : v}
										</li>
									))}
								</ul>
							</div>
						) : null}
					</main>

					{rightPanel ? (
						<aside className="flex min-h-0 min-w-0 flex-col border-l border-zinc-200/80 bg-gradient-to-b from-zinc-50/95 to-white dark:border-zinc-700/80 dark:from-zinc-950 dark:to-zinc-950/90">
							{rightPanel.slot ? (
								<div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden px-4 py-4 sm:px-5">
									{rightPanel.slot}
								</div>
							) : (
								<div className="px-5 py-4 text-sm text-zinc-600 sm:px-6 dark:text-zinc-400">
									<p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
										Right panel
									</p>
									<p className="text-xs text-zinc-500">
										{rightPanel.bulks.length} bulk(s) · width {rightPanel.width}
									</p>
								</div>
							)}
						</aside>
					) : null}
				</div>
			</div>
		);
	},
);
