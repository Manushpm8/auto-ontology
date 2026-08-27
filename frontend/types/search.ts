// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { SearchObjectType } from '@/enums/search';

export type DiscoveryBreadcrumb = {
	name: string;
	type: string;
	id?: string | null;
};

export type DiscoverySearchItem = {
	id: string;
	name: string | null;
	type: SearchObjectType;
	description: string | null;
	certified: boolean | string | null;
	parent_id: string | null;
	breadcrumbs: DiscoveryBreadcrumb[];
	synonyms?: string[];
};

export type DiscoveryFilters = {
	description?: boolean;
	objects?: SearchObjectType[];
};

export type DiscoverySearchRequest = {
	search_term: string;
	text_match_option?: string;
	filters?: DiscoveryFilters;
};
