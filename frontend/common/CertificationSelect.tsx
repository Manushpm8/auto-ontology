// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { CertificationBadge } from '@/common/CertificationBadge';
import { PopoverMenu } from '@/common/PopoverMenu';
import { Icon, IconName } from '@/common/icons';
import { fieldStatus } from '@/lib/certification';

export type CertificationSelectProps = {
	/** Current value of the single boolean flag this control edits. */
	certified: boolean;
	/** Called with the newly selected value (Pending -> false, Certified -> true). */
	onChange: (certified: boolean) => void;
	/** When true, renders a read-only badge instead of the dropdown. */
	disabled?: boolean;
	/** Renders the full pill (icon + status text) instead of the icon-only badge. */
	showLabel?: boolean;
};

export const CertificationSelect = ({
	certified,
	onChange,
	disabled = false,
	showLabel = false,
}: CertificationSelectProps) => {
	const status = fieldStatus(certified);

	if (disabled) {
		return <CertificationBadge status={status} iconOnly={!showLabel} />;
	}

	const items = [
		{
			label: 'Pending Approval',
			icon: <Icon name={IconName.Certification} className="h-4 w-4 text-zinc-400" />,
			onClick: () => onChange(false),
		},
		{
			label: 'Certified',
			icon: <Icon name={IconName.Certification} className="h-4 w-4 text-[#76b900]" />,
			onClick: () => onChange(true),
		},
	];

	return (
		<PopoverMenu
			className="inline-flex"
			items={items}
			trigger={({ toggle }) => (
				<button
					type="button"
					onClick={toggle}
					title="Set certification"
					className="inline-flex cursor-pointer items-center gap-1"
				>
					<CertificationBadge status={status} iconOnly={!showLabel} />
					<Icon
						name={IconName.ChevronRight}
						className="h-3 w-3 rotate-90 text-zinc-400"
					/>
				</button>
			)}
		/>
	);
};
