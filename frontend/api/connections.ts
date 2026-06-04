// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type { Connection } from '@/types/connection';
import type { ResponseWithCount } from './types';

export const connectionsApi = {
	getAll: () => requests.get<ResponseWithCount<Connection[]>>('connections'),
};
