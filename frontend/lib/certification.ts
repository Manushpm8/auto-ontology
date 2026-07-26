// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { CertificationStatus } from '@/enums/certification';

/** Shape shared by every entity with separate name/description flags (Term). */
export type CertifiableFields = {
	name_certified: boolean;
	description_certified: boolean;
};

/** Shape shared by attributes, which carry a single top-level certification flag. */
export type AttributeCertifiableFields = {
	certified: boolean;
};

/** Binary status for a single boolean flag. */
export const fieldStatus = (certified: boolean): CertificationStatus =>
	certified ? CertificationStatus.Certified : CertificationStatus.Pending;

/**
 * Binary status for one attribute (column or sql): CERTIFIED when its single
 * `certified` flag is set, otherwise PENDING. Never PARTIAL.
 */
export const attributeStatus = (item: AttributeCertifiableFields): CertificationStatus =>
	fieldStatus(item.certified);

/**
 * Three-state status for a term, derived from its own name/description flags
 * plus every column and sql attribute's single certification flag:
 *  - CERTIFIED when all flags are certified
 *  - PENDING when none are certified
 *  - PARTIAL otherwise
 *
 * Empty attribute collections are treated as vacuously certified (they never
 * block a CERTIFIED result).
 */
export const termStatus = (
	term: CertifiableFields,
	columnAttributes: AttributeCertifiableFields[],
	sqlAttributes: AttributeCertifiableFields[],
): CertificationStatus => {
	const flags: boolean[] = [
		term.name_certified,
		term.description_certified,
		...columnAttributes.map((a) => a.certified),
		...sqlAttributes.map((a) => a.certified),
	];
	if (flags.every(Boolean)) return CertificationStatus.Certified;
	if (flags.every((flag) => !flag)) return CertificationStatus.Pending;
	return CertificationStatus.Partial;
};
