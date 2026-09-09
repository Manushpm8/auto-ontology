// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { pageQuery, requests } from './requests';
import type { ApiPagedResponse, PageParams, ResponseWithError } from './types';
import type { Rule, RuleCreateInput, RuleUpdateInput } from '@/types/rules';

/** One page of rules, optionally narrowed by `q`. */
export type RulesListParams = PageParams & {
	/**
	 * Case-insensitive substring, matched against a rule's name and against the
	 * names of the tags it applies — the two things a rule's card shows.
	 */
	q?: string;
};

/**
 * Rule-based tags: a saved search, and the tags applied to everything it
 * matches.
 *
 * All of it is backed by the `rule` table. `update` is a rename and nothing
 * more — see `RuleUpdateInput` — and the three calls that take an id answer 404
 * when the rule is gone, which means the list the caller acted from is stale.
 */
export const rulesApi = {
	/**
	 * One page of rules, ordered by name, with `total` counting the whole match
	 * so a caller knows when to stop asking.
	 */
	getAll: (params?: RulesListParams): Promise<ApiPagedResponse<Rule[]>> =>
		requests.get('rules', {
			...(params?.q ? { q: params.q } : {}),
			...pageQuery(params),
		}),

	getById: (ruleId: string): Promise<ResponseWithError<{ data: Rule }>> =>
		requests.get(`rules/${ruleId}`),

	/**
	 * Save a rule, and get back its id.
	 *
	 * The id alone: the dialog closes on success and the list re-reads, so
	 * everything else about the stored rule — the timestamps, the author, the tag
	 * names — arrives from `getAll` rather than from an echo of what was posted.
	 *
	 * Tags go up as whole objects; only their ids are checked, against the tag
	 * table, and the rest is kept beside them so a read answers with the tags
	 * that were picked.
	 *
	 * 400 for a body that could never be a rule (a blank name, a search term
	 * shorter than the search accepts, no tags); 409 when another rule already
	 * holds the name, which is unique as a tag's is; 404 when a tag id is not a
	 * tag, which means the picker's list is stale. Its `message` is the
	 * backend's own wording in every case, so a form can show it as it came.
	 */
	create: (input: RuleCreateInput): Promise<ResponseWithError<{ data: { id: string } }>> =>
		requests.post('rules', input),

	/**
	 * Rename a rule. Answers with the whole rule, whose `modified` the edit has
	 * advanced and whose `modified_by` it has set to the caller.
	 *
	 * What the rule labelled is untouched: those rows name the rule by id, so
	 * every one of them reads under the new name.
	 *
	 * 400 for a blank name and 409 for one another rule holds — its own name,
	 * and a change of case alone, are not conflicts.
	 */
	update: (ruleId: string, input: RuleUpdateInput): Promise<ResponseWithError<{ data: Rule }>> =>
		requests.patch(`rules/${ruleId}`, input),

	/**
	 * Delete a rule, and say what becomes of the tags it applied. Anything
	 * tagged by hand is untouched either way: those rows name no rule.
	 *
	 * By default the labels go with the rule — they exist because it matched, so
	 * without it they are claims nothing can explain — and a tag those labels
	 * were the whole of goes too, unless another rule applies it. `keepTags`
	 * leaves them where they are instead, attributed to whoever wrote the rule,
	 * so the tag's page names that person rather than the rule; nothing is
	 * emptied that way, so no tag is deleted either.
	 *
	 * Keeping them is not a detach-and-reattach: recreating the same rule will
	 * not adopt the labels left behind, because a rule never claims a label that
	 * is already there.
	 *
	 * 404 when the rule is already gone, which means the caller's list is stale.
	 */
	delete: (
		ruleId: string,
		options?: { keepTags?: boolean },
	): Promise<ResponseWithError<{ data: { id: string } }>> =>
		requests.delete(`rules/${ruleId}${options?.keepTags ? '?keep_tags=true' : ''}`),
};
