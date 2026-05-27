// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
	conversationsApi,
	type ConversationDetail,
	type ConversationSummary,
} from '@/api/conversations';
import { CACHE } from '@/lib/queryClient';

// Single source of truth for query keys so invalidations stay in sync.
export const conversationsKeys = {
	all: ['conversations'] as const,
	detail: (id: string) => ['conversations', 'detail', id] as const,
};

export const useConversationsList = () =>
	useQuery<ConversationSummary[]>({
		queryKey: conversationsKeys.all,
		queryFn: () => conversationsApi.list(),
	});

export const useConversationDetail = (id: string | null) =>
	useQuery<ConversationDetail>({
		queryKey: conversationsKeys.detail(id ?? ''),
		queryFn: () => conversationsApi.get(id as string),
		enabled: !!id,
		// Detail is a snapshot at load time; once the store owns the messages
		// we don't want background refetches to clobber streaming state.
		staleTime: Infinity,
		gcTime: CACHE.DETAIL_GC,
	});

export const useCreateConversation = () => {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (title: string) => conversationsApi.create(title),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: conversationsKeys.all });
		},
	});
};

export const useRenameConversation = () => {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, title }: { id: string; title: string }) =>
			conversationsApi.rename(id, title),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: conversationsKeys.all });
		},
	});
};

export const useDeleteConversation = () => {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => conversationsApi.delete(id),
		onSuccess: (_data, id) => {
			qc.invalidateQueries({ queryKey: conversationsKeys.all });
			qc.removeQueries({ queryKey: conversationsKeys.detail(id) });
		},
	});
};
