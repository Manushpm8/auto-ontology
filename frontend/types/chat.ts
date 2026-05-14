// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type ChatMessage = {
	id: string;
	role: 'user' | 'assistant';
	content: string;
	sql?: string;
	timestamp: number;
};

export type ChatRequest = {
	question: string;
	connectorName?: string;
	acronyms?: string;
	customPrompts?: string;
};

export type StepEvent = {
	type: 'step';
	node: string;
	label: string;
};

export type ResultEvent = {
	type: 'result';
	answer: Record<string, unknown>;
};

export type ErrorEvent = {
	type: 'error';
	message: string;
};

export type ChatStreamEvent = StepEvent | ResultEvent | ErrorEvent;

export type GraphStep = {
	node: string;
	label: string;
	status: 'completed' | 'active';
};

export type Conversation = {
	id: string;
	title: string;
	messages: ChatMessage[];
	createdAt: number;
};
