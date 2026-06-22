// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requireAdmin } from '@/lib/auth-guards';
import { SsoConfigForm } from './SsoConfigForm';

const AdminSsoPage = async () => {
	await requireAdmin();
	return <SsoConfigForm />;
};

export default AdminSsoPage;
