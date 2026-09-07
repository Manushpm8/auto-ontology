// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SearchObjectType } from '@/enums/search';
import type { GlobalSearchRequest } from '@/types/search';
import type { TagChip } from '@/types/tags';

/**
 * A rule as its dialog has it, before anything is saved.
 *
 * A rule labels whatever a search matches — now and later — so what identifies
 * it is the tags to apply; the search itself is held by the screen the dialog
 * was opened from, which is what turns this into a `RuleCreateInput`.
 */
export type RuleTagDraft = {
	name: string;
	/** Applied to every matched item. */
	tags: TagChip[];
};

/**
 * A rule as it is saved.
 *
 * Built on `GlobalSearchRequest` rather than restating its three fields: a rule
 * *is* a saved search, and the request it replays has to be the request the
 * search accepts or the rule can never reproduce what it was created from.
 */
export type RuleCreateInput = GlobalSearchRequest & {
	name: string;
	/**
	 * The chips the dialog holds, sent whole.
	 *
	 * Only `id` is read: a chip's name is a snapshot of the tag table, so the
	 * backend resolves the names itself and answers with the ones it read. The
	 * name travels anyway because a caller already has it and reducing the chips
	 * to ids would only hide what was sent.
	 */
	tags: TagChip[];
};

/** The name and the tags, which are the only parts of a rule an edit changes. */
export type RuleUpdateInput = {
	name?: string;
	tags?: TagChip[];
};

/**
 * The search filters a rule replays.
 *
 * Every field is present here, unlike the `GlobalSearchFilters` a request
 * sends: a stored rule has resolved the defaults, so there is no longer an
 * omitted flag to interpret.
 *
 * `objects` is null for a rule saved from the search's All tab, which narrows
 * to no particular kind.
 */
export type RuleFilters = {
	description: boolean;
	synonyms: boolean;
	objects: SearchObjectType[] | null;
};

/** A stored rule, as every read returns it. */
export type Rule = {
	id: string;
	name: string;
	search_term: string;
	text_match_option: string;
	filters: RuleFilters;
	/** Applied to every item the search matches. */
	tags: TagChip[];
	/** Id of the user who saved it. */
	created_by: string;
	/** ISO 8601, from the backend clock. */
	created: string;
	/** ISO 8601. Equal to `created` until something edits the rule. */
	modified: string;
};
