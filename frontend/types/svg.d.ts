// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

declare module '*.svg' {
	import type { FC, SVGProps } from 'react';
	const component: FC<SVGProps<SVGSVGElement>>;
	export default component;
}
