'use client';

import { useEffect, useRef } from 'react';
import { Modal } from './Modal';

type ConfirmModalProps = {
	open: boolean;
	title: string;
	message: string;
	confirmLabel?: string;
	cancelLabel?: string;
	onConfirm: () => void;
	onCancel: () => void;
};

export const ConfirmModal = ({
	open,
	title,
	message,
	confirmLabel = 'Delete',
	cancelLabel = 'Cancel',
	onConfirm,
	onCancel,
}: ConfirmModalProps) => {
	const cancelRef = useRef<HTMLButtonElement>(null);

	useEffect(() => {
		if (open) cancelRef.current?.focus();
	}, [open]);

	return (
		<Modal open={open} onClose={onCancel}>
			<div className="p-6">
				<h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
					{title}
				</h3>
				<p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">{message}</p>
				<div className="mt-5 flex justify-end gap-2">
					<button
						ref={cancelRef}
						type="button"
						onClick={onCancel}
						className="rounded-lg border border-zinc-300 px-3.5 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
					>
						{cancelLabel}
					</button>
					<button
						type="button"
						onClick={onConfirm}
						className="rounded-lg bg-red-600 px-3.5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-700"
					>
						{confirmLabel}
					</button>
				</div>
			</div>
		</Modal>
	);
};
