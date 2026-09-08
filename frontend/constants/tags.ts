// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Mirrors `MAX_TAG_NAME_LENGTH` in `gsf/server/tags/router.py`, which rejects longer. */
export const MAX_TAG_NAME_LENGTH = 25;

/**
 * Query param asking `/api/tags` to resolve `created_by` / `modified_by` to the
 * accounts they name.
 *
 * Opt-in rather than given to everyone who could see it. The same list is read
 * by the tag picker on every catalog and glossary detail page — on each
 * navigation, and again after each save — and the picker wants a name and an id
 * to put on a chip. Resolving authors for it would be a user lookup per page
 * open whose result nothing reads.
 */
export const AUTHORS_PARAM = 'authors';

/** The value {@link AUTHORS_PARAM} is asked with. */
export const AUTHORS_PARAM_ON = '1';

/**
 * The actor a tag or a label carries when no person asked for it. Mirrors
 * `SYSTEM_ACTOR` in `gsf/dal/tags.py`, which the attach path writes to a
 * label's `tagged_by`.
 *
 * Distinct from a null, which means the actor was never recorded — a row
 * written before there was a column for it. Both are rendered, and the tag
 * list tells them apart: one says the deployment made the tag, the other says
 * nothing at all.
 */
export const SYSTEM_ACTOR = 'system';

/** Shown for `SYSTEM_ACTOR`, and for a label with no account behind it. */
export const SYSTEM_ACTOR_LABEL = 'Auto Generated';

/**
 * Shown for a null author, and for an id whose account is gone — the ids are
 * not foreign keys, so a deleted user leaves one behind that resolves to
 * nothing. Both are the same thing to a reader: there is a tag, and nobody left
 * to attribute it to.
 */
export const UNKNOWN_ACTOR_LABEL = 'Unknown';
