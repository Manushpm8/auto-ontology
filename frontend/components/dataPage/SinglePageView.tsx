'use client';

import { useEffect, useRef, useState } from 'react';
import { Spinner } from '@nvidia/foundations-react-core';
import { SinglePageComposer, type SinglePageComposerProps } from '@/common/SinglePageComposer';

export type SinglePageFormat = SinglePageComposerProps;

export type SinglePageViewProps = {
	dataId: string;
	title: string;
	getSinglePage: (dataId: string, treeFocusId: string | null) => Promise<SinglePageFormat>;
	treeFocusId?: string | null;
	/** Increment when explorer tree merges API data so details re-render without changing focus. */
	treeDataEpoch?: number;
	parentId?: string;
	isEditingDescription?: boolean;
	onSaveDescription?: () => void;
	onCancelEdit?: () => void;
};

export const SinglePageView = ({
	dataId,
	parentId,
	title,
	getSinglePage,
	treeFocusId = null,
	treeDataEpoch = 0,
	isEditingDescription = false,
	onSaveDescription,
	onCancelEdit,
}: SinglePageViewProps): React.JSX.Element | null => {
	const [loading, setLoading] = useState(true);
	const [props, setProps] = useState<SinglePageFormat | null>(null);
	const prevCoreRef = useRef<{
		dataId: string;
		treeFocusId: string | null;
		getSinglePage: (dataId: string, treeFocusId: string | null) => Promise<SinglePageFormat>;
	} | null>(null);

	useEffect(() => {
		let cancelled = false;
		const prev = prevCoreRef.current;
		const coreChanged =
			prev == null ||
			prev.dataId !== dataId ||
			prev.treeFocusId !== treeFocusId ||
			prev.getSinglePage !== getSinglePage;
		prevCoreRef.current = { dataId, treeFocusId, getSinglePage };

		(async () => {
			if (coreChanged) {
				setLoading(true);
			}
			try {
				const response = await getSinglePage(dataId, treeFocusId);
				if (!cancelled) {
					setProps(response);
				}
			} finally {
				if (!cancelled) {
					setLoading(false);
				}
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [dataId, getSinglePage, treeFocusId, treeDataEpoch]);

	if (loading) {
		return (
			<div
				className="m-2 flex h-full min-h-[min(70dvh,480px)] w-full flex-col items-center justify-center gap-4 rounded-2xl border border-zinc-200/80 bg-white/70 px-8 py-12 shadow-lg shadow-zinc-200/30 backdrop-blur-[2px] sm:m-3 dark:border-zinc-700/80 dark:bg-zinc-950/50 dark:shadow-none"
				role="status"
			>
				<Spinner aria-label="Loading" />
				<p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
					Loading details…
				</p>
			</div>
		);
	}

	if (!props) {
		return null;
	}

	const hasTreeFocus = treeFocusId != null && treeFocusId !== '';

	return (
		<div className="mt-1 flex h-full min-h-0 w-full flex-1 flex-col justify-start overflow-hidden !p-[20px] sm:mt-2">
			<SinglePageComposer
				header={{
					header: {
						...(hasTreeFocus ? { entityId: dataId, parentId } : {}),
						title,
						withBorder: true,
						...props.header?.header,
					},
					errorBanner: props.header?.errorBanner,
				}}
				sections={props.sections}
				rightPanel={props?.rightPanel}
				leftPanel={props?.leftPanel}
				entityUpdatingProperties={props?.entityUpdatingProperties}
				isEditingDescription={isEditingDescription}
				onSaveDescription={onSaveDescription}
				onCancelEdit={onCancelEdit}
			/>
		</div>
	);
};
