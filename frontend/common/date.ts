// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import dayjs from 'dayjs';

export type DateInput = string | number | Date;

export const formatDate = (value: DateInput, format = 'MMM DD YYYY'): string =>
	dayjs(value).format(format);
