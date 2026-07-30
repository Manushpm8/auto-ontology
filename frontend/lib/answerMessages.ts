// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// One agent answer renders as up to two assistant bubbles (illumex-style):
// Message 1 — prose + SQL; Message 2 — charts, or the raw result table when
// visualization is off or produced nothing.
//
// The two bubbles land at different times, because charts are a separate
// second step (POST /api/chat/visualize) rather than part of the agent's
// answer: Message 1 ships as soon as the SQL pipeline resolves, Message 2 once
// the chart step reports back. Each is therefore persisted by the route that
// produces it — the chat-completions proxy writes Message 1 in `after()`, the
// visualize proxy writes Message 2 — so history matches the live view.
//
// The split lives here because those routes and `useChat.ts` (which renders it
// live) all have to agree on it exactly. If they disagreed, reloading a
// conversation would show a different layout than watching the answer arrive.

import { stringifySqlResponse } from '@/lib/sqlResponse';

export const GENERIC_ANSWER_ERROR =
	'Something went wrong. Please try again, and if the issue persists, contact our support';

/** The agent keeps prose in `response` and chart specs in a separate array. */
export type AgentAnswer = {
	response?: string | null;
	sql_code?: string | null;
	sql_response_from_db?: unknown;
	charts?: unknown;
};

export type AnswerMessage = {
	content: string;
	sql?: string;
	sqlResponse?: string;
};

/** Strip ```chart / ```chart-carousel fences so Message 1 stays prose-only. */
export const stripChartFences = (markdown: string): string =>
	markdown
		.replace(/(^|\n)```(?:chart|chart-carousel)\b[\s\S]*?```/g, '\n')
		.replace(/\n{3,}/g, '\n\n')
		.trim();

export const chartsToFencedContent = (charts: Record<string, unknown>[]): string =>
	charts.map((spec) => `\`\`\`chart\n${JSON.stringify(spec)}\n\`\`\``).join('\n\n');

/** Message 1 — prose + SQL, ready the moment the agent's answer lands. */
export const buildProseMessage = (answer: AgentAnswer): AnswerMessage | null => {
	const { response, sql_code: sqlCode } = answer;
	const prose = stripChartFences(response ?? '');

	if (prose || sqlCode) return { content: prose, sql: sqlCode ?? undefined };

	// Nothing to say and no executed result for Message 2 to fall back on —
	// surface something rather than leaving the user without a reply.
	if (stringifySqlResponse(answer.sql_response_from_db) === undefined) {
		return { content: GENERIC_ANSWER_ERROR };
	}
	return null;
};

/**
 * Message 2 — the charts returned by the visualize step, or the raw result
 * table when that step is disabled, fails, or finds nothing worth plotting.
 * `charts` overrides `answer.charts` so callers can pass the specs the second
 * request just returned.
 */
export const buildResultMessage = (answer: AgentAnswer, charts?: unknown): AnswerMessage | null => {
	const specs = charts ?? answer.charts;
	if (Array.isArray(specs) && specs.length > 0) {
		return { content: chartsToFencedContent(specs as Record<string, unknown>[]) };
	}

	const sqlResponse = stringifySqlResponse(answer.sql_response_from_db);
	return sqlResponse ? { content: '', sqlResponse } : null;
};

export const buildAnswerMessages = (answer: AgentAnswer, charts?: unknown): AnswerMessage[] =>
	[buildProseMessage(answer), buildResultMessage(answer, charts)].filter(
		(msg): msg is AnswerMessage => msg !== null,
	);
