// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type ConversationAnalytics = {
	id: string;
	questionMessageId: string;
	question: string;
	questionTimestamp: string;
	response: string | null;
	responseMessageId: string | null;
	responseTimestamp: string | null;
	sql: string | null;
	// Display name (or email) of the user who ran the question, denormalized
	// onto the row at capture time (this is a reporting table).
	userName?: string | null;
};
