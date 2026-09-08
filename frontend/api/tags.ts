// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { pageQuery, requests } from './requests';
import type { ApiPagedResponse, ApiResponse, PageParams, ResponseWithError } from './types';
import { AUTHORS_PARAM, AUTHORS_PARAM_ON } from '@/constants/tags';
import type { TagItemType } from '@/enums/tags';
import type {
	Tag,
	TagChip,
	TagCreateInput,
	TagItem,
	TagTarget,
	TagUpdateInput,
} from '@/types/tags';

export const tagsApi = {
	/**
	 * Every tag.
	 *
	 * `authors` asks the route to resolve `created_by` / `modified_by` to
	 * accounts, and only the settings page wants it — see `AUTHORS_PARAM`. The
	 * tag picker calls this on every detail page it is shown on, so the default
	 * is the cheaper answer.
	 */
	getAll: ({ authors = false }: { authors?: boolean } = {}): Promise<ApiResponse<Tag[]>> =>
		requests.get('tags', authors ? { [AUTHORS_PARAM]: AUTHORS_PARAM_ON } : {}),

	/**
	 * One tag. What it labels is `getTargets`, a page at a time.
	 *
	 * Always asks for `authors`, unlike `getAll`: this is read once per detail
	 * page rather than on every navigation, and the page shows who curated the
	 * tag.
	 *
	 * 404 when the tag is gone, which — as for `delete` — means the list the
	 * caller opened it from is stale.
	 */
	getById: (tagId: string): Promise<ResponseWithError<{ data: Tag }>> =>
		requests.get(`tags/${tagId}`, { [AUTHORS_PARAM]: AUTHORS_PARAM_ON }),

	/**
	 * One page of the objects a tag labels, ordered by name, with `total`
	 * counting everything it labels so a caller knows when to stop asking.
	 *
	 * Its own read rather than a field on the tag: a tag applied by a rule can
	 * end up on the whole catalog, so this is the list the page scrolls.
	 *
	 * Every row carries the account or the rule that applied the label — the
	 * "Tagged By" column — resolved by the route without being asked, since the
	 * table has nowhere to put an unresolved id.
	 *
	 * 404 when the tag is gone; an existing tag labelling nothing answers 200
	 * with an empty page, which is the caller's empty state.
	 */
	getTargets: (tagId: string, params?: PageParams): Promise<ApiPagedResponse<TagItem[]>> =>
		requests.get(`tags/${tagId}/targets`, pageQuery(params)),

	/** 409 when the name is taken — its `message` is the backend's own wording. */
	create: (input: TagCreateInput): Promise<ResponseWithError<{ data: Tag }>> =>
		requests.post('tags', input),

	/**
	 * Rename a tag.
	 *
	 * Answers with the whole tag, whose `modified` the rename has advanced, so
	 * the row is redrawn from this response rather than from the name that was
	 * sent. 409 when another tag holds the name, 404 when this one is gone —
	 * either way the caller's list is stale. The tag's own name is accepted,
	 * including under a different case.
	 */
	update: (tagId: string, input: TagUpdateInput): Promise<ResponseWithError<{ data: Tag }>> =>
		requests.patch(`tags/${tagId}`, input),

	/** 404 when the tag is already gone, which means the caller's list is stale. */
	delete: (tagId: string): Promise<ResponseWithError<{ data: { id: string } }>> =>
		requests.delete(`tags/${tagId}`),

	/**
	 * Label an object with a tag.
	 *
	 * Both of these answer with the **object's** tags afterwards, not the tag
	 * that moved, so a caller redraws its chips from the response instead of
	 * trusting its own optimistic edit.
	 *
	 * Attaching a tag the object already carries succeeds and returns the same
	 * list; 404 means the tag or the object is gone.
	 */
	attach: (tagId: string, target: TagTarget): Promise<ApiResponse<TagChip[]>> =>
		requests.post(`tags/${tagId}/targets`, target),

	/** 404 when the object was not carrying the tag — a stale page. */
	detach: (tagId: string, type: TagItemType, itemId: string): Promise<ApiResponse<TagChip[]>> =>
		requests.delete(`tags/${tagId}/targets/${type}/${itemId}`),
};
