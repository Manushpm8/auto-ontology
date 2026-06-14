// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { MessageAnalytic } from '@/types/analytics';
import type { ResponseWithError } from './types';

type ListResult = { data: MessageAnalytic[]; total: number };
type ListResponse = ResponseWithError<ListResult>;
type RowResponse = ResponseWithError<MessageAnalytic>;

export const analyticsApi = {
	list: (params: { skip?: number; limit?: number } = {}): Promise<ListResponse> =>
		requests.get<ListResult>('analytics', params),

	create: (questionId: string): Promise<RowResponse> =>
		requests.post<MessageAnalytic>('analytics', { questionId }),

	setAnswer: (analyticsId: string, answerId: string): Promise<RowResponse> =>
		requests.patch<MessageAnalytic>(`analytics/${encodeURIComponent(analyticsId)}`, {
			answerId,
		}),
};
