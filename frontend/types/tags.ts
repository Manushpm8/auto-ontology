// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { TagItemType } from '@/enums/tags';

export type Tag = {
	id: string;
	name: string;
	/** ISO 8601, from the database clock. */
	created: string;
	/** ISO 8601. Equal to `created` until something edits the tag. */
	modified: string;
};

export type TagCreateInput = {
	name: string;
};

/** A rename — the name is the only part of a tag there is to edit. */
export type TagUpdateInput = {
	name: string;
};

/**
 * A tag as rendered beside the object it labels.
 *
 * Narrower than `Tag` on purpose: a chip needs the name to show and the id to
 * remove itself by, and the timestamps describe the tag rather than the
 * labelling.
 */
export type TagChip = {
	id: string;
	name: string;
};

/** The object to label: which kind it is, and which one. */
export type TagTarget = {
	type: TagItemType;
	id: string;
};

/**
 * One object carrying a tag.
 *
 * `path` says where the object sits — `database.schema` for a table,
 * `database.schema.table` for a column, and the owning term for either kind of
 * attribute. Null only for a term, which is a glossary entry rather than a
 * catalog object and sits under nothing.
 */
export type TagItem = {
	id: string;
	name: string;
	type: TagItemType;
	path: string | null;
	/** ISO 8601. When the tag was applied to this object. */
	tagged: string;
	/**
	 * The same relationships as `path`, as ids — what a link to the object's own
	 * page is built from, since those pages are keyed by id and the names in
	 * `path` are not unique enough to look one up by.
	 *
	 * Each kind carries only the ones it has: a table its database and schema, a
	 * column those plus its table, an attribute the term whose page lists it, a
	 * term none at all. An attribute always has one — the backend refuses to tag
	 * one that does not — so every row has somewhere to open.
	 */
	database_id: string | null;
	schema_id: string | null;
	table_id: string | null;
	term_id: string | null;
};

/**
 * A tag with everything it labels.
 *
 * `items` is empty for a tag nothing carries — a freshly created one, or one
 * whose last object was untagged — which the detail page renders as its empty
 * state.
 */
export type TagDetail = Tag & {
	items: TagItem[];
};
