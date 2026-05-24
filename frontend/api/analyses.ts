// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { CustomAnalysis } from '@/types/analysis';
import type { ResponseWithCount, ResponseWithError } from './types';

type ListResponse = ResponseWithError<ResponseWithCount<CustomAnalysis[]>>;

let pendingList: Promise<ListResponse> | null = null;

export const analyses = {
	/** All custom analyses; parallel callers share a single HTTP request. */
	list: (): Promise<ListResponse> => {
		if (pendingList != null) return pendingList;

		const promise = requests
			.get<ResponseWithCount<CustomAnalysis[]>>('custom-analyses')
			.finally(() => {
				pendingList = null;
			});

		pendingList = promise;
		return promise;
	},
};
