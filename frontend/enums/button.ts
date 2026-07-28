// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export enum ButtonTheme {
	Primary = 'primary',
	Secondary = 'secondary',
	Outline = 'outline',
	Danger = 'danger',
	DangerOutline = 'danger-outline',
	/** Pale bordered danger button (subtler than DangerOutline's solid hover-invert). */
	DangerSubtle = 'danger-subtle',
	Teal = 'teal',
	Ghost = 'ghost',
	/** Icon-only button with brand-accent hover (green tint). */
	Icon = 'icon',
	/** Icon-only button with a neutral gray hover, for generic utility controls (close, dots, expand/collapse…). */
	IconNeutral = 'icon-neutral',
	/** Icon-only button with a red hover, for destructive row actions. */
	IconDanger = 'icon-danger',
	/** Light bordered/tinted brand-accent button (text stays green, background only tints on hover). */
	Soft = 'soft',
}

export enum Size {
	SMALL = 'small',
	REGULAR = 'regular',
	LARGE = 'large',
}

export enum SelectButtonVariant {
	Avatar = 'avatar',
	Card = 'card',
	ListItem = 'list-item',
	Segmented = 'segmented',
	Swatch = 'swatch',
	Trigger = 'trigger',
}
