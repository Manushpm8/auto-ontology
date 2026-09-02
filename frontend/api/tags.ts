// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { ApiResponse, ResponseWithError } from './types';
import type { Tag, TagCreateInput } from '@/types/tags';

export const tagsApi = {
	getAll: (): Promise<ApiResponse<Tag[]>> => requests.get('tags'),

	/** 409 when the name is taken — its `message` is the backend's own wording. */
	create: (input: TagCreateInput): Promise<ResponseWithError<{ data: Tag }>> =>
		requests.post('tags', input),

	/** 404 when the tag is already gone, which means the caller's list is stale. */
	delete: (tagId: string): Promise<ResponseWithError<{ data: { id: string } }>> =>
		requests.delete(`tags/${tagId}`),
};
