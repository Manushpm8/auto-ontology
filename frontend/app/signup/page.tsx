// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { redirect } from 'next/navigation';
import { isSsoConfigured } from '@/lib/auth';
import { SignupForm } from './SignupForm';

/**
 * Open self-registration is available whenever SSO is not configured, no matter
 * how many accounts exist. Once an SSO provider is registered, sign-up closes
 * and users sign in with SSO instead. (The first account created always becomes
 * the admin; later accounts are viewers.)
 */
const SignupPage = async () => {
	if (await isSsoConfigured()) redirect('/login');
	return <SignupForm />;
};

export default SignupPage;
