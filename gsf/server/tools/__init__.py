# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synchronous tool endpoints for external agent harnesses (e.g. NVIDIA AI-Q).

Unlike the SSE chat route, these endpoints answer in a single blocking
request/response so they're trivial to consume as a registered tool/function
from an orchestrating agent framework.
"""
