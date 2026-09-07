// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { ApiResponse, ResponseWithError } from './types';
import type { Rule, RuleCreateInput, RuleUpdateInput } from '@/types/rules';

/**
 * Rule-based tags: a saved search, and the tags applied to everything it
 * matches.
 *
 * Nothing is stored behind these yet — see the module docstring of
 * `gsf/server/rules/router.py`. `create` validates and answers with the rule it
 * describes, `getAll` answers an empty list, and the three that address a
 * stored rule answer 404. The shapes are the ones the storing version keeps, so
 * callers written against them do not change when it lands.
 */
export const rulesApi = {
	/** Empty until rules are stored, which the Rules page shows as its empty state. */
	getAll: (): Promise<ApiResponse<Rule[]>> => requests.get('rules'),

	getById: (ruleId: string): Promise<ResponseWithError<{ data: Rule }>> =>
		requests.get(`rules/${ruleId}`),

	/**
	 * Save a rule, and get it back as a stored one would read back — with the
	 * id, the author and the timestamps the backend filled in.
	 *
	 * Tags go up as whole objects, but only their ids are read — the names in
	 * the answer are the ones the backend found in the tag table.
	 *
	 * 400 for a body that could never be a rule (a blank name, a search term
	 * shorter than the search accepts, no tags); 404 when a tag id is not a tag,
	 * which means the picker's list is stale. Its `message` is the backend's own
	 * wording in both cases.
	 */
	create: (input: RuleCreateInput): Promise<ResponseWithError<{ data: Rule }>> =>
		requests.post('rules', input),

	/** Answers with the whole rule, whose `modified` the edit has advanced. */
	update: (ruleId: string, input: RuleUpdateInput): Promise<ResponseWithError<{ data: Rule }>> =>
		requests.patch(`rules/${ruleId}`, input),

	delete: (ruleId: string): Promise<ResponseWithError<{ data: { id: string } }>> =>
		requests.delete(`rules/${ruleId}`),
};
