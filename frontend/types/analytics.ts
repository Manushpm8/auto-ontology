// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type MessageAnalytic = {
	id: string;
	questionId: string;
	answerId: string | null;
	createdAt: string;
	updatedAt: string;
	question: string | null;
	reasoning: string | null;
	responseSql: string | null;
};
